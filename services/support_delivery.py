"""Доставка сообщений поддержки: in-app (direct_messages) + Telegram при офлайне."""
from __future__ import annotations

import html
from datetime import datetime, timedelta
from typing import Any, Optional

from db.database import database
from db.models import direct_messages, support_message_deliveries, users
from config import settings
from bot.handlers.notify import notify_user

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


def _tg_chat_id(user_row: dict) -> Optional[int]:
    tid = user_row.get("tg_id") or user_row.get("linked_tg_id")
    if tid is None:
        return None
    try:
        return int(tid)
    except (TypeError, ValueError):
        return None


async def deliver_support_message(
    *,
    admin_id: int,
    recipient_user_id: int,
    text: str,
    feedback_id: Optional[int] = None,
) -> dict:
    """
    Всегда кладём системное сообщение в ЛК (direct_messages).
    Если пользователь не онлайн и есть Telegram — дублируем в бот.
    """
    body = (text or "").strip()
    if not body:
        return {"ok": False, "error": "empty"}

    target = await database.fetch_one(users.select().where(users.c.id == recipient_user_id))
    if not target:
        return {"ok": False, "error": "user not found"}

    online = _is_online(target.get("last_seen_at"))
    dm_text = f"💬 Сообщение от поддержки MushroomsAI\n\n{body}"

    await database.execute(
        direct_messages.insert().values(
            sender_id=admin_id,
            recipient_id=recipient_user_id,
            text=dm_text,
            is_read=False,
            is_system=True,
        )
    )

    tg_id = _tg_chat_id(dict(target))
    telegram_attempted = False
    telegram_ok = False
    if settings.TELEGRAM_ENABLED and not online and tg_id:
        telegram_attempted = True
        safe = html.escape(body)
        footer = (
            "\n\n<i>То же сообщение доступно в личном кабинете на сайте "
            "(уведомления / сообщения).</i>"
        )
        telegram_ok = await notify_user(
            tg_id,
            f"💬 <b>Ответ от команды MushroomsAI</b>\n\n{safe}{footer}",
        )

    preview = _preview(body)
    await database.execute(
        support_message_deliveries.insert().values(
            admin_id=admin_id,
            recipient_id=recipient_user_id,
            feedback_id=feedback_id,
            message_preview=preview,
            in_app_delivered=True,
            telegram_attempted=telegram_attempted,
            telegram_ok=telegram_ok,
            user_was_online=online,
        )
    )

    return {
        "ok": True,
        "user_was_online": online,
        "telegram_sent": telegram_ok,
        "telegram_attempted": telegram_attempted,
    }
