from telegram import Update
from telegram.ext import ContextTypes
from db.database import database
from db.models import users
from ai.openai_client import chat_with_ai
from services.subscription_service import can_ask_question, increment_question_count
from bot.handlers.start import ensure_user

LIMIT_TEXT = (
    "Вы исчерпали дневной лимит бесплатных вопросов (5 в день).\n\n"
    "Для безлимитных консультаций подключите подписку:\n"
    "Старт — 990 руб/мес\n"
    "Про — 1990 руб/мес\n\n"
    "Напишите /tariffs для подробностей."
)

MENU_COMMANDS = {
    "консультация", "рецепты", "магазин", "о грибах",
    "тарифы и подписки", "referral", "язык"
}


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Skip menu commands
    if text.lower() in MENU_COMMANDS:
        return

    tg_user = update.effective_user
    user = await ensure_user(tg_user)

    allowed = await can_ask_question(user["id"])
    if not allowed:
        await update.message.reply_text(LIMIT_TEXT)
        return

    await update.message.chat.send_action("typing")

    try:
        answer = await chat_with_ai(
            user_message=text,
            user_id=user["id"],
        )
        await increment_question_count(user["id"])
        await update.message.reply_text(answer)

        # Schedule follow-up
        from datetime import datetime, timedelta
        from db.models import followups
        scheduled = datetime.utcnow() + timedelta(days=3)
        followup_msg = (
            f"{tg_user.first_name}, как вы себя чувствуете после нашей консультации?\n\n"
            "Есть ли изменения? Готов ответить на новые вопросы."
        )
        existing = await database.fetch_all(
            followups.select()
            .where(followups.c.user_id == user["id"])
            .where(followups.c.sent == False)
        )
        if not existing:
            await database.execute(
                followups.insert().values(
                    user_id=user["id"],
                    scheduled_at=scheduled,
                    message=followup_msg,
                )
            )
    except Exception as e:
        await update.message.reply_text(
            "Произошла ошибка при обработке запроса. Пожалуйста, попробуйте позже."
        )
