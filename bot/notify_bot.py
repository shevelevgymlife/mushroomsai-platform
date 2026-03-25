"""Бот уведомлений администратора — запускается на NOTIFY_BOT_TOKEN."""
import logging

from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from config import settings
from bot.handlers.admin import cmd_status, cmd_users, admin_callback
from bot.handlers.support_admin import get_reply_conversation

logger = logging.getLogger(__name__)


def create_notify_bot() -> Application:
    application = (
        Application.builder()
        .token(settings.NOTIFY_BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", _notify_start))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("users", cmd_users))
    application.add_handler(get_reply_conversation())
    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin:"))

    return application


async def _notify_start(update, context):
    from bot.handlers.admin import _is_admin, _admin_keyboard
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Доступ только для администратора.")
        return
    await update.message.reply_text(
        "👋 <b>NEUROFUNGI AI — уведомления</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=_admin_keyboard(),
    )
