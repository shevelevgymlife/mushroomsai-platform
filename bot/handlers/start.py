from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes

from config import settings


def _is_admin(user_id: int) -> bool:
    return user_id == int(settings.ADMIN_TG_ID or 0)


def _admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Статус", callback_data="admin:status"),
            InlineKeyboardButton("👤 Пользователи", callback_data="admin:users"),
        ],
        [
            InlineKeyboardButton("🍄 Открыть приложение", web_app=WebAppInfo(url="https://mushroomsai.ru")),
        ],
    ])


def _user_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🍄 Открыть приложение", web_app=WebAppInfo(url="https://mushroomsai.ru")),
        ],
    ])


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
            "🔗 <b>Привязка аккаунта MushroomsAI</b>\n\n"
            "Вы хотите привязать этот Telegram к вашему аккаунту на сайте?\n"
            "После привязки вы сможете входить через Telegram.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    user = update.effective_user
    name = user.first_name if user else "друг"
    is_admin = _is_admin(user.id)

    if is_admin:
        await update.message.reply_text(
            f"Привет, {name}! 👋\n\n"
            f"<b>MushroomsAI — панель администратора</b>\n"
            f"Выберите действие:",
            parse_mode="HTML",
            reply_markup=_admin_keyboard(),
        )
    else:
        await update.message.reply_text(
            f"Привет, {name}! 👋\n\n"
            f"Добро пожаловать в <b>MushroomsAI</b> — умный помощник грибовода.\n"
            f"Нажмите кнопку ниже, чтобы открыть платформу:",
            parse_mode="HTML",
            reply_markup=_user_keyboard(),
        )
