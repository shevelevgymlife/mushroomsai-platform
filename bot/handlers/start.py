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
            "🔗 <b>Привязка аккаунта NEUROFUNGI AI</b>\n\n"
            "Вы хотите привязать этот Telegram к вашему аккаунту на сайте?\n"
            "После привязки вы сможете входить через Telegram.\n\n"
            "Если на сайте вы также подключаете <b>Google</b>, действуйте по <b>круговой процедуре</b>: "
            "сначала подтвердите вход в Google в браузере, затем при необходимости снова подтвердите ссылку "
            "здесь в Telegram — так аккаунты Google и Telegram сойдутся в одном профиле.",
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
        f"Добро пожаловать в <b>NEUROFUNGI AI</b> — умный помощник грибовода.\n"
        f"Нажмите кнопку ниже, чтобы открыть платформу:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
