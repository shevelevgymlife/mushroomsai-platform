"""Системные уведомления от имени технической поддержки NEUROFUNGI: ЛС + Telegram."""
from __future__ import annotations

import html
import logging
from typing import Optional

import sqlalchemy as sa

from config import settings
from db.database import database
from db.models import direct_messages, users

logger = logging.getLogger(__name__)


async def resolve_support_sender_id() -> Optional[int]:
    """
    Аккаунт-отправитель системных сообщений в ЛС.
    TECH_SUPPORT_USER_ID → пользователь с ADMIN_EMAIL → первый admin.
    """
    tid = int(getattr(settings, "TECH_SUPPORT_USER_ID", 0) or 0)
    if tid > 0:
        row = await database.fetch_one(sa.select(users.c.id).where(users.c.id == tid))
        if row:
            return int(row["id"])
    em = (settings.ADMIN_EMAIL or "").strip()
    if em:
        row = await database.fetch_one(sa.select(users.c.id).where(users.c.email == em).limit(1))
        if row:
            return int(row["id"])
    row = await database.fetch_one(
        sa.select(users.c.id).where(users.c.role == "admin").order_by(users.c.id.asc()).limit(1)
    )
    return int(row["id"]) if row else None


async def deliver_system_support_notification(
    *,
    recipient_user_id: int,
    body_plain: str,
    telegram_html: Optional[str] = None,
) -> dict:
    """
    Дублирует в ЛС (от техподдержки) и в Telegram (основной бот пользователя).
    body_plain — текст без префикса; в ЛС добавится шапка «Системные оповещения · NEUROFUNGI AI».
    """
    body = (body_plain or "").strip()
    if not body:
        return {"ok": False, "error": "empty"}

    target = await database.fetch_one(users.select().where(users.c.id == int(recipient_user_id)))
    if not target:
        return {"ok": False, "error": "user not found"}

    notify_uid = int(target.get("primary_user_id") or recipient_user_id)
    sid = await resolve_support_sender_id()
    if not sid:
        logger.warning("system_support_delivery: no support sender id, skipping DM")
    else:
        dm_text = "Системные оповещения · NEUROFUNGI AI\n\n" + body
        try:
            await database.execute(
                direct_messages.insert().values(
                    sender_id=sid,
                    recipient_id=notify_uid,
                    text=dm_text,
                    is_read=False,
                    is_system=True,
                )
            )
        except Exception:
            logger.exception("system_support_delivery: DM insert failed uid=%s", recipient_user_id)

    tg_id = target.get("tg_id") or target.get("linked_tg_id")
    if not tg_id:
        fam = await database.fetch_one(
            users.select()
            .where(users.c.primary_user_id == notify_uid)
            .where(sa.or_(users.c.tg_id.is_not(None), users.c.linked_tg_id.is_not(None)))
            .order_by(users.c.id.asc())
            .limit(1)
        )
        if fam:
            tg_id = fam.get("tg_id") or fam.get("linked_tg_id")

    tg_ok = False
    if tg_id:
        tg_msg = telegram_html
        if not tg_msg:
            esc = html.escape(body)
            tg_msg = (
                "<b>Системные оповещения · NEUROFUNGI AI</b>\n\n"
                + esc.replace("\n", "<br/>")
            )
        try:
            from services.notify_user_stub import notify_user

            # notify_user шлёт через notify_user_telegram с HTML
            await notify_user(int(tg_id), tg_msg)
            tg_ok = True
        except Exception as e:
            logger.warning("system_support_delivery: telegram failed: %s", e)

    return {"ok": True, "telegram_sent": tg_ok, "dm_sent": bool(sid)}
