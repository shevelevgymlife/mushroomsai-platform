"""Учёт групп/супергрупп для виджета NeuroFungi AI (каналы обрабатываются в channel_autopost)."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from services.group_ai_widget_delivery import disable_group_ai_widget
from services.group_ai_widget_service import upsert_chat_discovered

logger = logging.getLogger(__name__)


async def on_my_chat_member_group_widget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    res = update.my_chat_member
    if not res or not res.chat:
        return
    chat = res.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    new = res.new_chat_member
    if new.user.id != context.bot.id:
        return

    if new.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        try:
            await disable_group_ai_widget(int(chat.id))
        except Exception as e:
            logger.debug("disable_group_ai_widget on leave chat=%s: %s", chat.id, e)
        return

    ctype = "supergroup" if chat.type == ChatType.SUPERGROUP else "group"
    try:
        await upsert_chat_discovered(int(chat.id), ctype, chat.title)
    except Exception as e:
        logger.warning("upsert_chat_discovered group widget chat=%s: %s", chat.id, e)
