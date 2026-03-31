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
        "subtitle": "Банковские карты, Apple Pay, Google Pay, рекуррентные платежи",
    },
    {
        "id": "tinkoff",
        "title": "Тинькофф Касса",
        "subtitle": "Интернет-эквайринг Тинькофф (подключение и реквизиты терминала)",
    },
    {
        "id": "yookassa",
        "title": "ЮKassa (сайт)",
        "subtitle": "Оплата на сайте через YooKassa",
    },
    {
        "id": "yookassa_bot",
        "title": "ЮKassa Бот",
        "subtitle": "Оплата в Telegram, вебхук ЮKassa, автосброс подписки и ЛС при окончании",
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
