"""AI в главном боте: только после кнопки «Задать вопрос AI»; лимит 5/сутки (без подписки) или безлимит по тарифу Старт+."""
import logging

import sqlalchemy as sa
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

from bot.handlers.start import main_keyboard as _main_kb
from bot.handlers.channel_autopost import main_keyboard_with_autopost as _main_kb_autopost

from config import settings
from db.database import database
from db.models import admin_permissions, messages, users
from services.subscription_service import (
    can_ask_question,
    check_subscription,
    increment_question_count,
    FREE_AI_LIMIT_MESSAGE,
    FREE_AI_UPGRADE_INLINE,
)
from services.payment_plans_catalog import get_effective_plans

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


async def _standard_reply_kb(update: Update, site: str, ai_active: bool):
    tg_user = update.effective_user
    if not tg_user:
        return _main_kb(site, ai_active=ai_active)
    row = await _get_user_by_tg_id(tg_user.id)
    if row:
        return await _main_kb_autopost(site, ai_active, int(row["id"]))
    return _main_kb(site, ai_active=ai_active)


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
    return plan != "free"


async def _count_lifetime_tg_guest_user_messages(tg_inner_id: int) -> int:
    sk = f"tg_{int(tg_inner_id)}"
    return int(
        await database.fetch_val(
            sa.select(sa.func.count()).select_from(messages).where(
                messages.c.session_key == sk,
                messages.c.role == "user",
            )
        )
        or 0
    )


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
    )
    from services.referral_shop_prefs import TG_BTN_SHOP_MARKETPLACE, TG_BTN_SHOP_SIMPLE
    from services.closed_telegram_access import (
        TG_BTN_CLOSED_BACK,
        TG_BTN_CLOSED_CHANNEL,
        TG_BTN_CLOSED_CONSULT,
        TG_BTN_CLOSED_GROUP,
        TG_BTN_CLOSED_HUB,
    )
    from bot.handlers.channel_autopost import (
        BTN_AUTOPOST_DISABLE,
        BTN_AUTOPOST_ENABLE,
        BTN_CH_SOC_OFF,
        BTN_CH_SOC_ON,
    )

    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")

    # Кнопки обрабатываются отдельными хендлерами в main_bot; на всякий случай не дублируем
    if text in (
        BTN_AI,
        BTN_AI_EXIT,
        BTN_COMMUNITY_POST,
        BTN_CONNECT_CHANNEL,
        BTN_AUTOPOST_DISABLE,
        BTN_AUTOPOST_ENABLE,
        BTN_CH_SOC_ON,
        BTN_CH_SOC_OFF,
        TG_BTN_SHOP_MARKETPLACE,
        TG_BTN_SHOP_SIMPLE,
        TG_BTN_CLOSED_HUB,
        TG_BTN_CLOSED_BACK,
        TG_BTN_CLOSED_CHANNEL,
        TG_BTN_CLOSED_GROUP,
        TG_BTN_CLOSED_CONSULT,
    ):
        return

    # Мастер «Пост в сообщество» — не показывать подсказку про нейросеть (текст обрабатывает мастер или игнорируется)
    if context.user_data.get("cp_post_wizard"):
        return

    if not context.user_data.get("tg_ai_mode"):
        ask_ai_inline = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🤖 Задать вопрос AI", callback_data="tg_shop_ask_ai")]]
        )
        await update.message.reply_text(
            "Нажмите кнопку ниже и задайте вопрос AI.",
            parse_mode="HTML",
            reply_markup=ask_ai_inline,
        )
        return

    user_row = await _get_user_by_tg_id(tg_user.id)

    if user_row:
        user_id = int(user_row.get("primary_user_id") or user_row["id"])
        unlimited = await _is_unlimited_ai(user_id)
    else:
        user_id = None
        unlimited = False

    if not unlimited and user_id:
        if not await can_ask_question(user_id):
            await _send_limit_reached(update, context, site)
            return
    elif not unlimited and not user_id:
        eff0 = await get_effective_plans()
        guest_cap = int((eff0.get("free") or {}).get("questions_per_day") or BOT_DAILY_LIMIT)
        if await _count_lifetime_tg_guest_user_messages(tg_user.id) >= guest_cap:
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
            reply_markup=await _standard_reply_kb(update, site, True),
        )
        return

    if not unlimited:
        eff = await get_effective_plans()
        cap = int((eff.get("free") or {}).get("questions_per_day") or BOT_DAILY_LIMIT)
        remaining = 0
        if user_id:
            await increment_question_count(user_id)
            plan_u = await check_subscription(user_id)
            if plan_u == "free":
                row = await database.fetch_one(users.select().where(users.c.id == user_id))
                used = int((row or {}).get("daily_questions") or 0)
                remaining = max(0, cap - used)
        else:
            used = await _count_lifetime_tg_guest_user_messages(tg_user.id)
            remaining = max(0, cap - used)

        if user_id and plan_u != "free":
            await update.message.reply_text(
                answer,
                parse_mode="Markdown",
                reply_markup=ai_followup_inline(),
            )
            return

        if remaining > 0:
            answer += f"\n\n_Осталось бесплатных сообщений: {remaining} из {cap}._"
            await update.message.reply_text(
                answer,
                parse_mode="Markdown",
                reply_markup=ai_followup_inline(),
            )
            return

        answer = (answer or "").rstrip() + FREE_AI_UPGRADE_INLINE
        context.user_data["tg_ai_mode"] = False
        await update.message.reply_text(answer, parse_mode="Markdown")
        await update.message.reply_text(
            f"⏳ {FREE_AI_LIMIT_MESSAGE}\n\nРежим AI отключён.",
            reply_markup=await _standard_reply_kb(update, site, False),
            parse_mode="HTML",
        )
        await update.message.reply_text(
            "Приложение:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📱 Приложение (тариф Старт)",
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

    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    await q.message.reply_text(
        "Вы вышли из режима AI. Обычные кнопки бота снова активны.",
        reply_markup=await _standard_reply_kb(update, site, False),
    )


async def _send_limit_reached(update: Update, context: ContextTypes.DEFAULT_TYPE, site: str) -> None:
    context.user_data["tg_ai_mode"] = False

    await update.message.reply_text(
        f"💬 {FREE_AI_LIMIT_MESSAGE}\n\n"
        "Режим AI отключён. Кнопки бота работают как обычно.\n\n"
        "Подписка «Старт» в приложении — безлимитные вопросы к AI.",
        reply_markup=await _standard_reply_kb(update, site, False),
        parse_mode="HTML",
    )
    await update.message.reply_text(
        "Приложение:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📱 Приложение (тариф Старт)",
                        web_app=WebAppInfo(url=site or "https://mushroomsai.onrender.com"),
                    )
                ],
            ]
        ),
    )
