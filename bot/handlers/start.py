from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    arg = args[0] if args else ""

    # Deeplink: привязка аккаунта (lt_ prefix)
    if arg.startswith("lt_"):
        token = arg
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, привязать", callback_data=f"link_confirm:{token}"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"link_cancel:{token}"),
            ]
        ]
        await update.message.reply_text(
            "🔗 <b>Привязка аккаунта MushroomsAI</b>\n\n"
            "Вы хотите привязать этот Telegram к вашему аккаунту на сайте?\n"
            "После привязки вы сможете входить через Telegram.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # Обычный старт
    user = update.effective_user
    name = user.first_name if user else "друг"

    await update.message.reply_text(
        f"Привет, {name}! 👋\n\nДобро пожаловать в <b>MushroomsAI</b> — умный помощник грибовода.",
        parse_mode="HTML",
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🍄 Открыть приложение",
                web_app=WebAppInfo(url="https://mushroomsai.onrender.com"),
            )
        ],
    ]
    await update.message.reply_text(
        "Нажмите кнопку ниже, чтобы открыть платформу:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
