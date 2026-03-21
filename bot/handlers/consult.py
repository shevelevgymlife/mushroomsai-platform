from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.handlers.start import ensure_user_or_blocked_reply
from services.subscription_service import check_subscription
from config import settings


async def consult_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    plan = await check_subscription(user["id"])

    if plan in ("start", "pro"):
        text = (
            "Задайте ваш вопрос прямо в этом чате.\n\n"
            "Я проконсультирую вас по подбору функциональных грибов, "
            "дозировкам и протоколам.\n\n"
            "У вас активна подписка — вопросы без ограничений."
        )
    else:
        text = (
            "Задайте ваш вопрос прямо в этом чате.\n\n"
            "Бесплатный лимит: 5 вопросов в день.\n\n"
            "Я подберу для вас оптимальный гриб и расскажу о протоколе применения."
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Записаться на личную консультацию", url=f"{settings.SITE_URL}/dashboard")],
    ])

    await update.message.reply_text(text, reply_markup=keyboard)
