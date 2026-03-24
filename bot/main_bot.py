import logging

from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from config import settings

logger = logging.getLogger(__name__)
from bot.handlers.start import start_handler
from bot.handlers.chat import message_handler
from bot.handlers.consult import consult_handler
from bot.handlers.recipes import recipes_handler, recipe_callback
from bot.handlers.shop import shop_handler
from bot.handlers.mushrooms import mushrooms_handler, mushroom_deep_callback
from bot.handlers.subscriptions import subscriptions_handler, show_tariffs_callback
from bot.handlers.referral import referral_handler
from bot.handlers.language import show_language_selector, handle_language_callback
from bot.handlers.task_approval import approval_status_command, task_approval_callback
from bot.handlers.task_intake import get_task_intake_conversation
from bot.handlers.lead import lead_start, lead_name, lead_phone, lead_question, lead_cancel, ASK_NAME, ASK_PHONE, ASK_QUESTION
from bot.handlers.feedback_handler import get_feedback_conversation
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes


def _bot_token() -> str:
    return (settings.TELEGRAM_TOKEN or settings.DEPLOY_NOTIFY_TG_BOT_TOKEN or "").strip()


def _mini_app_url() -> str:
    base = (settings.SITE_URL or "https://mushroomsai.ru").strip().rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    return base + "/"


async def _set_menu_webapp_button(application: Application) -> None:
    """Нижняя слева кнопка в Telegram (меню Web App) — подпись и открытие Mini App."""
    try:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="MushroomsAI",
                web_app=WebAppInfo(url=_mini_app_url()),
            ),
        )
        logger.info("Bot menu button set to WebApp MushroomsAI → %s", _mini_app_url())
    except Exception as e:
        logger.warning("set_chat_menu_button failed: %s", e)


async def post_init(application: Application) -> None:
    await _set_menu_webapp_button(application)


async def community_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🍄 Открыть сообщество", url="https://mushroomsai.ru/community")
    ]])
    await update.message.reply_text(
        "Присоединяйтесь к сообществу MushroomsAI!\n\n"
        "Делитесь опытом, читайте посты участников, задавайте вопросы.",
        reply_markup=keyboard,
    )


def create_bot() -> Application:
    token = _bot_token()
    if not token:
        raise RuntimeError("Telegram bot token is not configured")
    app = Application.builder().token(token).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("tariffs", subscriptions_handler))
    app.add_handler(CommandHandler("referral", referral_handler))
    app.add_handler(CommandHandler("language", show_language_selector))
    app.add_handler(CommandHandler("approval_status", approval_status_command))

    # Conversation: lead (consultation request)
    lead_conv = ConversationHandler(
        entry_points=[CommandHandler("consult", lead_start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lead_name)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, lead_phone)],
            ASK_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, lead_question)],
        },
        fallbacks=[CommandHandler("cancel", lead_cancel)],
    )
    app.add_handler(lead_conv)

    # Conversation: task intake from Telegram owner
    app.add_handler(get_task_intake_conversation())

    # Feedback conversation
    app.add_handler(get_feedback_conversation())

    # Menu text handlers
    app.add_handler(MessageHandler(filters.Regex("^Консультация$"), consult_handler))
    app.add_handler(MessageHandler(filters.Regex("^Рецепты$"), recipes_handler))
    app.add_handler(MessageHandler(filters.Regex("^Магазин$"), shop_handler))
    app.add_handler(MessageHandler(filters.Regex("^О грибах$"), mushrooms_handler))
    app.add_handler(MessageHandler(filters.Regex("^Тарифы и подписки$"), subscriptions_handler))
    app.add_handler(MessageHandler(filters.Regex("^Сообщество$"), community_handler))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(recipe_callback, pattern="^recipe_basic$"))
    app.add_handler(CallbackQueryHandler(mushroom_deep_callback, pattern="^mushroom_deep$"))
    app.add_handler(CallbackQueryHandler(show_tariffs_callback, pattern="^show_tariffs$"))
    app.add_handler(CallbackQueryHandler(handle_language_callback, pattern="^lang_"))
    app.add_handler(CallbackQueryHandler(task_approval_callback, pattern=r"^confirm:(yes|no):"))

    # AI chat — all other text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    return app
