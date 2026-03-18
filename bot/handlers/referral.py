from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.handlers.start import ensure_user
from services.referral_service import get_referral_stats
from config import settings


async def referral_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update.effective_user)
    stats = await get_referral_stats(user["id"])

    ref_link = f"https://t.me/{context.bot.username}?start={user['referral_code']}"

    text = (
        f"Реферальная программа\n\n"
        f"Ваша ссылка:\n{ref_link}\n\n"
        f"Приглашено друзей: {stats['total']}\n"
        f"Активировано бонусов: {stats['bonus_applied']}\n\n"
        "Условия:\n"
        "— Друг переходит по вашей ссылке и покупает подписку\n"
        "— Вы оба получаете скидку 50% на следующий месяц"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Посмотреть статистику", url=f"{settings.SITE_URL}/dashboard")],
    ])

    await update.message.reply_text(text, reply_markup=keyboard)
