"""Кнопка «Оплачено» по заявке на вывод реф. баланса в notify-боте."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.admin import _is_admin

logger = logging.getLogger(__name__)


async def referral_withdraw_paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    if not data.startswith("refwd_paid:"):
        return

    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return

    try:
        wid = int(data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer("Некорректные данные.", show_alert=True)
        return

    from services.referral_service import admin_mark_referral_withdrawal_paid

    ok, msg = await admin_mark_referral_withdrawal_paid(wid)
    if ok:
        await query.answer("Готово: резерв снят, пользователь уведомлён.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("edit_message_reply_markup after refwd_paid", exc_info=True)
        try:
            await query.message.reply_text(
                f"✅ Заявка <code>#{wid}</code> отмечена оплаченной (резерв снят, баланс не списывался повторно).",
                parse_mode="HTML",
            )
        except Exception:
            logger.debug("reply after refwd_paid", exc_info=True)
    else:
        await query.answer((msg or "Ошибка")[:180], show_alert=True)
