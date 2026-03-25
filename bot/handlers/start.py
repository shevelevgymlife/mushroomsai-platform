from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user else "друг"

    await update.message.reply_text(
        f"Привет, {name}! 👋\n\nДобро пожаловать в MushroomsAI — умный помощник грибовода.",
        parse_mode="HTML",
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "Открыть приложение",
                web_app=WebAppInfo(url="https://mushroomsai.onrender.com"),
            )
        ],
        [InlineKeyboardButton("Помощь", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )
