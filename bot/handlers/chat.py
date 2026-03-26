"""AI чат в боте: 5 вопросов в сутки для обычных пользователей, безлимит для админов с can_training_bot."""
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes

from config import settings
from db.database import database
from db.models import users, messages, admin_permissions

logger = logging.getLogger(__name__)

BOT_DAILY_LIMIT = 5


async def _get_user_by_tg_id(tg_id: int):
    return await database.fetch_one(
        users.select().where(
            sa.or_(users.c.tg_id == tg_id, users.c.linked_tg_id == tg_id)
        )
    )


async def _has_unlimited_ai(user_id: int) -> bool:
    """Проверяем can_training_bot в admin_permissions."""
    row = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == user_id)
    )
    if not row:
        return False
    return bool(row.get("can_training_bot"))


async def _count_today_messages(user_id: int) -> int:
    """Считаем сообщения пользователя (role=user) за сегодня UTC."""
    today = datetime.now(timezone.utc).date()
    count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(messages).where(
            messages.c.user_id == user_id,
            messages.c.role == "user",
            sa.func.date(messages.c.created_at) == today,
        )
    )
    return int(count or 0)


async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает любое текстовое сообщение боту как вопрос к AI."""
    tg_user = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        return

    # Ищем пользователя в БД
    user_row = await _get_user_by_tg_id(tg_user.id)

    if user_row:
        user_id = user_row["id"]
        unlimited = await _has_unlimited_ai(user_id)
    else:
        # Незарегистрированный пользователь — тоже 5 вопросов, но без БД истории
        user_id = None
        unlimited = False

    # Проверяем лимит
    if not unlimited and user_id:
        count = await _count_today_messages(user_id)
        if count >= BOT_DAILY_LIMIT:
            await _send_limit_reached(update)
            return
    elif not unlimited and not user_id:
        # Считаем по session_key (tg_id как строка)
        session_key = f"tg_{tg_user.id}"
        today = datetime.now(timezone.utc).date()
        count = await database.fetch_val(
            sa.select(sa.func.count()).select_from(messages).where(
                messages.c.session_key == session_key,
                messages.c.role == "user",
                sa.func.date(messages.c.created_at) == today,
            )
        ) or 0
        if int(count) >= BOT_DAILY_LIMIT:
            await _send_limit_reached(update)
            return

    # Отправляем "печатает..."
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing",
    )

    try:
        from ai.openai_client import chat_with_ai
        session_key = f"tg_{tg_user.id}" if not user_id else None
        answer = await chat_with_ai(
            user_message=text,
            user_id=user_id,
            session_key=session_key,
        )
    except Exception as e:
        logger.warning("AI chat error: %s", e)
        await update.message.reply_text("Произошла ошибка. Попробуйте позже.")
        return

    # Добавляем счётчик если не безлимит
    if not unlimited:
        used = await _count_today_messages(user_id) if user_id else int(count) + 1
        remaining = max(0, BOT_DAILY_LIMIT - used)
        if remaining > 0:
            answer += f"\n\n_Осталось вопросов сегодня: {remaining} из {BOT_DAILY_LIMIT}_"
        else:
            answer += f"\n\n_Это был последний бесплатный вопрос на сегодня._"

    await update.message.reply_text(answer, parse_mode="Markdown")


async def _send_limit_reached(update: Update):
    site = (settings.SITE_URL or "").rstrip("/")
    app_url = site + "/app"
    await update.message.reply_text(
        "💬 Вы использовали все 5 бесплатных вопросов на сегодня.\n\n"
        "Дальнейшие ответы доступны в нашем сообществе — зарегистрируйтесь и получите доступ к AI без ограничений.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🍄 Войти в сообщество",
                web_app=WebAppInfo(url=app_url),
            )],
        ]),
    )
