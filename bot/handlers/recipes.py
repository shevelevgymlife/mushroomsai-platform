from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.handlers.start import ensure_user
from services.subscription_service import check_subscription
from ai.openai_client import chat_with_ai
from config import settings

RECIPE_PROMPT = (
    "Составь краткий рецепт применения функциональных грибов для укрепления иммунитета "
    "и общего оздоровления. Включи: название гриба, дозировку, курс, способ приёма. "
    "Ответь лаконично и профессионально."
)


async def recipes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update.effective_user)
    plan = await check_subscription(user["id"])

    if plan in ("start", "pro"):
        text = (
            "Раздел рецептов и протоколов.\n\n"
            "Напишите, какую задачу хотите решить, и я подберу персональный протокол:\n"
            "— Иммунитет\n"
            "— Энергия и концентрация\n"
            "— Восстановление\n"
            "— Женское здоровье\n"
            "— Другое\n\n"
            "Также доступны PDF-протоколы с подробными дозировками."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Получить базовый рецепт", callback_data="recipe_basic")],
            [InlineKeyboardButton("PDF-протоколы", url=f"{settings.SITE_URL}/shop")],
        ])
    else:
        text = (
            "Раздел рецептов.\n\n"
            "Бесплатно доступен 1 общий рецепт в день.\n\n"
            "По подписке Старт/Про — персональные PDF-рецепты с точными дозировками."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Получить рецепт дня", callback_data="recipe_basic")],
            [InlineKeyboardButton("Подписки", callback_data="show_tariffs")],
        ])

    await update.message.reply_text(text, reply_markup=keyboard)


async def recipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = await ensure_user(query.from_user)
    await query.message.chat.send_action("typing")

    recipe = await chat_with_ai(
        user_message=RECIPE_PROMPT,
        user_id=user["id"],
    )
    await query.message.reply_text(recipe)
