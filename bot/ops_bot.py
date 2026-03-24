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
from bot.handlers.task_intake import (
    task_give_entry,
    task_text_received,
    task_photo_choice,
    task_photo_received,
    task_cancel,
    ASK_TASK_TEXT,
    ASK_PHOTO_CHOICE,
    WAIT_PHOTO,
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

    app.add_handler(CommandHandler("start", task_give_entry))
    app.add_handler(CommandHandler("task", task_give_entry))
    app.add_handler(CommandHandler("approval_status", approval_status_command))

    intake_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^Дать задачу$"), task_give_entry),
            CommandHandler("task", task_give_entry),
        ],
        states={
            ASK_TASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_text_received)],
            ASK_PHOTO_CHOICE: [CallbackQueryHandler(task_photo_choice, pattern=r"^task_photo:(yes|no)$")],
            WAIT_PHOTO: [MessageHandler(filters.PHOTO, task_photo_received)],
        },
        fallbacks=[CommandHandler("cancel", task_cancel)],
    )
    app.add_handler(intake_conv)
    app.add_handler(CallbackQueryHandler(task_approval_callback, pattern=r"^confirm:(yes|no):"))

    logger.info("Ops bot handlers configured")
    return app
