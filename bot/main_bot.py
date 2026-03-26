import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters,
)

from config import settings
from bot.handlers.start import start
from bot.handlers.link import link_confirm_callback, link_merge_callback
from bot.handlers.support import get_support_conversation
from bot.handlers.chat import handle_chat_message

logger = logging.getLogger(__name__)

SECURITY_URL = "https://t.me/VPN_POLETELI_bot?start=742166400"

# Тексты кнопок клавиатуры
BTN_SHOP = "🛍 Маркет плейс"
BTN_COMMUNITY = "🌐 Сообщество"
BTN_WEB = "🌍 Веб версия"
BTN_SECURITY = "🔒 Безопасность"
BTN_SUPPORT = "🆘 Тех. поддержка"


async def _shop_handler(update, context):
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    await update.message.reply_text(
        "🛍 <b>Маркет плейс NEUROFUNGI</b>\n\n"
        "Доступно только внутри приложения после регистрации и подписки <b>Старт</b>.\n\n"
        "В маркет плейсе у каждого товара есть описание, комментарии, отзывы и рейтинг.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🍄 Открыть приложение по подписке Старт", web_app=WebAppInfo(url=site))],
        ]),
        parse_mode="HTML",
    )


async def _community_handler(update, context):
    site = (settings.SITE_URL or "").rstrip("/")
    await update.message.reply_text(
        "🌐 <b>Сообщество NEUROFUNGI AI</b>\n\nОткрыть приложение:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🍄 Открыть сообщество",
                web_app=WebAppInfo(url=site + "/app"),
            )],
        ]),
        parse_mode="HTML",
    )


async def _web_handler(update, context):
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    await update.message.reply_text(
        "🌍 <b>Веб версия NEUROFUNGI AI</b>\n\nОткрыть сайт в браузере:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🌍 Открыть сайт", url=site)],
        ]),
        parse_mode="HTML",
    )


async def _security_handler(update, context):
    await update.message.reply_text(
        "🔒 <b>Безопасность</b>\n\nЗащитите свои данные и соединение:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 Открыть", url=SECURITY_URL)],
        ]),
        parse_mode="HTML",
    )


def create_bot() -> Application:
    application = (
        Application.builder()
        .token(settings.TELEGRAM_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(get_support_conversation())

    # Кнопки клавиатуры
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_SHOP}$"), _shop_handler))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_COMMUNITY}$"), _community_handler))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_WEB}$"), _web_handler))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_SECURITY}$"), _security_handler))

    # Callback кнопки
    application.add_handler(CallbackQueryHandler(link_confirm_callback, pattern=r"^link_confirm:"))
    application.add_handler(CallbackQueryHandler(link_confirm_callback, pattern=r"^link_cancel:"))
    application.add_handler(CallbackQueryHandler(link_merge_callback, pattern=r"^link_merge_ok:"))

    # AI чат — все остальные текстовые сообщения
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_chat_message,
    ))

    return application


async def setup_bot_menu(application: Application) -> None:
    """Устанавливает кнопку меню бота как WebApp (открывает сайт)."""
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    try:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Вход",
                web_app=WebAppInfo(url=site + "/app"),
            )
        )
        logger.info("Bot menu button set to WebApp: %s", site)
    except Exception as e:
        logger.warning("Could not set bot menu button: %s", e)
