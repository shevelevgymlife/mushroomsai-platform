"""ЮKassa: проверка платежа по API и активация подписки (вебхук + дублирование с Telegram)."""
from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from db.database import database
from db.models import payment_webhook_dedup
from services.payment_plans_catalog import get_effective_plans
from services.payment_provider_settings import get_provider_settings
from services.subscription_service import activate_subscription, gift_subscription
from services.yookassa_bot_offerings import (
    DEFAULT_DURATION_MINUTES,
    get_merged_bot_offerings,
    offering_by_id,
)
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
    Ожидает metadata: user_id, plan (start|pro|maxi) или подарок: gift, giver_id, recipient_id, offering_id.
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
    plan = (meta.get("plan") or "").strip().lower()
    offering_id = (meta.get("offering_id") or meta.get("offeringId") or "").strip().lower()
    if uid_raw is None or uid_raw == "":
        # Платёж из Telegram часто без metadata — активация через successful_payment в боте.
        # Оплата с сайта без user_id в metadata — проверьте create payment; вебхук не сможет выдать тариф.
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

    if offering_id:
        offerings = await get_merged_bot_offerings()
        off = offering_by_id(offerings, offering_id)
        if not off or not off.get("enabled"):
            return False, "bad_offering_metadata"
        eff = str(off.get("effective_plan") or "start").lower()
        if eff not in ("start", "pro", "maxi"):
            return False, "bad_effective_plan"
        try:
            dm = int(off.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            dm = 0
        if dm <= 0:
            dm = DEFAULT_DURATION_MINUTES
        expected = float(off.get("price_rub") or 0)
        if expected <= 0:
            return False, "bad_price_config"
        if abs(paid - expected) > 0.02 and abs(paid - expected) > expected * 0.005:
            logger.warning(
                "yookassa amount mismatch uid=%s offering=%s paid=%s expected=%s",
                uid,
                offering_id,
                paid,
                expected,
            )
            return False, "amount_mismatch"
        ok = await activate_subscription(
            uid,
            eff,
            months=1,
            duration_minutes=dm,
            paid_price_rub=expected,
        )
    else:
        if plan not in ("start", "pro", "maxi"):
            return False, "bad_plan_metadata"

        plans = await get_effective_plans()
        expected = float((plans.get(plan) or {}).get("price") or 0)
        if expected <= 0:
            return False, "bad_price_config"
        if abs(paid - expected) > 0.02 and abs(paid - expected) > expected * 0.005:
            logger.warning("yookassa amount mismatch uid=%s plan=%s paid=%s expected=%s", uid, plan, paid, expected)
            return False, "amount_mismatch"

        ok = await activate_subscription(uid, plan, months=1)
    if not ok:
        return False, "activate_failed"
    await _mark("yookassa", pid)
    return True, "ok"


async def _apply_yookassa_gift_payment(
    payment: dict[str, Any], meta: dict[str, Any], pid: str
) -> tuple[bool, str]:
    """Подарок подписки: metadata gift=1, giver_id, recipient_id, offering_id."""
    try:
        giver_id = int(meta.get("giver_id") or meta.get("gift_giver_id") or 0)
        recipient_id = int(meta.get("recipient_id") or meta.get("gift_recipient_id") or 0)
    except (TypeError, ValueError):
        return False, "bad_gift_ids"
    oid = (meta.get("offering_id") or meta.get("offeringId") or "").strip().lower()
    if giver_id <= 0 or recipient_id <= 0 or not oid:
        return False, "bad_gift_metadata"

    amount_obj = payment.get("amount") or {}
    val = amount_obj.get("value")
    try:
        from decimal import Decimal

        paid = float(Decimal(str(val)))
    except Exception:
        paid = 0.0

    offerings = await get_merged_bot_offerings()
    off = offering_by_id(offerings, oid)
    if not off or not off.get("enabled"):
        return False, "bad_offering_metadata"
    eff = str(off.get("effective_plan") or "start").lower()
    if eff not in ("start", "pro", "maxi"):
        return False, "bad_effective_plan"
    expected = float(off.get("price_rub") or 0)
    if expected <= 0:
        return False, "bad_price_config"
    if abs(paid - expected) > 0.02 and abs(paid - expected) > expected * 0.005:
        logger.warning(
            "yookassa gift amount mismatch giver=%s off=%s paid=%s expected=%s",
            giver_id,
            oid,
            paid,
            expected,
        )
        return False, "amount_mismatch"

    ok, err = await gift_subscription(giver_id, recipient_id, eff)
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

    st = await get_provider_settings("yookassa_bot")
    shop_id, secret_key = resolve_yookassa_shop_credentials(st)
    if not shop_id or not secret_key:
        return True, "ignored_no_credentials"

    payment_id = (obj.get("id") or "").strip()
    if not payment_id:
        return False, "no_id"

    verified = await fetch_yookassa_payment(shop_id, secret_key, payment_id)
    if not verified:
        return False, "verify_failed"

    return await apply_yookassa_payment_succeeded(verified)
