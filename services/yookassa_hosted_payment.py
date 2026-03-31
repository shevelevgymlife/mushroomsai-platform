"""
Создание платежа ЮKassa с перенаправлением на страницу оплаты (сайт и Telegram WebApp).
Используются shopId и secret из настроек payment_provider:yookassa_bot.
"""
from __future__ import annotations

import base64
import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx

logger = logging.getLogger(__name__)

YOOKASSA_API = "https://api.yookassa.ru/v3/payments"


async def create_yookassa_redirect_payment(
    *,
    shop_id: str,
    secret_key: str,
    amount_rub: float,
    description: str,
    return_url: str,
    metadata: dict[str, str],
) -> tuple[str | None, str | None]:
    """
    Создаёт платёж с confirmation.type=redirect.
    Возвращает (confirmation_url, error_message).
    """
    sid = (shop_id or "").strip()
    sec = (secret_key or "").strip()
    if not sid or not sec:
        return None, "no_shop_credentials"

    try:
        val = Decimal(str(amount_rub)).quantize(Decimal("0.01"))
        if val <= 0:
            return None, "bad_amount"
        value_str = format(val, "f")
    except Exception:
        return None, "bad_amount"

    auth = base64.b64encode(f"{sid}:{sec}".encode()).decode("ascii")
    idempotence_key = str(uuid.uuid4())
    body: dict[str, Any] = {
        "amount": {"value": value_str, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url[:2048]},
        "capture": True,
        "description": (description or "Подписка")[:128],
        "metadata": {str(k)[:50]: str(v)[:512] for k, v in metadata.items()},
    }

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                YOOKASSA_API,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Idempotence-Key": idempotence_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except Exception as e:
        logger.exception("yookassa create payment request failed: %s", e)
        return None, "request_failed"

    if r.status_code not in (200, 201):
        logger.warning("yookassa create payment HTTP %s: %s", r.status_code, r.text[:800])
        return None, f"http_{r.status_code}"

    try:
        data = r.json()
    except Exception:
        return None, "bad_json"

    conf = data.get("confirmation") or {}
    url = (conf.get("confirmation_url") or "").strip()
    if not url:
        logger.warning("yookassa create payment no confirmation_url: %s", data)
        return None, "no_confirmation_url"

    return url, None
