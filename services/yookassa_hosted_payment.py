"""
Создание платежа ЮKassa с перенаправлением на страницу оплаты (сайт и Telegram WebApp).
Используются shopId и secret из настроек payment_provider:yookassa_bot или override из Environment.
"""
from __future__ import annotations

import base64
import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

YOOKASSA_API = "https://api.yookassa.ru/v3/payments"


def _yookassa_error_description(err_body: Any) -> str:
    """Собирает текст ошибки из ответа API (в т.ч. вложенные поля)."""
    if not isinstance(err_body, dict):
        return ""
    parts: list[str] = []
    d = err_body.get("description") or err_body.get("message") or ""
    if isinstance(d, str) and d.strip():
        parts.append(d.strip())
    for key in ("parameter", "code"):
        v = err_body.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    nested = err_body.get("errors")
    if isinstance(nested, list):
        for it in nested:
            if isinstance(it, dict):
                for k in ("description", "message", "code"):
                    s = it.get(k)
                    if isinstance(s, str) and s.strip():
                        parts.append(s.strip())
    return " ".join(parts)[:800]


def _is_receipt_related_error(desc: str) -> bool:
    """Ошибка чека 54-ФЗ (EN/RU), как в ответе ЮKassa."""
    low = (desc or "").lower()
    if "receipt" in low or "чек" in low or "54" in low or "фиск" in low:
        return True
    return False


async def create_yookassa_redirect_payment(
    *,
    shop_id: str,
    secret_key: str,
    amount_rub: float,
    description: str,
    return_url: str,
    metadata: dict[str, str],
    customer_email: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Создаёт платёж с confirmation.type=redirect.
    Возвращает (confirmation_url, error_message, payment_id).
    """
    sid = (shop_id or "").strip()
    sec = (secret_key or "").strip()
    if not sid or not sec:
        return None, "no_shop_credentials", None

    try:
        val = Decimal(str(amount_rub)).quantize(Decimal("0.01"))
        if val <= 0:
            return None, "bad_amount", None
        value_str = format(val, "f")
    except Exception:
        return None, "bad_amount", None

    auth = base64.b64encode(f"{sid}:{sec}".encode()).decode("ascii")
    idempotence_key = str(uuid.uuid4())
    body: dict[str, Any] = {
        "amount": {"value": value_str, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url[:2048]},
        "capture": True,
        "description": (description or "Подписка")[:128],
        "metadata": {str(k)[:50]: str(v)[:512] for k, v in metadata.items()},
    }

    vat = int(getattr(settings, "YOOKASSA_RECEIPT_VAT_CODE", 0) or 0)
    if vat not in (1, 2, 3, 4, 5, 6):
        if vat:
            logger.warning("yookassa: unsupported vat_code=%s, receipt disabled for web payment", vat)
        vat = 0
    em = (customer_email or "").strip()
    if vat > 0 and em and "@" in em:
        # Как в bot/handlers/yookassa_subscribe.py: без payment_mode/payment_subject ЮKassa
        # часто отвечает 400 «Receipt is missing or illegal».
        body["receipt"] = {
            "customer": {"email": em[:256]},
            "items": [
                {
                    "description": (description or "Подписка")[:128],
                    "quantity": "1.00",
                    "amount": {"value": value_str, "currency": "RUB"},
                    "vat_code": vat,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }
            ],
        }
    elif vat > 0 and not em:
        logger.warning(
            "yookassa: YOOKASSA_RECEIPT_VAT_CODE=%s but user has no email — чек не отправлен в create payment",
            vat,
        )

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
        return None, "request_failed", None

    if r.status_code not in (200, 201):
        err_tag = f"http_{r.status_code}"
        try:
            err_body = r.json()
            if isinstance(err_body, dict):
                code = err_body.get("code") or err_body.get("type") or ""
                desc = _yookassa_error_description(err_body) or (
                    (err_body.get("description") or err_body.get("message") or "")[:400]
                )
                desc_l = (desc or "").lower()
                # Та же формулировка EN/RU, что в кабинете ЮKassa; «отсутствует» только в связке с чеком.
                receipt_illegal = (
                    "receipt is missing or illegal" in desc_l
                    or ("illegal" in desc_l and "receipt" in desc_l)
                    or ("некоррект" in desc_l and ("чек" in desc_l or "receipt" in desc_l))
                    or ("чек" in desc_l and "отсутствует" in desc_l and "некоррект" in desc_l)
                )
                # Некоторые аккаунты ЮKassa отклоняют чек в API — повтор без receipt.
                if r.status_code == 400 and "receipt" in body and receipt_illegal and _is_receipt_related_error(
                    desc
                ):
                    try:
                        retry_body = dict(body)
                        retry_body.pop("receipt", None)
                        async with httpx.AsyncClient(timeout=45.0) as client:
                            rr = await client.post(
                                YOOKASSA_API,
                                headers={
                                    "Authorization": f"Basic {auth}",
                                    "Idempotence-Key": str(uuid.uuid4()),
                                    "Content-Type": "application/json",
                                },
                                json=retry_body,
                            )
                        if rr.status_code in (200, 201):
                            data2 = rr.json()
                            pay_id2 = (data2.get("id") or "").strip() or None
                            conf2 = data2.get("confirmation") or {}
                            url2 = (conf2.get("confirmation_url") or "").strip()
                            if url2:
                                logger.warning("yookassa create payment succeeded after retry without receipt")
                                return url2, None, pay_id2
                    except Exception:
                        logger.exception("yookassa receipt retry failed")
                err_tag = f"{err_tag}:{code}:{desc}" if (code or desc) else err_tag
                logger.warning(
                    "yookassa create payment HTTP %s code=%s desc=%s full=%s",
                    r.status_code,
                    code,
                    desc,
                    err_body,
                )
            else:
                logger.warning("yookassa create payment HTTP %s: %s", r.status_code, r.text[:800])
        except Exception:
            logger.warning("yookassa create payment HTTP %s: %s", r.status_code, r.text[:800])
        return None, err_tag, None

    try:
        data = r.json()
    except Exception:
        return None, "bad_json", None

    pay_id = (data.get("id") or "").strip() or None
    conf = data.get("confirmation") or {}
    url = (conf.get("confirmation_url") or "").strip()
    if not url:
        logger.warning("yookassa create payment no confirmation_url: %s", data)
        return None, "no_confirmation_url", pay_id

    return url, None, pay_id
