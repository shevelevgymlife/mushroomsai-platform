import logging

from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from config import settings
from bot.handlers.start import start
from bot.handlers.link import link_confirm_callback, link_merge_callback
from bot.handlers.support import get_support_conversation

logger = logging.getLogger(__name__)


def create_bot() -> Application:
    application = (
        Application.builder()
        .token(settings.TELEGRAM_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(get_support_conversation())
    application.add_handler(
        CallbackQueryHandler(link_confirm_callback, pattern=r"^link_confirm:")
    )
    application.add_handler(
        CallbackQueryHandler(link_confirm_callback, pattern=r"^link_cancel:")
    )
    application.add_handler(
        CallbackQueryHandler(link_merge_callback, pattern=r"^link_merge_")
    )

    return application


async def setup_bot_menu(application: Application) -> None:
    """Устанавливает кнопку меню бота как WebApp (открывает сайт)."""
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    try:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Вход",
                web_app=WebAppInfo(url=site),
            )
        )
        logger.info("Bot menu button set to WebApp: %s", site)
    except Exception as e:
        logger.warning("Could not set bot menu button: %s", e)
