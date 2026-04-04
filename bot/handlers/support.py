"""ConversationHandler для 'Написать в поддержку' в главном боте."""
import logging

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db.database import database
from db.models import feedback, users

logger = logging.getLogger(__name__)

WAITING_SUPPORT_MSG = 1


async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["tg_ai_mode"] = False
    await query.message.reply_text(
        "📝 <b>Написать в поддержку</b>\n\n"
        "Опишите ваш вопрос или проблему — мы ответим вам как можно скорее.\n\n"
        "Или /cancel для отмены.",
        parse_mode="HTML",
    )
    return WAITING_SUPPORT_MSG


async def support_text_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запуск поддержки через кнопку клавиатуры."""
    context.user_data["tg_ai_mode"] = False
    await update.message.reply_text(
        "📝 <b>Написать в поддержку</b>\n\n"
        "Опишите ваш вопрос или проблему — мы ответим вам как можно скорее.\n\n"
        "Или /cancel для отмены.",
        parse_mode="HTML",
    )
    return WAITING_SUPPORT_MSG


async def receive_support_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = update.message.text

    # Найти пользователя в БД по tg_id
    user_row = await database.fetch_one(
        users.select().where(users.c.tg_id == user.id)
    )
    user_id = user_row["id"] if user_row else None

    # Сохранить обращение в feedback
    feedback_id = await database.execute(
        feedback.insert().values(
            user_id=user_id,
            message=text,
            status="new",
        )
    )

    # Уведомить администратора через notify-бот
    from services.tg_notify import notify_new_feedback_with_reply

    parts = [user.first_name or "", user.last_name or ""]
    user_label = " ".join(p for p in parts if p).strip() or user.username or f"tg:{user.id}"

    await notify_new_feedback_with_reply(
        feedback_id=feedback_id,
        text=text,
        user_label=user_label,
        user_tg_id=user.id,
    )

    await update.message.reply_text(
        "✅ <b>Ваше сообщение отправлено в поддержку!</b>\n\n"
        "Мы ответим вам в ближайшее время.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cancel_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


def get_support_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(support_start, pattern=r"^support$"),
            MessageHandler(filters.Regex(r"^🆘 Тех\. поддержка$"), support_text_start),
        ],
        states={
            WAITING_SUPPORT_MSG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_support_msg),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_support)],
        per_user=True,
        per_chat=True,
    )
