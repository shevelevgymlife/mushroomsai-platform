from telegram import Update, ForceReply
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CommandHandler
from db.database import database
from db.models import feedback, users
from bot.handlers.start import ensure_user

AWAITING_FEEDBACK = 1


async def feedback_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь нажал 'Написать нам' — просим ввести сообщение."""
    await update.message.reply_text(
        "Напишите ваше сообщение, вопрос или предложение — мы обязательно ответим.\n\n"
        "Для отмены напишите /cancel",
        reply_markup=ForceReply(selective=True),
    )
    return AWAITING_FEEDBACK


async def feedback_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получаем сообщение и сохраняем в БД."""
    tg_user = update.effective_user
    user = await ensure_user(tg_user)
    message_text = update.message.text.strip()

    try:
        await database.execute(
            feedback.insert().values(
                user_id=user["id"],
                message=message_text,
                status="new",
            )
        )
        await update.message.reply_text(
            "Спасибо! Ваше сообщение получено. Мы ответим вам в ближайшее время. 🍄"
        )
    except Exception:
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, попробуйте позже."
        )

    return ConversationHandler.END


async def feedback_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


def get_feedback_conversation():
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Написать нам$"), feedback_start)],
        states={
            AWAITING_FEEDBACK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_receive)
            ],
        },
        fallbacks=[CommandHandler("cancel", feedback_cancel)],
    )
