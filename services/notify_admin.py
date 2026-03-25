"""Уведомление администратора в Telegram."""
from __future__ import annotations

from services.tg_notify import tg_send


async def notify_admin_telegram(text: str) -> None:
    await tg_send(text)
