"""Обработчик ответа администратора на обращение в поддержку (notify-бот)."""
import logging

import httpx
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import settings
from db.database import database
from db.models import feedback

logger = logging.getLogger(__name__)

WAITING_REPLY_TEXT = 10


def _is_admin(user_id: int) -> bool:
    return user_id == int(settings.ADMIN_TG_ID or 0)


async def reply_fb_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return ConversationHandler.END

    # callback_data: reply_fb:{feedback_id}:{user_tg_id}
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return ConversationHandler.END

    feedback_id = int(parts[1])
    user_tg_id = int(parts[2])

    context.user_data["reply_feedback_id"] = feedback_id
    context.user_data["reply_user_tg_id"] = user_tg_id

    await query.message.reply_text(
        f"✍️ <b>Пишите ответ</b> (обращение #{feedback_id}):\n\n"
        "Или /cancel для отмены.",
        parse_mode="HTML",
    )
    return WAITING_REPLY_TEXT


async def reply_fb_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update.effective_user.id):
        return ConversationHandler.END

    text = update.message.text
    feedback_id = context.user_data.get("reply_feedback_id")
    user_tg_id = context.user_data.get("reply_user_tg_id")

    if not feedback_id or not user_tg_id:
        await update.message.reply_text("Ошибка: сессия истекла. Нажмите «Ответить» снова.")
        return ConversationHandler.END

    # Отправить ответ пользователю через главный бот
    token = settings.TELEGRAM_TOKEN
    sent = False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": user_tg_id,
                    "text": f"💬 <b>Ответ поддержки MushroomsAI:</b>\n\n{text}",
                    "parse_mode": "HTML",
                },
            )
            sent = r.status_code == 200
            if not sent:
                logger.warning("reply_fb_text send failed: %s %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("reply_fb_text exception: %s", e)

    # Обновить статус обращения в БД
    try:
        await database.execute(
            feedback.update().where(feedback.c.id == feedback_id).values(status="replied")
        )
    except Exception as e:
        logger.warning("reply_fb_text db update failed: %s", e)

    if sent:
        await update.message.reply_text(
            f"✅ Ответ отправлен пользователю (обращение #{feedback_id})."
        )
    else:
        await update.message.reply_text(
            f"⚠️ Telegram-отправка не удалась, но статус обновлён (обращение #{feedback_id})."
        )

    return ConversationHandler.END


async def cancel_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


def get_reply_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(reply_fb_start, pattern=r"^reply_fb:")],
        states={
            WAITING_REPLY_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reply_fb_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_reply)],
        per_user=True,
        per_chat=False,
    )
