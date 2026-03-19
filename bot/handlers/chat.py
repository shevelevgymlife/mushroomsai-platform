from telegram import Update
from telegram.ext import ContextTypes
from db.database import database
from db.models import users, knowledge_base, shop_products
from ai.openai_client import chat_with_ai
from services.subscription_service import can_ask_question, increment_question_count
from bot.handlers.start import ensure_user

# Telegram IDs with unlimited access
UNLIMITED_USERS = [742166400]
# DB user IDs with unlimited access (add Google-linked account IDs here)
UNLIMITED_USER_IDS: set = set()

LIMIT_TEXT = (
    "Вы исчерпали дневной лимит бесплатных вопросов (5 в день).\n\n"
    "Для безлимитных консультаций подключите подписку:\n"
    "Старт — 990 руб/мес\n"
    "Про — 1990 руб/мес\n\n"
    "Напишите /tariffs для подробностей."
)

MENU_COMMANDS = {
    "консультация", "рецепты", "магазин", "о грибах",
    "тарифы и подписки", "referral", "язык"
}

# Ключевые слова грибов для поиска товаров
MUSHROOM_KEYWORDS = {
    "чага": "чага",
    "chaga": "чага",
    "рейши": "рейши",
    "reishi": "рейши",
    "лев": "лев",
    "ежовик": "лев",
    "lion": "лев",
    "шиитаке": "шиитаке",
    "shiitake": "шиитаке",
    "мухомор": "мухомор",
    "amanita": "мухомор",
    "кордицепс": "кордицепс",
    "cordyceps": "кордицепс",
}


async def search_knowledge(question: str) -> str | None:
    """Ищет релевантные записи в knowledge_base по ключевым словам вопроса."""
    words = [w.strip(".,!?;:()[]").lower() for w in question.split() if len(w) > 3]
    if not words:
        return None

    try:
        rows = await database.fetch_all(knowledge_base.select())
        best_match = None
        best_score = 0

        for row in rows:
            content_lower = (row["content"] or "").lower()
            title_lower = (row["title"] or "").lower()
            score = sum(
                2 if w in title_lower else (1 if w in content_lower else 0)
                for w in words
            )
            if score > best_score:
                best_score = score
                best_match = row

        if best_match and best_score >= 2:
            snippet = (best_match["content"] or "")[:800]
            return f"[База знаний — {best_match['title']}]\n{snippet}"
    except Exception:
        pass

    return None


async def find_mushroom_product(question: str) -> str | None:
    """Ищет товар в shop_products если в вопросе упомянут гриб."""
    question_lower = question.lower()
    mushroom_type = None

    for keyword, mtype in MUSHROOM_KEYWORDS.items():
        if keyword in question_lower:
            mushroom_type = mtype
            break

    if not mushroom_type:
        return None

    try:
        row = await database.fetch_one(
            shop_products.select().where(
                shop_products.c.mushroom_type == mushroom_type
            ).limit(1)
        )
        if row:
            price_text = f"{row['price']} руб." if row["price"] else ""
            url = row["url"] or f"https://mushroomsai.ru/shop"
            return (
                f"\n\n🍄 *{row['name']}*\n"
                f"{row['description'] or ''}\n"
                f"{price_text}\n"
                f"Купить: {url}"
            )
    except Exception:
        pass

    return None


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Skip menu commands
    if text.lower() in MENU_COMMANDS:
        return

    tg_user = update.effective_user
    user = await ensure_user(tg_user)

    is_unlimited = (
        tg_user.id in UNLIMITED_USERS
        or user["id"] in UNLIMITED_USER_IDS
        or user.get("linked_tg_id") in UNLIMITED_USERS
    )
    if not is_unlimited:
        allowed = await can_ask_question(user["id"])
        if not allowed:
            await update.message.reply_text(LIMIT_TEXT)
            return

    await update.message.chat.send_action("typing")

    try:
        # Ищем в базе знаний и добавляем контекст к вопросу
        knowledge_context = await search_knowledge(text)
        enriched_message = text
        if knowledge_context:
            enriched_message = f"{text}\n\n{knowledge_context}"

        answer = await chat_with_ai(
            user_message=enriched_message,
            user_id=user["id"],
        )
        await increment_question_count(user["id"])

        # Добавляем карточку товара если упомянут гриб
        product_card = await find_mushroom_product(text)
        if product_card:
            answer = answer + product_card

        await update.message.reply_text(answer, parse_mode="Markdown")

        # Schedule follow-up
        from datetime import datetime, timedelta
        from db.models import followups
        scheduled = datetime.utcnow() + timedelta(days=3)
        followup_msg = (
            f"{tg_user.first_name}, как вы себя чувствуете после нашей консультации?\n\n"
            "Есть ли изменения? Готов ответить на новые вопросы."
        )
        existing = await database.fetch_all(
            followups.select()
            .where(followups.c.user_id == user["id"])
            .where(followups.c.sent == False)
        )
        if not existing:
            await database.execute(
                followups.insert().values(
                    user_id=user["id"],
                    scheduled_at=scheduled,
                    message=followup_msg,
                )
            )
    except Exception as e:
        await update.message.reply_text(
            "Произошла ошибка при обработке запроса. Пожалуйста, попробуйте позже."
        )
