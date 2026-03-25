from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    arg = args[0] if args else ""

    # Deeplink: привязка аккаунта
    if arg.startswith("lt_"):
        token = arg
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да, привязать", callback_data=f"link_confirm:{token}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"link_cancel:{token}"),
        ]])
        await update.message.reply_text(
            "🔗 <b>Привязка аккаунта NeuroFungi AI</b>\n\n"
            "Вы хотите привязать этот Telegram к вашему аккаунту на сайте?\n"
            "После привязки вы сможете входить через Telegram.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    user = update.effective_user
    name = user.first_name if user else "друг"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🍄 Открыть приложение", web_app=WebAppInfo(url="https://mushroomsai.ru"))],
        [InlineKeyboardButton("✉️ Написать в поддержку", callback_data="support")],
    ])

    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        f"Добро пожаловать в <b>NeuroFungi AI</b> — умный помощник грибовода.\n"
        f"Нажмите кнопку ниже, чтобы открыть платформу:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
