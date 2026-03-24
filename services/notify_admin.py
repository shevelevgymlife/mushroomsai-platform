"""Уведомление администратора в Telegram (если заданы токен и ADMIN_TG_ID)."""
from __future__ import annotations

import httpx

from config import settings


async def notify_admin_telegram(text: str) -> None:
    if not settings.TELEGRAM_ENABLED:
        return
    if not text or not settings.TELEGRAM_TOKEN or not settings.ADMIN_TG_ID:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": settings.ADMIN_TG_ID, "text": text[:3900]},
            )
    except Exception:
        pass
