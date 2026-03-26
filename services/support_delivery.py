"""Доставка сообщений поддержки: in-app (direct_messages) + Telegram."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from config import settings
from db.database import database
from db.models import direct_messages, support_message_deliveries, users

logger = logging.getLogger(__name__)

ONLINE_THRESHOLD_MINUTES = 10


def _preview(text: str, max_len: int = 400) -> str:
    t = (text or "").strip()
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


def _is_online(last_seen_at: Any) -> bool:
    if not last_seen_at:
        return False
    try:
        return last_seen_at > datetime.utcnow() - timedelta(minutes=ONLINE_THRESHOLD_MINUTES)
    except TypeError:
        return False


async def _send_telegram(tg_id: int, text: str) -> bool:
    token = (settings.TELEGRAM_TOKEN or "").strip()
    if not token or not tg_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": tg_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            if r.status_code != 200:
                logger.warning("support_delivery tg send failed: %s %s", r.status_code, r.text[:200])
                return False
        return True
    except Exception as e:
        logger.warning("support_delivery tg send exception: %s", e)
        return False


async def deliver_support_message(
    *,
    admin_id: int,
    recipient_user_id: int,
    text: str,
    feedback_id: Optional[int] = None,
) -> dict:
    """Кладём сообщение в ЛК и отправляем в Telegram."""
    body = (text or "").strip()
    if not body:
        return {"ok": False, "error": "empty"}

    target = await database.fetch_one(users.select().where(users.c.id == recipient_user_id))
    if not target:
        return {"ok": False, "error": "user not found"}

    online = _is_online(target.get("last_seen_at"))
    dm_text = f"💬 Сообщение от поддержки NEUROFUNGI AI\n\n{body}"

    # Сохраняем в ЛК (direct_messages)
    await database.execute(
        direct_messages.insert().values(
            sender_id=admin_id,
            recipient_id=recipient_user_id,
            text=dm_text,
            is_read=False,
            is_system=True,
        )
    )

    # Отправляем в Telegram если есть tg_id
    tg_id = target.get("tg_id") or target.get("linked_tg_id")
    tg_attempted = bool(tg_id)
    tg_ok = False
    if tg_id:
        tg_msg = f"💬 <b>Ответ поддержки NEUROFUNGI AI:</b>\n\n{body}"
        tg_ok = await _send_telegram(int(tg_id), tg_msg)

    preview = _preview(body)
    await database.execute(
        support_message_deliveries.insert().values(
            admin_id=admin_id,
            recipient_id=recipient_user_id,
            feedback_id=feedback_id,
            message_preview=preview,
            in_app_delivered=True,
            telegram_attempted=tg_attempted,
            telegram_ok=tg_ok,
            user_was_online=online,
        )
    )

    return {
        "ok": True,
        "user_was_online": online,
        "telegram_sent": tg_ok,
        "telegram_attempted": tg_attempted,
    }
