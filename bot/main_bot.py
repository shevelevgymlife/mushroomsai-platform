from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from config import settings
from bot.handlers.start import start_handler
from bot.handlers.chat import message_handler
from bot.handlers.consult import consult_handler
from bot.handlers.recipes import recipes_handler, recipe_callback
from bot.handlers.shop import shop_handler
from bot.handlers.mushrooms import mushrooms_handler, mushroom_deep_callback
from bot.handlers.subscriptions import subscriptions_handler, show_tariffs_callback
from bot.handlers.referral import referral_handler
from bot.handlers.language import show_language_selector, handle_language_callback
from bot.handlers.lead import lead_start, lead_name, lead_phone, lead_question, lead_cancel, ASK_NAME, ASK_PHONE, ASK_QUESTION
from bot.handlers.feedback_handler import get_feedback_conversation


def create_bot() -> Application:
    app = Application.builder().token(settings.TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("tariffs", subscriptions_handler))
    app.add_handler(CommandHandler("referral", referral_handler))
    app.add_handler(CommandHandler("language", show_language_selector))

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

    # Feedback conversation
    app.add_handler(get_feedback_conversation())

    # Menu text handlers
    app.add_handler(MessageHandler(filters.Regex("^Консультация$"), consult_handler))
    app.add_handler(MessageHandler(filters.Regex("^Рецепты$"), recipes_handler))
    app.add_handler(MessageHandler(filters.Regex("^Магазин$"), shop_handler))
    app.add_handler(MessageHandler(filters.Regex("^О грибах$"), mushrooms_handler))
    app.add_handler(MessageHandler(filters.Regex("^Тарифы и подписки$"), subscriptions_handler))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(recipe_callback, pattern="^recipe_basic$"))
    app.add_handler(CallbackQueryHandler(mushroom_deep_callback, pattern="^mushroom_deep$"))
    app.add_handler(CallbackQueryHandler(show_tariffs_callback, pattern="^show_tariffs$"))
    app.add_handler(CallbackQueryHandler(handle_language_callback, pattern="^lang_"))

    # AI chat — all other text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    return app
