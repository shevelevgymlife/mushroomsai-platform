"""Кнопка «Обновить бот»: актуальная reply-клавиатура и синхронизация закрытых TG."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import settings

logger = logging.getLogger(__name__)


async def execute_bot_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.handlers.channel_autopost import main_keyboard_with_autopost
    from bot.handlers.start import ensure_user_or_blocked_reply, sync_closed_telegram_after_bot_identity

    user = await ensure_user_or_blocked_reply(update)
    if not user or not update.message:
        return

    context.user_data["tg_ai_mode"] = False
    context.user_data["tg_ai_offline_hint_shown"] = False
    context.user_data.pop("channel_link_awaiting", None)
    context.user_data.pop("channel_link_need_forward", None)

    if context.user_data.get("cp_post_wizard"):
        try:
            from bot.handlers.community_post_wizard import _clear_draft

            _clear_draft(context)
        except Exception:
            logger.debug("clear cp draft on refresh failed", exc_info=True)

    await sync_closed_telegram_after_bot_identity(user)
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    uid = int(user.get("primary_user_id") or user["id"])
    kb = await main_keyboard_with_autopost(site, False, uid)

    await update.message.reply_html(
        "🔄 <b>Бот обновлён.</b> Клавиатура и доступы приведены к актуальному состоянию.",
        reply_markup=kb,
    )
