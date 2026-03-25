from telegram.ext import Application, CommandHandler

from config import settings
from bot.handlers.start import start


def create_bot() -> Application:
    application = (
        Application.builder()
        .token(settings.TELEGRAM_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))

    return application
