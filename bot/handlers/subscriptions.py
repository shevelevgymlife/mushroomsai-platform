from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.handlers.start import ensure_user_or_blocked_reply
from services.subscription_service import check_subscription, PLANS
from config import settings

TARIFFS_TEXT = """Тарифы и подписки:

Бесплатный
5 вопросов в день
1 рецепт в день

Старт — 990 руб/мес
Безлимитные консультации
PDF-рецепты с дозировками
История диалогов 1 мес

Про — 1990 руб/мес
Всё из Старт
Приоритетные ответы
История диалогов 3 мес
Раздел "Глубокий разбор"

---

Дополнительные продукты:

Коробка месяца — 4500 руб
3-4 вида грибов + PDF + поддержка в чате

Срочная консультация (30-40 мин) — 4900 руб
Видео или аудио с Евгением Шевелевым

PDF-протоколы — 990-1490 руб:
- Иммунитет
- Энергия и концентрация
- Женское здоровье и гормоны
- Восстановление после болезней

Анализ анализов крови — 1499-1990 руб
Фото анализов → персональный PDF"""


async def subscriptions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    plan = await check_subscription(user["id"])
    plan_name = PLANS.get(plan, {}).get("name", "Бесплатный")

    text = f"Ваш текущий план: {plan_name}\n\n" + TARIFFS_TEXT

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Подключить Старт", url=f"{settings.SITE_URL}/dashboard"),
            InlineKeyboardButton("Подключить Про", url=f"{settings.SITE_URL}/dashboard"),
        ],
        [InlineKeyboardButton("Все продукты на сайте", url=f"{settings.SITE_URL}/shop")],
    ])

    await update.message.reply_text(text, reply_markup=keyboard)


async def show_tariffs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        TARIFFS_TEXT,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Подключить подписку", url=f"{settings.SITE_URL}/dashboard")]
        ]),
    )
