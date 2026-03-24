import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import settings
from bot.handlers.task_approval import approval_status_command, task_approval_callback
from bot.handlers.task_intake import get_task_intake_conversation

logger = logging.getLogger(__name__)


def _ops_token() -> str:
    return (
        (getattr(settings, "TASK_APPROVAL_BOT_TOKEN", "") or "").strip()
        or (settings.DEPLOY_NOTIFY_TG_BOT_TOKEN or "").strip()
    )


def create_ops_bot() -> Application:
    token = _ops_token()
    if not token:
        raise RuntimeError("Ops bot token is not configured")
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Для постановки задачи отправьте /task или нажмите «Дать задачу».")))
    app.add_handler(CommandHandler("approval_status", approval_status_command))
    app.add_handler(get_task_intake_conversation())
    app.add_handler(CallbackQueryHandler(task_approval_callback, pattern=r"^confirm:(yes|no):"))

    logger.info("Ops bot handlers configured")
    return app
