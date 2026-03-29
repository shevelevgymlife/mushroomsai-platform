"""AI в главном боте: только после кнопки «Задать вопрос AI»; лимит 5/сутки (без подписки) или безлимит по тарифу Старт+."""
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

from config import settings
from db.database import database
from db.models import admin_permissions, messages, users

logger = logging.getLogger(__name__)

BOT_DAILY_LIMIT = 5


def ai_followup_inline() -> InlineKeyboardMarkup:
    """После ответа AI — да/нет: продолжить или выйти."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, продолжить с AI", callback_data="tg_ai_continue")],
            [InlineKeyboardButton("❌ Нет, выйти из режима AI", callback_data="tg_ai_exit")],
        ]
    )


async def _get_user_by_tg_id(tg_id: int):
    return await database.fetch_one(
        users.select().where(sa.or_(users.c.tg_id == tg_id, users.c.linked_tg_id == tg_id))
    )


async def _has_unlimited_flag(user_id: int) -> bool:
    row = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == user_id)
    )
    if not row:
        return False
    return bool(row.get("can_ai_unlimited"))


async def _is_unlimited_ai(user_id: int | None) -> bool:
    if not user_id:
        return False
    if await _has_unlimited_flag(user_id):
        return True
    from services.subscription_service import check_subscription

    plan = await check_subscription(user_id)
    return plan in ("start", "pro", "maxi")


async def _count_today_messages(user_id: int) -> int:
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
    """Текст в общий чат — в AI только если включён режим tg_ai_mode (кнопка «Задать вопрос AI»)."""
    tg_user = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        return

    from bot.handlers.start import (
        BTN_AI,
        BTN_AI_EXIT,
        BTN_COMMUNITY_POST,
        BTN_CONNECT_CHANNEL,
        main_keyboard,
    )
    from bot.handlers.channel_autopost import BTN_AUTOPOST_DISABLE, BTN_AUTOPOST_ENABLE

    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")

    # Кнопки обрабатываются отдельными хендлерами в main_bot; на всякий случай не дублируем
    if text in (
        BTN_AI,
        BTN_AI_EXIT,
        BTN_COMMUNITY_POST,
        BTN_CONNECT_CHANNEL,
        BTN_AUTOPOST_DISABLE,
        BTN_AUTOPOST_ENABLE,
    ):
        return

    # Мастер «Пост в сообщество» — не показывать подсказку про нейросеть (текст обрабатывает мастер или игнорируется)
    if context.user_data.get("cp_post_wizard"):
        return

    if not context.user_data.get("tg_ai_mode"):
        await update.message.reply_text(
            "💬 <b>Нейросеть не подключена.</b>\n\n"
            "Чтобы задать вопрос AI, нажмите кнопку «🤖 Задать вопрос AI» внизу экрана.\n\n"
            "Обычные сообщения в этот чат не отправляются в нейросеть.",
            parse_mode="HTML",
            reply_markup=main_keyboard(site, ai_active=False),
        )
        return

    user_row = await _get_user_by_tg_id(tg_user.id)

    if user_row:
        user_id = user_row["id"]
        unlimited = await _is_unlimited_ai(user_id)
    else:
        user_id = None
        unlimited = False

    if not unlimited and user_id:
        count = await _count_today_messages(user_id)
        if count >= BOT_DAILY_LIMIT:
            await _send_limit_reached(update, context, site)
            return
    elif not unlimited and not user_id:
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
            await _send_limit_reached(update, context, site)
            return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

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
        await update.message.reply_text(
            "Произошла ошибка. Попробуйте позже.",
            reply_markup=main_keyboard(site, ai_active=True),
        )
        return

    if not unlimited:
        if user_id:
            used = await _count_today_messages(user_id)
        else:
            sk = f"tg_{tg_user.id}"
            today = datetime.now(timezone.utc).date()
            used = int(
                await database.fetch_val(
                    sa.select(sa.func.count()).select_from(messages).where(
                        messages.c.session_key == sk,
                        messages.c.role == "user",
                        sa.func.date(messages.c.created_at) == today,
                    )
                )
                or 0
            )
        remaining = max(0, BOT_DAILY_LIMIT - used)
        if remaining > 0:
            answer += f"\n\n_Осталось вопросов сегодня: {remaining} из {BOT_DAILY_LIMIT}_"
        else:
            answer += f"\n\n_Это был последний бесплатный вопрос на сегодня._"
            context.user_data["tg_ai_mode"] = False
            await update.message.reply_text(answer, parse_mode="Markdown")
            await update.message.reply_text(
                "⏳ Суточный лимит исчерпан. Режим AI отключён — кнопки бота снова только меню.\n\n"
                "Безлимит — подписка «Старт» в приложении.",
                reply_markup=main_keyboard(site, ai_active=False),
                parse_mode="HTML",
            )
            await update.message.reply_text(
                "Открыть приложение:",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🍄 Открыть приложение по подписке Старт",
                                web_app=WebAppInfo(url=site),
                            )
                        ],
                    ]
                ),
            )
            return

    await update.message.reply_text(
        answer,
        parse_mode="Markdown",
        reply_markup=ai_followup_inline(),
    )


async def tg_ai_continue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer("Напишите следующий вопрос текстом.")


async def tg_ai_exit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    context.user_data["tg_ai_mode"] = False
    await q.answer("Вы вышли из режима AI.")
    from bot.handlers.start import main_keyboard

    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    await q.message.reply_text(
        "Вы вышли из режима AI. Обычные кнопки бота снова активны.",
        reply_markup=main_keyboard(site, ai_active=False),
    )


async def _send_limit_reached(update: Update, context: ContextTypes.DEFAULT_TYPE, site: str) -> None:
    context.user_data["tg_ai_mode"] = False
    from bot.handlers.start import main_keyboard

    await update.message.reply_text(
        "💬 Вы использовали все 5 бесплатных вопросов на сегодня.\n\n"
        "Режим AI отключён. Кнопки бота работают как обычно.\n\n"
        "Безлимит — подписка «Старт» в приложении.",
        reply_markup=main_keyboard(site, ai_active=False),
        parse_mode="HTML",
    )
    await update.message.reply_text(
        "Открыть приложение:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🍄 Открыть приложение по подписке Старт",
                        web_app=WebAppInfo(url=site or "https://mushroomsai.onrender.com"),
                    )
                ],
            ]
        ),
    )
