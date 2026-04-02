"""
Создание платежа ЮKassa с перенаправлением на страницу оплаты.
Канал выбирает вызывающий код: payment_provider:yookassa (браузер) или yookassa_bot (Mini App); либо override в Environment.
"""
from __future__ import annotations

import base64
import logging
import re
import uuid
from decimal import Decimal
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

YOOKASSA_API = "https://api.yookassa.ru/v3/payments"

# В официальных примерах ЮKassa для чека онлайн-оплаты: internet как строка "true".
_INTERNET_TRUE = "true"


def _normalize_e164_phone(raw: str | None) -> str | None:
    """E.164 для customer.phone (РФ)."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10:
        return "+7" + digits
    if len(digits) == 11 and digits.startswith("8"):
        return "+7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
    if s.startswith("+") and len(digits) >= 10:
        return "+" + digits
    return None


def _yookassa_error_description(err_body: Any) -> str:
    """Полный текст ошибки для логов (в т.ч. вложенные поля)."""
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


def _yookassa_user_facing_description(err_body: Any) -> str:
    """Короткое описание для URL/query (без склейки parameter=receipt)."""
    if not isinstance(err_body, dict):
        return ""
    d = err_body.get("description") or err_body.get("message") or ""
    if isinstance(d, str) and d.strip():
        return d.strip()[:400]
    return ""


def _is_receipt_related_error(desc: str) -> bool:
    low = (desc or "").lower()
    return bool("receipt" in low or "чек" in low or "54" in low or "фиск" in low)


def _build_receipt(
    *,
    vat: int,
    value_str: str,
    description: str,
    customer_email: str | None,
    customer_phone: str | None,
) -> dict[str, Any] | None:
    em = (customer_email or "").strip()
    cust: dict[str, str] = {}
    if em and "@" in em:
        cust["email"] = em[:256]
    else:
        ph = _normalize_e164_phone(customer_phone)
        if ph:
            cust["phone"] = ph
        else:
            return None
    subj = (getattr(settings, "YOOKASSA_WEB_RECEIPT_PAYMENT_SUBJECT", None) or "service").strip() or "service"
    item: dict[str, Any] = {
        "description": (description or "Подписка")[:128],
        "quantity": 1.0,
        "amount": {"value": value_str, "currency": "RUB"},
        "vat_code": vat,
        "payment_mode": "full_payment",
        "payment_subject": subj[:64],
    }
    receipt: dict[str, Any] = {
        "customer": cust,
        "items": [item],
        "internet": _INTERNET_TRUE,
    }
    ts = int(getattr(settings, "YOOKASSA_RECEIPT_TAX_SYSTEM_CODE", 0) or 0)
    if ts in (1, 2, 3, 4, 5, 6):
        receipt["tax_system_code"] = ts
    return receipt


def _receipt_error_for_retry(desc_l: str) -> bool:
    return (
        "receipt is missing or illegal" in desc_l
        or ("illegal" in desc_l and "receipt" in desc_l)
        or ("некоррект" in desc_l and ("чек" in desc_l or "receipt" in desc_l))
        or ("чек" in desc_l and "отсутствует" in desc_l and "некоррект" in desc_l)
    )


def _success_from_response(data: dict[str, Any]) -> tuple[str | None, str | None] | None:
    pay_id = (data.get("id") or "").strip() or None
    conf = data.get("confirmation") or {}
    url = (conf.get("confirmation_url") or "").strip()
    if url:
        return url, pay_id
    return None


async def create_yookassa_redirect_payment(
    *,
    shop_id: str,
    secret_key: str,
    amount_rub: float,
    description: str,
    return_url: str,
    metadata: dict[str, str],
    customer_email: str | None = None,
    customer_phone: str | None = None,
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

    vat_cfg = int(getattr(settings, "YOOKASSA_RECEIPT_VAT_CODE", 0) or 0)
    if vat_cfg not in (1, 2, 3, 4, 5, 6):
        if vat_cfg:
            logger.warning("yookassa: unsupported vat_code=%s, treating as 0 for web payment", vat_cfg)
        vat_cfg = 0

    em = (customer_email or "").strip()
    phone = (customer_phone or "").strip() or None

    def base_body() -> dict[str, Any]:
        return {
            "amount": {"value": value_str, "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": return_url[:2048]},
            "capture": True,
            "description": (description or "Подписка")[:128],
            "metadata": {str(k)[:50]: str(v)[:512] for k, v in metadata.items()},
        }

    body = base_body()
    if vat_cfg > 0:
        rec = _build_receipt(
            vat=vat_cfg,
            value_str=value_str,
            description=description,
            customer_email=em or None,
            customer_phone=phone,
        )
        if rec:
            body["receipt"] = rec
        else:
            logger.warning(
                "yookassa: YOOKASSA_RECEIPT_VAT_CODE=%s but no customer email/phone — чек не в create payment",
                vat_cfg,
            )

    async def do_post(b: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=45.0) as client:
            return await client.post(
                YOOKASSA_API,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Idempotence-Key": str(uuid.uuid4()),
                    "Content-Type": "application/json",
                },
                json=b,
            )

    try:
        r = await do_post(body)
    except Exception as e:
        logger.exception("yookassa create payment request failed: %s", e)
        return None, "request_failed", None

    if r.status_code in (200, 201):
        try:
            data = r.json()
        except Exception:
            return None, "bad_json", None
        ok = _success_from_response(data)
        if ok:
            url, pay_id = ok
            return url, None, pay_id
        logger.warning("yookassa create payment no confirmation_url: %s", data)
        return None, "no_confirmation_url", (data.get("id") or "").strip() or None

    # Ошибка: разбор, ретраи
    err_tag = f"http_{r.status_code}"
    user_desc = ""
    try:
        err_body = r.json()
    except Exception:
        err_body = None

    if isinstance(err_body, dict):
        code = err_body.get("code") or err_body.get("type") or ""
        user_desc = _yookassa_user_facing_description(err_body)
        desc = user_desc or _yookassa_error_description(err_body)[:400]
        desc_l = (desc or "").lower()
        full_for_log = _yookassa_error_description(err_body)

        # Ретрай 1: чек был — пробуем без чека.
        if (
            r.status_code == 400
            and "receipt" in body
            and _receipt_error_for_retry(desc_l)
            and _is_receipt_related_error(desc)
        ):
            try:
                retry_body = dict(body)
                retry_body.pop("receipt", None)
                rr = await do_post(retry_body)
                if rr.status_code in (200, 201):
                    jd = rr.json()
                    ok = _success_from_response(jd)
                    if ok:
                        u, pid = ok
                        logger.warning("yookassa create payment succeeded after retry without receipt")
                        return u, None, pid
            except Exception:
                logger.exception("yookassa receipt retry without failed")

        # Ретрай 2: чека не было — пробуем с vat=1 (часто УСН без НДС в API).
        if (
            r.status_code == 400
            and "receipt" not in body
            and _receipt_error_for_retry(desc_l)
            and _is_receipt_related_error(desc)
        ):
            rec2 = _build_receipt(
                vat=1,
                value_str=value_str,
                description=description,
                customer_email=em or None,
                customer_phone=phone,
            )
            if rec2:
                try:
                    b2 = base_body()
                    b2["receipt"] = rec2
                    r2 = await do_post(b2)
                    if r2.status_code in (200, 201):
                        jd = r2.json()
                        ok = _success_from_response(jd)
                        if ok:
                            u, pid = ok
                            logger.warning(
                                "yookassa create payment succeeded after retry with default receipt (vat=1)"
                            )
                            return u, None, pid
                except Exception:
                    logger.exception("yookassa receipt retry with default failed")

        err_tag = f"{err_tag}:{code}:{user_desc}" if (code or user_desc) else err_tag
        logger.warning(
            "yookassa create payment HTTP %s code=%s user_desc=%s full=%s err_body=%s",
            r.status_code,
            code,
            user_desc,
            full_for_log,
            err_body,
        )
    else:
        logger.warning("yookassa create payment HTTP %s: %s", r.status_code, r.text[:800])

    return None, err_tag, None

