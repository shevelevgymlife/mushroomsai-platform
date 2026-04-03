"""Проверка уведомлений CloudPayments и активация подписки."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs

from db.database import database
from db.models import payment_webhook_dedup, users
from services.payment_plans_catalog import get_effective_plans, is_catalog_paid_checkout_plan
from services.payment_provider_settings import get_provider_settings
from services.subscription_service import activate_subscription, gift_subscription

logger = logging.getLogger(__name__)


def verify_content_hmac(body: bytes, content_hmac_header: str | None, api_secret: str) -> bool:
    """Content-HMAC = base64(hmac_sha256(api_secret, body)) — см. документацию CloudPayments."""
    if not api_secret or not content_hmac_header:
        return False
    try:
        digest = hmac.new(api_secret.encode("utf-8"), body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("ascii").strip()
        got = (content_hmac_header or "").strip()
        return hmac.compare_digest(expected, got)
    except Exception:
        return False


def _parse_data_field(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except Exception:
            return {}
    return {}


_SUB_INVOICE_RE = re.compile(r"^sub-(\d+)-([a-z0-9_]+)-", re.IGNORECASE)


def _parse_cloudpayments_webhook_body(body: bytes) -> dict[str, Any]:
    """
    Pay-уведомление приходит либо JSON, либо (часто) application/x-www-form-urlencoded с полями TransactionId, Amount, Status…
    См. интеграции CloudPayments: подпись считается по сырому телу, парсим то же тело.
    """
    if not body:
        return {}
    raw = body[3:] if body.startswith(b"\xef\xbb\xbf") else body
    text: str | None = None
    for enc in ("utf-8", "cp1251"):
        try:
            text = raw.decode(enc).strip()
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return {}
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            logger.warning("cloudpayments webhook: JSON parse failed, len=%s", len(text))
            return {}
    pairs = parse_qs(text, keep_blank_values=True, strict_parsing=False)
    flat: dict[str, Any] = {}
    for k, vals in pairs.items():
        if not vals:
            continue
        flat[str(k)] = vals[0] if len(vals) == 1 else vals
    return flat


def _flatten_cloudpayments_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Pay-уведомление может приходить плоским JSON или с вложенным Model (как в ответах API).
    Поля транзакции тогда лежат внутри Model.
    """
    model = payload.get("Model")
    if isinstance(model, dict):
        merged = {**payload, **model}
        return merged
    return dict(payload)


def _merge_custom_data(flat: dict[str, Any]) -> dict[str, Any]:
    """
    userId/plan из виджета: data → в уведомлении часто Data, JsonData (строка) или metadata (новое имя).
    """
    out: dict[str, Any] = {}
    out.update(_parse_data_field(flat.get("Data")))

    jd = flat.get("JsonData")
    if isinstance(jd, dict):
        out.update(jd)
    elif isinstance(jd, str):
        out.update(_parse_data_field(jd))

    for mk in ("Metadata", "metadata"):
        meta = flat.get(mk)
        if isinstance(meta, dict):
            out.update(meta)
            break

    inv = flat.get("InvoiceId")
    if inv is not None:
        m = _SUB_INVOICE_RE.match(str(inv).strip())
        if m:
            out.setdefault("userId", int(m.group(1)))
            out.setdefault("plan", m.group(2).lower())

    return out


async def _already_processed(provider: str, external_id: str) -> bool:
    row = await database.fetch_one(
        payment_webhook_dedup.select()
        .where(payment_webhook_dedup.c.provider == provider)
        .where(payment_webhook_dedup.c.external_id == external_id[:128])
    )
    return row is not None


async def _mark_processed(provider: str, external_id: str) -> None:
    try:
        await database.execute(
            payment_webhook_dedup.insert().values(provider=provider, external_id=external_id[:128])
        )
    except Exception:
        logger.debug("payment_webhook_dedup insert failed", exc_info=True)


async def handle_cloudpayments_notification(
    body: bytes,
    content_hmac: str | None,
) -> tuple[bool, str]:
    """
    Обрабатывает JSON-уведомление CloudPayments (Pay и аналоги).
    Возвращает (success, message) — success=False → ответ 403/400.
    """
    st = await get_provider_settings("cloudpayments")
    if not st.get("enabled"):
        return False, "cloudpayments_disabled"
    api_secret = (st.get("api_secret") or "").strip()
    if not api_secret:
        return False, "no_api_secret"
    if not verify_content_hmac(body, content_hmac, api_secret):
        return False, "bad_hmac"

    payload = _parse_cloudpayments_webhook_body(body)
    if not payload:
        logger.warning("cloudpayments webhook: empty or unparseable body len=%s", len(body or b""))
        return False, "bad_json"

    flat = _flatten_cloudpayments_payload(payload)
    status = (flat.get("Status") or "").strip()
    if status != "Completed":
        logger.info("cloudpayments webhook: skip status=%s", status or "(empty)")
        return True, f"ignored_status:{status}"

    tx_id = flat.get("TransactionId")
    if tx_id is None:
        logger.warning("cloudpayments webhook: no TransactionId keys=%s", list(flat.keys())[:25])
        return False, "no_transaction_id"
    ext = str(tx_id)

    if await _already_processed("cloudpayments", ext):
        return True, "duplicate"

    data = _merge_custom_data(flat)
    gift_raw = str(data.get("gift") or "").strip().lower()
    if gift_raw in ("1", "true", "yes", "on"):
        giver_raw = data.get("giverId") or data.get("giver_id") or data.get("userId") or flat.get("AccountId")
        recipient_raw = data.get("recipientId") or data.get("recipient_id")
        plan = (data.get("plan") or "").strip().lower()
        try:
            giver_id = int(giver_raw)
            recipient_id = int(recipient_raw)
        except (TypeError, ValueError):
            return False, "bad_gift_users"
        plans = await get_effective_plans()
        if not is_catalog_paid_checkout_plan(plans, plan):
            return False, "bad_plan"
        try:
            amount = float(flat.get("Amount") or flat.get("PaymentAmount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        expected = float((plans.get(plan) or {}).get("price") or 0)
        if expected <= 0:
            return False, "bad_price_config"
        if abs(amount - expected) > 0.02 and abs(amount - expected) > expected * 0.005:
            logger.warning(
                "cloudpayments gift amount mismatch giver=%s plan=%s amount=%s expected=%s",
                giver_id,
                plan,
                amount,
                expected,
            )
            return False, "amount_mismatch"
        giver_row = await database.fetch_one(users.select().where(users.c.id == giver_id))
        if not giver_row:
            return False, "user_not_found"
        ok, err = await gift_subscription(giver_id, recipient_id, plan)
        if not ok:
            return False, f"gift_{err}"
        await _mark_processed("cloudpayments", ext)
        return True, "ok_gift"

    uid = data.get("userId") or data.get("user_id") or flat.get("AccountId")
    plan = (data.get("plan") or "").strip().lower()
    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        logger.warning(
            "cloudpayments bad_user: uid=%r plan=%r invoiceId=%r data_keys=%s",
            uid,
            plan,
            flat.get("InvoiceId"),
            list(data.keys()),
        )
        return False, "bad_user"

    plans = await get_effective_plans()
    if not is_catalog_paid_checkout_plan(plans, plan):
        logger.warning(
            "cloudpayments bad_plan: plan=%r catalog_ok=%s show=%s price=%s",
            plan,
            plan in plans,
            (plans.get(plan) or {}).get("show_in_catalog"),
            (plans.get(plan) or {}).get("price"),
        )
        return False, "bad_plan"

    try:
        amount = float(flat.get("Amount") or flat.get("PaymentAmount") or 0)
    except (TypeError, ValueError):
        amount = 0.0

    expected = float((plans.get(plan) or {}).get("price") or 0)
    if expected <= 0:
        return False, "bad_price_config"
    # допускаем небольшую погрешность float
    if abs(amount - expected) > 0.02 and abs(amount - expected) > expected * 0.005:
        logger.warning(
            "cloudpayments amount mismatch uid=%s plan=%s amount=%s expected=%s",
            uid_int,
            plan,
            amount,
            expected,
        )
        return False, "amount_mismatch"

    urow = await database.fetch_one(users.select().where(users.c.id == uid_int))
    if not urow:
        return False, "user_not_found"

    ok = await activate_subscription(uid_int, plan, months=1)
    if not ok:
        logger.warning("cloudpayments activate_failed uid=%s plan=%s", uid_int, plan)
        return False, "activate_failed"
    logger.info("cloudpayments subscription activated uid=%s plan=%s tx=%s", uid_int, plan, ext)
    await _mark_processed("cloudpayments", ext)
    return True, "ok"
