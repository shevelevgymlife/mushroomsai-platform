"""ЮKassa: проверка платежа по API и активация подписки (вебхук + дублирование с Telegram)."""
from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from db.database import database
from db.models import payment_webhook_dedup
from services.payment_plans_catalog import get_effective_plans, is_catalog_paid_checkout_plan
from services.payment_provider_settings import get_provider_settings
from services.subscription_service import activate_subscription, gift_subscription
from services.yookassa_credentials import resolve_yookassa_shop_credentials

logger = logging.getLogger(__name__)


async def _dedup_exists(provider: str, external_id: str) -> bool:
    row = await database.fetch_one(
        payment_webhook_dedup.select()
        .where(payment_webhook_dedup.c.provider == provider)
        .where(payment_webhook_dedup.c.external_id == external_id[:128])
    )
    return row is not None


async def _mark(provider: str, external_id: str) -> None:
    try:
        await database.execute(
            payment_webhook_dedup.insert().values(provider=provider, external_id=external_id[:128])
        )
    except Exception:
        logger.debug("yookassa dedup insert failed", exc_info=True)


async def fetch_yookassa_payment_with_fallback(payment_id: str) -> dict[str, Any] | None:
    """
    GET /v3/payments/{id}: сначала yookassa_bot, затем payment_provider:yookassa (как при создании платежа).
    Нужно, если платёж создан резервным магазином или вебхук проверяет чужой shop_id.
    """
    pid = (payment_id or "").strip()
    if not pid:
        return None
    st1 = await get_provider_settings("yookassa_bot")
    sid1, sec1 = resolve_yookassa_shop_credentials(st1)
    pay = await fetch_yookassa_payment(sid1, sec1, pid)
    if pay:
        return pay
    st2 = await get_provider_settings("yookassa")
    sid2, sec2 = resolve_yookassa_shop_credentials(st2)
    if sid2 and sec2 and (sid2 != sid1 or sec2 != sec1):
        pay2 = await fetch_yookassa_payment(sid2, sec2, pid)
        if pay2:
            return pay2
    return None


async def fetch_yookassa_payment(shop_id: str, secret_key: str, payment_id: str) -> dict[str, Any] | None:
    auth = base64.b64encode(f"{shop_id}:{secret_key}".encode()).decode("ascii")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"https://api.yookassa.ru/v3/payments/{payment_id}",
                headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            )
        if r.status_code != 200:
            logger.warning("yookassa GET payment %s -> %s", payment_id, r.status_code)
            return None
        return r.json()
    except Exception:
        logger.exception("yookassa fetch payment failed id=%s", payment_id)
        return None


async def apply_yookassa_payment_succeeded(payment: dict[str, Any]) -> tuple[bool, str]:
    """
    Обрабатывает объект payment из API/вебхука (status=succeeded).
    metadata: user_id, plan и/или offering_id (alias slug тарифа из каталога).
    Подарок: gift, giver_id, recipient_id, plan или offering_id.
    """
    pid = (payment.get("id") or "").strip()
    if not pid:
        return False, "no_payment_id"
    if await _dedup_exists("yookassa", pid):
        return True, "duplicate"

    status = (payment.get("status") or "").strip()
    if status != "succeeded":
        return True, f"not_succeeded:{status}"

    meta = payment.get("metadata") or {}
    if isinstance(meta, str):
        try:
            import json

            meta = json.loads(meta)
        except Exception:
            meta = {}

    gift_raw = str(meta.get("gift") or "").strip().lower()
    if gift_raw in ("1", "true", "yes", "on"):
        return await _apply_yookassa_gift_payment(payment, meta, pid)

    uid_raw = meta.get("user_id") or meta.get("userId")
    plan_key = (
        (meta.get("plan") or meta.get("offering_id") or meta.get("offeringId") or "").strip().lower()
    )
    if uid_raw is None or uid_raw == "":
        logger.warning(
            "yookassa payment %s succeeded but metadata has no user_id — activation skipped",
            pid,
        )
        return True, "ignored_no_metadata"
    try:
        uid = int(uid_raw)
    except (TypeError, ValueError):
        return False, "bad_user_in_metadata"

    amount_obj = payment.get("amount") or {}
    val = amount_obj.get("value")
    try:
        from decimal import Decimal

        paid = float(Decimal(str(val)))
    except Exception:
        paid = 0.0

    plans = await get_effective_plans()
    if not plan_key or not is_catalog_paid_checkout_plan(plans, plan_key):
        return False, "bad_plan_metadata"
    pmeta = plans[plan_key]
    expected = float(pmeta.get("price") or 0)
    if expected <= 0:
        return False, "bad_price_config"
    if abs(paid - expected) > 0.02 and abs(paid - expected) > expected * 0.005:
        logger.warning("yookassa amount mismatch uid=%s plan=%s paid=%s expected=%s", uid, plan_key, paid, expected)
        return False, "amount_mismatch"

    ok = await activate_subscription(uid, plan_key, months=1, paid_price_rub=expected)
    if not ok:
        return False, "activate_failed"
    await _mark("yookassa", pid)
    return True, "ok"


async def _apply_yookassa_gift_payment(
    payment: dict[str, Any], meta: dict[str, Any], pid: str
) -> tuple[bool, str]:
    """Подарок подписки: metadata gift=1, giver_id, recipient_id, plan или offering_id."""
    try:
        giver_id = int(meta.get("giver_id") or meta.get("gift_giver_id") or 0)
        recipient_id = int(meta.get("recipient_id") or meta.get("gift_recipient_id") or 0)
    except (TypeError, ValueError):
        return False, "bad_gift_ids"
    plan_key = (
        (meta.get("plan") or meta.get("offering_id") or meta.get("offeringId") or "").strip().lower()
    )
    if giver_id <= 0 or recipient_id <= 0 or not plan_key:
        return False, "bad_gift_metadata"

    amount_obj = payment.get("amount") or {}
    val = amount_obj.get("value")
    try:
        from decimal import Decimal

        paid = float(Decimal(str(val)))
    except Exception:
        paid = 0.0

    plans = await get_effective_plans()
    if not is_catalog_paid_checkout_plan(plans, plan_key):
        return False, "bad_plan_metadata"
    expected = float((plans.get(plan_key) or {}).get("price") or 0)
    if expected <= 0:
        return False, "bad_price_config"
    if abs(paid - expected) > 0.02 and abs(paid - expected) > expected * 0.005:
        logger.warning(
            "yookassa gift amount mismatch giver=%s plan=%s paid=%s expected=%s",
            giver_id,
            plan_key,
            paid,
            expected,
        )
        return False, "amount_mismatch"

    ok, err = await gift_subscription(giver_id, recipient_id, plan_key)
    if not ok:
        logger.warning("yookassa gift_subscription failed: %s", err)
        return False, f"gift_{err}"
    await _mark("yookassa", pid)
    return True, "ok_gift"


async def handle_yookassa_http_notification(body: dict[str, Any]) -> tuple[bool, str]:
    """
    Тело уведомления ЮKassa: { "type": "notification", "event": "payment.succeeded", "object": { ... } }
    """
    event = (body.get("event") or body.get("type") or "").strip()
    obj = body.get("object")
    if not isinstance(obj, dict):
        return True, "ignored_no_object"

    if event != "payment.succeeded":
        return True, f"ignored_event:{event}"

    payment_id = (obj.get("id") or "").strip()
    if not payment_id:
        return False, "no_id"

    verified = await fetch_yookassa_payment_with_fallback(payment_id)
    if not verified:
        return False, "verify_failed"

    return await apply_yookassa_payment_succeeded(verified)
