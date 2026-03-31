"""
Мастер «Стать партнёром магазина и соцсети»: те же шаги и регламент, что на /referral.
Сохранение реферальной ссылки магазина в БД, закрепления ключевых сообщений, выдача ссылок приглашений.
"""
from __future__ import annotations

import html
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.handlers.start import (
    BTN_AI,
    BTN_AI_EXIT,
    BTN_COMMUNITY_POST,
    BTN_CONNECT_CHANNEL,
    BTN_PARTNER,
    ensure_user_or_blocked_reply,
)
from bot.handlers.channel_autopost import main_keyboard_with_autopost
from config import settings
from db.database import database
from db.models import users
from services.referral_service import invite_referral_code_for_sharing, referral_bonus_per_invite_rub
from services.referral_shop_prefs import SHOP_RUS_URL, normalize_referral_shop_url
from services.subscription_service import paid_subscription_for_referral_program

logger = logging.getLogger(__name__)

WAITING_SHOP_URL = 1

from services.referral_shop_prefs import TG_BTN_SHOP_MARKETPLACE, TG_BTN_SHOP_SIMPLE

_MENU_INTERRUPTS = frozenset(
    {
        BTN_AI,
        BTN_AI_EXIT,
        BTN_COMMUNITY_POST,
        BTN_CONNECT_CHANNEL,
        TG_BTN_SHOP_MARKETPLACE,
        TG_BTN_SHOP_SIMPLE,
        "🌐 Сообщество",
        "🌍 Веб версия",
        "🔒 Безопасность",
        "🆘 Тех. поддержка",
    }
)


async def _reply_kb(update: Update, internal_uid: int, ai_active: bool = False):
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    return await main_keyboard_with_autopost(site, ai_active, int(internal_uid))


async def _pin_safe(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> None:
    try:
        await context.bot.pin_chat_message(
            chat_id=chat_id, message_id=message_id, disable_notification=True
        )
    except Exception:
        logger.debug("pin skipped chat=%s msg=%s", chat_id, message_id, exc_info=True)


def _intro_html() -> str:
    return (
        "🤝 <b>Партнёр: коротко</b>\n\n"
        "1) Нужен <b>оплаченный Старт+</b> (пробные 3 дня не считаются).\n"
        "2) Магазин: Neurotrops → меню → кабинет → <b>Моя ссылка</b>.\n"
        "3) Пришлите ссылку сюда — я сохраню.\n"
        "4) Раздавайте ссылки Telegram и сайта.\n\n"
        "<i>Уже сохраняли ссылку? Напишите «дальше».</i>"
    )


async def partner_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["tg_ai_mode"] = False
    u = await ensure_user_or_blocked_reply(update)
    if not u or not update.effective_chat:
        return ConversationHandler.END
    uid = int(u.get("primary_user_id") or u["id"])
    kb = await _reply_kb(update, uid, ai_active=False)

    intro = await update.message.reply_html(_intro_html(), disable_web_page_preview=True)
    await _pin_safe(context, update.effective_chat.id, intro.message_id)

    if not await paid_subscription_for_referral_program(uid):
        site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
        pay_kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💳 Оплатить Старт+ здесь", web_app=WebAppInfo(url=f"{site}/subscriptions"))],
            ]
        )
        await update.message.reply_html(
            "⚠️ Нужен <b>оплаченный Старт+</b> (не пробные 3 дня).\n"
            "Оплатить можно кнопкой ниже.\n"
            "После оплаты снова нажмите «🤝 Стать партнёром» — пришлёте свою ссылку магазина.\n\n"
            "Пока без оплаты этап партнёрства недоступен.",
            reply_markup=pay_kb,
        )
        return ConversationHandler.END

    row = await database.fetch_one(users.select().where(users.c.id == uid))
    cur = ((row or {}).get("referral_shop_url") or "").strip()
    hint = ""
    if cur:
        hint = (
            f"\n\nСейчас сохранено:\n<code>{html.escape(cur[:900])}</code>\n\n"
            "Пришлите <b>новую</b> ссылку или напишите «<b>дальше</b>», чтобы перейти к пригласительным ссылкам."
        )
    else:
        hint = "\n\nПришлите ссылку вида <code>https://…</code> одним сообщением."

    rows = [
        [InlineKeyboardButton("🛍 Взять ссылку в магазине (Neurotrops)", url=SHOP_RUS_URL)],
    ]
    await update.message.reply_html(
        "<b>Шаг 2–3: ссылка магазина</b>\n"
        "Откройте Neurotrops, скопируйте «Моя ссылка» и отправьте сюда." + hint,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )
    return WAITING_SHOP_URL


async def _send_final_links_block(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    uid: int,
    kb,
    *,
    shop_saved: bool,
) -> None:
    code = await invite_referral_code_for_sharing(uid)
    plat = not await paid_subscription_for_referral_program(uid)
    bot = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@") or "mushrooms_ai_bot"
    base = (settings.SITE_URL or "").strip().rstrip("/")
    ref_tg = f"https://t.me/{bot}?start={code}" if code else "—"
    ref_site = f"{base}/login?ref={code}" if code and base else "—"
    bonus = referral_bonus_per_invite_rub()
    shop_note = ""
    if shop_saved:
        shop_note = "✅ Ссылка магазина сохранена. Приглашённые по приложению увидят ваш магазин.\n\n"
    plat_note = ""
    if plat:
        plat_note = "ℹ️ Сейчас показаны <b>ссылки платформы</b>. После оплаты «Старт+» будут ваши ссылки.\n\n"
    text = (
        f"📣 <b>Шаг 4: приглашения</b>\n\n"
        f"{plat_note}"
        f"{shop_note}"
        f"<b>Telegram:</b>\n"
        f"<a href=\"{html.escape(ref_tg, quote=True)}\">Открыть ссылку</a>\n"
        f"<code>{html.escape(ref_tg)}</code>\n\n"
        f"<b>Сайт:</b>\n"
        f"<a href=\"{html.escape(ref_site, quote=True)}\">Открыть ссылку</a>\n"
        f"<code>{html.escape(ref_site)}</code>\n\n"
        f"Раздавайте эти ссылки везде.\n"
        f"Подписки в приложении: до <b>10%</b> (~{bonus} ₽ со Старт), 1 раз после <b>платной</b> оплаты.\n"
        "Пробные 3 дня не считаются.\n\n"
        "Магазин: до ~10% по правилам Neurotrops. Учёт — в кабинете магазина."
    )
    inline = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📋 Копировать ссылки", callback_data="ref_copy_links")]]
    )
    msg = await update.message.reply_html(text, reply_markup=inline, disable_web_page_preview=True)
    await _pin_safe(context, update.effective_chat.id, msg.message_id)


async def ref_copy_links_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer("Отправляю ссылки для копирования")
    u = await ensure_user_or_blocked_reply(update)
    if not u:
        return
    uid = int(u.get("primary_user_id") or u["id"])
    code = await invite_referral_code_for_sharing(uid)
    bot = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@") or "mushrooms_ai_bot"
    base = (settings.SITE_URL or "").strip().rstrip("/")
    ref_tg = f"https://t.me/{bot}?start={code}" if code else "—"
    ref_site = f"{base}/login?ref={code}" if code and base else "—"
    await q.message.reply_text(
        "📋 Скопируйте и отправляйте:\n\n"
        f"Telegram:\n{ref_tg}\n\n"
        f"Сайт:\n{ref_site}"
    )


async def partner_receive_shop_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_chat:
        return ConversationHandler.END
    u = await ensure_user_or_blocked_reply(update)
    if not u:
        return ConversationHandler.END
    uid = int(u.get("primary_user_id") or u["id"])
    kb = await _reply_kb(update, uid, ai_active=False)

    raw = (update.message.text or "").strip()
    if raw == BTN_PARTNER:
        return await partner_start(update, context)

    if raw in _MENU_INTERRUPTS:
        await update.message.reply_text(
            "Ввод ссылки прерван. Нажмите «🤝 Стать партнёром» снова, если нужно продолжить.",
            reply_markup=kb,
        )
        return ConversationHandler.END

    low = raw.lower()
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    cur = ((row or {}).get("referral_shop_url") or "").strip()

    if low in ("дальше", "далее", "next", "skip", "готово"):
        if not cur:
            await update.message.reply_text(
                "Ссылка магазина ещё не сохранена. Пришлите https://… или откройте магазин кнопкой выше.",
                reply_markup=kb,
            )
            return WAITING_SHOP_URL
        await _send_final_links_block(update, context, uid, kb, shop_saved=False)
        return ConversationHandler.END

    try:
        normalized = normalize_referral_shop_url(raw)
    except ValueError as e:
        await update.message.reply_text(
            f"Не получилось принять ссылку: {e}\n"
            "Нужна ссылка с https://… Скопируйте «Моя ссылка» из кабинета Neurotrops.",
            reply_markup=kb,
        )
        return WAITING_SHOP_URL

    if not normalized:
        await update.message.reply_text("Пустая ссылка. Пришлите полный адрес https://…", reply_markup=kb)
        return WAITING_SHOP_URL

    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(referral_shop_url=normalized, referral_shop_partner_self=True)
    )

    conf = await update.message.reply_html(
        "✅ <b>Сохранено</b> — ссылка магазина привязана.\n\n"
        f"<code>{html.escape(normalized[:900])}</code>",
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    await _pin_safe(context, update.effective_chat.id, conf.message_id)

    await _send_final_links_block(update, context, uid, kb, shop_saved=True)
    return ConversationHandler.END


async def partner_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = await ensure_user_or_blocked_reply(update)
    uid = int(u.get("primary_user_id") or u["id"]) if u else 0
    kb = await _reply_kb(update, uid, ai_active=False) if u else None
    if update.message:
        await update.message.reply_text("Оформление партнёрства отменено.", reply_markup=kb)
    return ConversationHandler.END


def get_partner_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^" + re.escape(BTN_PARTNER) + "$"), partner_start),
        ],
        states={
            WAITING_SHOP_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, partner_receive_shop_url),
            ],
        },
        fallbacks=[CommandHandler("cancel", partner_cancel)],
        name="partner_wizard",
        allow_reentry=True,
        per_message=False,
    )
