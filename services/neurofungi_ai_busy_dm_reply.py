"""Автоответ NeuroFungi AI в ЛС, когда пишут на аккаунт бота / единого AI."""
from __future__ import annotations

import logging
from typing import Optional, Set

import sqlalchemy as sa

from db.database import database
from db.models import users
from services.system_support_delivery import NEUROFUNGI_AI_DISPLAY_NAME, resolve_neurofungi_ai_user_id

logger = logging.getLogger(__name__)

NEUROFUNGI_BUSY_DM_AUTO_REPLY_RU = (
    "Не пишите мне в личку — я сама выйду к вам на связь. "
    "Сейчас занята глобальной работой в проекте."
)


async def _neurofungi_ai_inbox_user_ids() -> Set[int]:
    ids: set[int] = set()
    try:
        from services.ai_community_bot import load_bot_settings_row

        row = await load_bot_settings_row()
        if row and row.get("user_id"):
            ids.add(int(row["user_id"]))
    except Exception:
        logger.debug("busy_dm_reply: community bot row", exc_info=True)
    n = await resolve_neurofungi_ai_user_id()
    if n:
        ids.add(int(n))
    return {x for x in ids if x > 0}


async def maybe_send_neurofungi_ai_busy_dm_reply(
    *,
    human_sender_id: int,
    bot_recipient_id: int,
) -> Optional[int]:
    """
    Если пользователь пишет в ЛС аккаунту NeuroFungi / боту сообщества — вставить автоответ от этого аккаунта.
    """
    if human_sender_id <= 0 or bot_recipient_id <= 0:
        return None
    if human_sender_id == bot_recipient_id:
        return None
    targets = await _neurofungi_ai_inbox_user_ids()
    if bot_recipient_id not in targets:
        return None
    text = NEUROFUNGI_BUSY_DM_AUTO_REPLY_RU
    try:
        row = await database.fetch_one_write(
            sa.text(
                "INSERT INTO direct_messages (sender_id, recipient_id, text, is_read, is_system) "
                "VALUES (:s, :r, :t, false, false) RETURNING id"
            ).bindparams(s=bot_recipient_id, r=human_sender_id, t=text)
        )
    except Exception:
        logger.exception("busy_dm_reply: insert failed")
        return None
    reply_id = int(row["id"]) if row and row.get("id") is not None else None
    if not reply_id:
        return None
    bot_row = await database.fetch_one(users.select().where(users.c.id == bot_recipient_id))
    nm = (bot_row.get("name") if bot_row else None) or NEUROFUNGI_AI_DISPLAY_NAME
    try:
        from services.in_app_notifications import create_notification

        await create_notification(
            recipient_id=int(human_sender_id),
            actor_id=int(bot_recipient_id),
            ntype="message",
            title="Личное сообщение",
            body=f"{nm}: {text[:400]}",
            link_url=f"/chats?open_user={bot_recipient_id}",
            source_kind="direct_message",
            source_id=int(reply_id),
        )
    except Exception:
        logger.debug("busy_dm_reply: notification failed", exc_info=True)
    try:
        from services.legacy_dm_chat_sync import sync_direct_messages_pair

        await sync_direct_messages_pair(
            int(bot_recipient_id),
            int(human_sender_id),
            broadcast_legacy_dm_id=int(reply_id),
        )
    except Exception:
        logger.debug("busy_dm_reply: sync failed", exc_info=True)
    try:
        from services.in_app_notifications import should_send_telegram_for_event

        human = await database.fetch_one(users.select().where(users.c.id == human_sender_id))
        if human and await should_send_telegram_for_event(int(human_sender_id), "message"):
            tg_id = human.get("tg_id") or human.get("linked_tg_id")
            if tg_id:
                from services.notify_user_stub import notify_user_dm_with_read_button

                await notify_user_dm_with_read_button(
                    tg_id, nm, text, f"/chats?open_user={bot_recipient_id}"
                )
    except Exception:
        logger.debug("busy_dm_reply: telegram failed", exc_info=True)
    return reply_id
