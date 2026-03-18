from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import settings


async def payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment requests — redirect to website for payment."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Оплатить на сайте", url=f"{settings.SITE_URL}/dashboard")],
    ])
    await update.message.reply_text(
        "Оплата подписок и продуктов доступна на сайте.\n\n"
        "Перейдите в личный кабинет для оформления.",
        reply_markup=keyboard,
    )


async def followup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle follow-up reply from user."""
    text = update.message.text
    if not text:
        return

    from bot.handlers.chat import message_handler
    await message_handler(update, context)
