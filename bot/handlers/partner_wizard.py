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
from services.referral_bonus_settings import get_effective_referrer_bonus_percent
from services.referral_service import (
    REF_WITHDRAW_BTN_PREFIX,
    invite_referral_code_for_sharing,
    referral_bonus_per_invite_rub,
    telegram_referral_withdraw_reply_html,
)
from services.referral_shop_prefs import SHOP_RUS_URL, normalize_referral_shop_url_for_save
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
        "2) Магазин: каталог → меню → личный кабинет → <b>Моя ссылка</b> → копировать.\n"
        "3) Пришлите ссылку сюда — я сохраню.\n"
        "4) Раздавайте ссылки Telegram и сайта.\n\n"
        "Полные условия: <b>/referral → вкладка/блок «Условия»</b>.\n\n"
        "<i>Уже сохраняли ссылку? Напишите «дальше».</i>"
    )


async def partner_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["tg_ai_mode"] = False
    u = await ensure_user_or_blocked_reply(update)
    if not u or not update.effective_chat:
        return ConversationHandler.END
    uid = int(u.get("primary_user_id") or u["id"])
    kb = await _reply_kb(update, uid, ai_active=False)

    row = await database.fetch_one(users.select().where(users.c.id == uid))
    shop_url = ((row or {}).get("referral_shop_url") or "").strip()
    if await paid_subscription_for_referral_program(uid) and shop_url:
        await _send_final_links_block(
            update, context, uid, kb, shop_saved=True, already_partner=True
        )
        return ConversationHandler.END

    intro = await update.message.reply_html(_intro_html(), disable_web_page_preview=True)
    await _pin_safe(context, update.effective_chat.id, intro.message_id)
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    await update.message.reply_html(
        f"ℹ️ Условия партнёрства: <a href=\"{html.escape(site + '/referral#conditions', quote=True)}\">открыть</a>",
        disable_web_page_preview=True,
    )

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
        "Магазин: каталог → меню → личный кабинет → «Моя ссылка» → копировать. Затем отправьте ссылку сюда." + hint,
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
    already_partner: bool = False,
) -> None:
    code = await invite_referral_code_for_sharing(uid)
    plat = not await paid_subscription_for_referral_program(uid)
    bot = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@") or "mushrooms_ai_bot"
    base = (settings.SITE_URL or "").strip().rstrip("/")
    ref_tg = f"https://t.me/{bot}?start={code}" if code else "—"
    ref_site = f"{base}/login?ref={code}" if code and base else "—"
    bonus = await referral_bonus_per_invite_rub(uid)
    pct_eff = await get_effective_referrer_bonus_percent(uid)
    pct_txt = f"{int(pct_eff)}" if abs(pct_eff - round(pct_eff)) < 0.01 else f"{pct_eff:.2f}".rstrip("0").rstrip(".")
    conditions_url = f"{base}/referral#conditions" if base else "https://mushroomsai.ru/referral#conditions"
    shop_note = ""
    if shop_saved:
        shop_note = "✅ Ссылка магазина сохранена. Приглашённые по приложению увидят ваш магазин.\n\n"
    plat_note = ""
    if plat:
        plat_note = "ℹ️ Сейчас показаны <b>ссылки платформы</b>. После оплаты «Старт+» будут ваши ссылки.\n\n"
    if already_partner:
        title = "✅ <b>Вы уже партнёр</b> — магазин привязан.\n\nВот ваши ссылки:\n\n"
    else:
        title = f"📣 <b>Шаг 4: приглашения</b>\n\n"
    text = (
        f"{title}"
        f"{plat_note}"
        f"{shop_note}"
        f"<b>Telegram:</b>\n"
        f"<a href=\"{html.escape(ref_tg, quote=True)}\">Открыть ссылку</a>\n"
        f"<code>{html.escape(ref_tg)}</code>\n\n"
        f"<b>Сайт:</b>\n"
        f"<a href=\"{html.escape(ref_site, quote=True)}\">Открыть ссылку</a>\n"
        f"<code>{html.escape(ref_site)}</code>\n\n"
        f"Раздавайте эти ссылки везде.\n"
        f"Подписки в приложении: <b>{html.escape(pct_txt)}% от каждой фактической платной покупки</b> приглашённого.\n"
        "Начисление идёт, только если у вас в этот момент активна платная подписка (Старт/Про/Макси).\n"
        f"Пробные 3 дня не считаются. Для ориентира: со Старт это ~{bonus} ₽.\n\n"
        "Магазин: до ~10% по правилам Neurotrops. Учёт — в кабинете магазина.\n"
        f"Условия: <a href=\"{html.escape(conditions_url, quote=True)}\">подробно</a>"
    )
    inline = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Скопировать Telegram", callback_data="ref_copy_tg")],
            [InlineKeyboardButton("📋 Скопировать веб-ссылку", callback_data="ref_copy_site")],
            [InlineKeyboardButton("ℹ️ Как копировать", callback_data="ref_copy_help")],
        ]
    )
    msg = await update.message.reply_html(text, reply_markup=inline, disable_web_page_preview=True)
    await _pin_safe(context, update.effective_chat.id, msg.message_id)


async def _ref_links_for_user(update: Update) -> tuple[str, str] | None:
    u = await ensure_user_or_blocked_reply(update)
    if not u:
        return None
    uid = int(u.get("primary_user_id") or u["id"])
    code = await invite_referral_code_for_sharing(uid)
    bot = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@") or "mushrooms_ai_bot"
    base = (settings.SITE_URL or "").strip().rstrip("/")
    ref_tg = f"https://t.me/{bot}?start={code}" if code else "—"
    ref_site = f"{base}/login?ref={code}" if code and base else "—"
    return ref_tg, ref_site


async def ref_copy_tg_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer("Отправляю Telegram-ссылку")
    links = await _ref_links_for_user(update)
    if not links:
        return
    ref_tg, _ = links
    await q.message.reply_text(f"📋 Telegram ссылка:\n{ref_tg}")


async def ref_copy_site_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer("Отправляю веб-ссылку")
    links = await _ref_links_for_user(update)
    if not links:
        return
    _, ref_site = links
    await q.message.reply_text(f"📋 Веб-ссылка:\n{ref_site}")


async def ref_copy_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer()
    await q.message.reply_text(
        "ℹ️ Как копировать:\n"
        "1) Нажмите кнопку «Скопировать …».\n"
        "2) Удерживайте ссылку в сообщении.\n"
        "3) Нажмите «Копировать» и отправляйте."
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

    if raw.startswith(REF_WITHDRAW_BTN_PREFIX):
        ok, html_body = await telegram_referral_withdraw_reply_html(uid)
        await update.message.reply_html(
            html_body,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

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
        normalized = await normalize_referral_shop_url_for_save(raw, saver_user_id=uid)
    except ValueError as e:
        await update.message.reply_text(
            f"Не получилось принять ссылку: {e}\n"
            "Нужна ссылка с https://… Магазин: каталог → меню → личный кабинет → «Моя ссылка» → копировать.",
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


async def partner_referral_conditions_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Внутри мастера партнёрства команда /referral не должна «молчать»:
    даём ссылку на условия и продолжаем ожидать ссылку магазина.
    """
    if not update.message:
        return WAITING_SHOP_URL
    u = await ensure_user_or_blocked_reply(update)
    if not u:
        return ConversationHandler.END
    uid = int(u.get("primary_user_id") or u["id"])
    kb = await _reply_kb(update, uid, ai_active=False)
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    await update.message.reply_html(
        "📘 Условия партнёрской программы:\n"
        f'<a href="{html.escape(site + "/referral#conditions", quote=True)}">{html.escape(site + "/referral#conditions")}</a>\n\n'
        "После просмотра пришлите сюда вашу ссылку магазина (https://...).",
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    return WAITING_SHOP_URL


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
        fallbacks=[
            CommandHandler("cancel", partner_cancel),
            CommandHandler("referral", partner_referral_conditions_command),
        ],
        name="partner_wizard",
        allow_reentry=True,
        per_message=False,
    )
