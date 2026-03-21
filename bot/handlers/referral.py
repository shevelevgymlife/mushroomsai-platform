from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.handlers.start import ensure_user
from services.referral_service import get_referral_stats, referral_bonus_per_invite_rub
from config import settings


async def referral_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update.effective_user)
    stats = await get_referral_stats(user["id"])

    ref_link = f"https://t.me/{context.bot.username}?start={user['referral_code']}"
    site = settings.SITE_URL.rstrip("/")
    ref_site = f"{site}/login?ref={user['referral_code']}"
    bonus = referral_bonus_per_invite_rub()

    text = (
        f"Реферальная программа\n\n"
        f"Telegram:\n{ref_link}\n\n"
        f"Сайт:\n{ref_site}\n\n"
        f"Приглашено: {stats['total']}\n"
        f"Баланс бонусов: {stats['balance_rub']} ₽\n\n"
        f"За каждого друга, который зарегистрируется по вашей ссылке, "
        f"на баланс начисляется {bonus} ₽ (10% от тарифа Старт)."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Открыть кабинет", url=f"{settings.SITE_URL}/dashboard")],
    ])

    await update.message.reply_text(text, reply_markup=keyboard)
