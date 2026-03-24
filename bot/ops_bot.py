import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import settings
from bot.handlers.task_approval import approval_status_command, task_approval_callback
from bot.handlers.task_intake import (
    task_give_entry,
    task_text_received,
    task_photo_choice,
    task_photo_received,
)

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
    app.add_handler(CommandHandler("task", task_give_entry))
    app.add_handler(CommandHandler("approval_status", approval_status_command))
    app.add_handler(MessageHandler(filters.Regex("^Дать задачу$"), task_give_entry))
    app.add_handler(CallbackQueryHandler(task_photo_choice, pattern=r"^task_photo:(yes|no)$"))
    app.add_handler(CallbackQueryHandler(task_approval_callback, pattern=r"^confirm:(yes|no):"))
    app.add_handler(MessageHandler(filters.PHOTO, task_photo_received))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, task_text_received))

    logger.info("Ops bot handlers configured")
    return app
