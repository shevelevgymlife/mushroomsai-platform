from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters
from db.database import database
from db.models import leads
from bot.handlers.start import ensure_user_or_blocked_reply
from config import settings

ASK_NAME, ASK_PHONE, ASK_QUESTION = range(3)


async def lead_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        from telegram.ext import ConversationHandler
        return ConversationHandler.END
    await update.message.reply_text(
        "Запись на консультацию с Евгением Шевелевым.\n\n"
        "Введите ваше имя:"
    )
    return ASK_NAME


async def lead_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["lead_name"] = update.message.text
    await update.message.reply_text("Введите ваш номер телефона:")
    return ASK_PHONE


async def lead_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["lead_phone"] = update.message.text
    await update.message.reply_text("Опишите кратко ваш запрос или вопрос:")
    return ASK_QUESTION


async def lead_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        from telegram.ext import ConversationHandler
        return ConversationHandler.END
    question = update.message.text

    await database.execute(
        leads.insert().values(
            user_id=user["id"],
            name=context.user_data.get("lead_name"),
            phone=context.user_data.get("lead_phone"),
            question=question,
            status="new",
        )
    )

    await update.message.reply_text(
        "Заявка принята. Евгений свяжется с вами в ближайшее время.\n\n"
        "Также вы можете задать вопросы прямо в этом чате."
    )

    # Notify admin
    try:
        admin_text = (
            f"Новая заявка!\n"
            f"Имя: {context.user_data.get('lead_name')}\n"
            f"Телефон: {context.user_data.get('lead_phone')}\n"
            f"Вопрос: {question}\n"
            f"TG: @{update.effective_user.username}"
        )
        await update.get_bot().send_message(chat_id=settings.ADMIN_TG_ID, text=admin_text)
    except Exception:
        pass

    return ConversationHandler.END


async def lead_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END
