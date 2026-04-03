"""
Настройки эквайринга по провайдерам (platform_settings: payment_provider:<id>).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from db.database import database
from db.models import platform_settings

logger = logging.getLogger(__name__)

PAYMENT_PROVIDERS: list[dict[str, str]] = [
    {
        "id": "cloudpayments",
        "title": "CloudPayments",
        "subtitle": "Карты, СБП, SberPay, T-Pay и др. (в виджете — что подключено в личном кабинете CloudPayments)",
    },
    {
        "id": "tinkoff",
        "title": "Тинькофф Касса",
        "subtitle": "Интернет-эквайринг Тинькофф (подключение и реквизиты терминала)",
    },
    {
        "id": "yookassa",
        "title": "ЮKassa (веб-сайт)",
        "subtitle": "Второй магазин ЮKassa: оплата подписки из браузера (не Mini App). Свой shopId, секрет и HTTP-уведомления",
        "admin_path": "/admin/payment/yookassa",
    },
    {
        "id": "yookassa_bot",
        "title": "ЮKassa (бот и Mini App)",
        "subtitle": "Первый магазин: счёт в Telegram, редирект на оплату из Mini App; цены — в «Тарифы подписок»",
        "admin_path": "/admin/payment/yookassa-bot",
    },
    {
        "id": "telegram_stars",
        "title": "Telegram Stars",
        "subtitle": "Оплата звёздами внутри Telegram",
    },
    {
        "id": "crypto",
        "title": "Криптовалюта",
        "subtitle": "NOWPayments или аналог (API-ключ, валюта)",
    },
]

# Идентификаторы для параметра виджета restrictedPaymentMethods (список ОТКЛЮЧАЕМЫХ способов).
# Документация: developers.cloudpayments.ru (виджет / PaymentBlocks).
CLOUDPAYMENTS_WIDGET_METHOD_CHOICES: list[tuple[str, str]] = [
    ("Card", "Банковская карта"),
    ("Sbp", "СБП"),
    ("SberPay", "SberPay"),
    ("TinkoffPay", "T-Pay (Т-Банк)"),
    ("MirPay", "Mir Pay"),
    ("ForeignCard", "Иностранные карты"),
    ("TcsInstallment", "Рассрочка Т-Банк"),
    ("Dolyame", "Долями"),
]

CLOUDPAYMENTS_WIDGET_METHOD_IDS = frozenset(m[0] for m in CLOUDPAYMENTS_WIDGET_METHOD_CHOICES)


def normalize_cloudpayments_restricted_payment_methods(value: Any) -> list[str]:
    """Нормализует список методов, которые нужно скрыть в виджете (restrictedPaymentMethods)."""
    if not value:
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [x.strip() for x in value.replace(",", " ").split() if x.strip()]
    else:
        return []
    out: list[str] = []
    for x in items:
        s = str(x).strip()
        if s in CLOUDPAYMENTS_WIDGET_METHOD_IDS and s not in out:
            out.append(s)
    return out


def _key(provider_id: str) -> str:
    return f"payment_provider:{provider_id.strip().lower()}"


async def get_provider_settings(provider_id: str) -> dict[str, Any]:
    pid = provider_id.strip().lower()
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == _key(pid))
        )
        if not row or not row.get("value"):
            return {}
        return json.loads(row["value"])
    except Exception:
        logger.debug("get_provider_settings failed id=%s", pid, exc_info=True)
        return {}


async def save_provider_settings(provider_id: str, data: dict[str, Any]) -> None:
    pid = provider_id.strip().lower()
    raw = json.dumps(data, ensure_ascii=False)
    exists = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == _key(pid))
    )
    if exists:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == _key(pid))
            .values(value=raw)
        )
    else:
        await database.execute(platform_settings.insert().values(key=_key(pid), value=raw))


def merge_secrets(
    incoming: dict[str, Any],
    previous: dict[str, Any],
    secret_fields: tuple[str, ...],
) -> dict[str, Any]:
    """Пустое поле секрета = оставить previous."""
    out = dict(incoming)
    for f in secret_fields:
        v = out.get(f)
        if isinstance(v, str) and (not v.strip() or v.strip() == "••••"):
            if f in previous:
                out[f] = previous[f]
            else:
                out.pop(f, None)
    return out
