"""Кнопки закрытого канала/чатов в боте (всегда видны всем) и обработка заявок на вступление."""
from __future__ import annotations

import html
import logging
import re

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import ContextTypes, MessageHandler, filters

from bot.handlers.channel_autopost import main_keyboard_with_autopost
from bot.handlers.start import ensure_user_or_blocked_reply
from config import settings

logger = logging.getLogger(__name__)

from services.closed_telegram_access import (
    TG_BTN_CLOSED_BACK,
    TG_BTN_CLOSED_CHANNEL,
    TG_BTN_CLOSED_CONSULT,
    TG_BTN_CLOSED_GROUP,
    TG_BTN_CLOSED_HUB,
    closed_access_entitlement_for_user,
    closed_resource_invite_ready,
    load_closed_telegram_config,
    plan_closed_access,
)

CLOSED_BTN_RX = "^(" + "|".join(re.escape(x) for x in (TG_BTN_CLOSED_CHANNEL, TG_BTN_CLOSED_GROUP, TG_BTN_CLOSED_CONSULT)) + ")$"

KEY_BY_BTN = {
    TG_BTN_CLOSED_CHANNEL: "channel",
    TG_BTN_CLOSED_GROUP: "group",
    TG_BTN_CLOSED_CONSULT: "consult",
}


def closed_telegram_submenu_markup() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(TG_BTN_CLOSED_CHANNEL)],
        [KeyboardButton(TG_BTN_CLOSED_GROUP)],
        [KeyboardButton(TG_BTN_CLOSED_CONSULT)],
        [KeyboardButton(TG_BTN_CLOSED_BACK)],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


async def closed_telegram_hub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["tg_ai_mode"] = False
    u = await ensure_user_or_blocked_reply(update)
    if not u or not update.message:
        return
    await update.message.reply_text(
        "Выберите: закрытый канал, группу или чат консультаций.",
        reply_markup=closed_telegram_submenu_markup(),
    )


async def closed_telegram_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["tg_ai_mode"] = False
    u = await ensure_user_or_blocked_reply(update)
    if not u or not update.message:
        return
    uid = int(u.get("primary_user_id") or u["id"])
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    await update.message.reply_text(
        "Главное меню.",
        reply_markup=await main_keyboard_with_autopost(site, False, uid),
    )


def _sub_markup(site: str) -> InlineKeyboardMarkup:
    base = (site or "https://mushroomsai.onrender.com").rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base.lstrip("/")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💳 Оформить подписку",
                    web_app=WebAppInfo(url=base + "/subscriptions"),
                )
            ],
        ]
    )


async def closed_telegram_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["tg_ai_mode"] = False
    u = await ensure_user_or_blocked_reply(update)
    if not u or not update.message:
        return
    uid = int(u.get("primary_user_id") or u["id"])
    text = (update.message.text or "").strip()
    rk = KEY_BY_BTN.get(text)
    if not rk:
        return

    from services.payment_plans_catalog import get_effective_plans
    from services.subscription_service import check_subscription

    role = (u.get("role") or "user").lower()
    is_staff = role in ("admin", "moderator")
    eff_plan = await check_subscription(uid)
    plans = await get_effective_plans()
    plan_meta = plans.get(eff_plan) or plans.get("free") or {}

    def _pname(sk: str) -> str:
        return html.escape(str((plans.get(sk) or {}).get("name") or sk))

    ent = await closed_access_entitlement_for_user(uid, is_staff=is_staff, plan_meta=plan_meta)
    r = ent["resources"].get(rk) or {}
    url = r.get("url")
    cfg = await load_closed_telegram_config()
    ca = plan_closed_access(plan_meta)
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")

    if r.get("entitled") and url:
        await update.message.reply_text(
            "Нажмите кнопку ниже. После входа бот сможет одобрить заявку, если чат настроен на приём через бота.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Перейти", url=url)]]),
            parse_mode="HTML",
        )
        await update.message.reply_text("⌨️", reply_markup=await main_keyboard_with_autopost(site, False, uid))
        return

    if is_staff:
        if not cfg.get(f"{rk}_enabled"):
            await update.message.reply_text("Этот ресурс выключен в настройках платформы.")
        elif not (cfg.get(f"{rk}_invite_url") or "").strip():
            await update.message.reply_text("Ссылка ещё не задана в админке (Оплата → закрытые каналы и чаты).")
        else:
            raw_u = ((cfg.get(f"{rk}_invite_url") or "").strip())
            await update.message.reply_text(
                "Служебный просмотр ссылки:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Открыть", url=raw_u)]]),
            )
        await update.message.reply_text("⌨️", reply_markup=await main_keyboard_with_autopost(site, False, uid))
        return

    # Нет доступа по подписке или нет ссылки
    if ca.get(rk) and closed_resource_invite_ready(cfg, rk) and (cfg.get(f"{rk}_invite_url") or "").strip():
        await update.message.reply_text(
            "По тарифу доступ предусмотрен, но ссылка сейчас уточняется у администратора. Загляните позже или напишите в поддержку.",
            parse_mode="HTML",
        )
        await update.message.reply_text("⌨️", reply_markup=await main_keyboard_with_autopost(site, False, uid))
        return

    if rk in ("channel", "group"):
        body = (
            f"🔒 <b>Закрытый канал (библиотека) и закрытая группа</b>\n\n"
            f"Они открываются с тарифа «{_pname('start')}» и выше.\n\n"
            f"Сейчас подписка не активна или уровень ниже — оформите «{_pname('start')}» (или старше)."
        )
    else:
        body = (
            f"🔒 <b>Закрытый чат консультаций</b>\n\n"
            f"Доступен в тарифах «{_pname('pro')}» и «{_pname('maxi')}».\n\n"
            f"Оформите один из них, чтобы открыть чат."
        )

    await update.message.reply_text(
        body,
        reply_markup=_sub_markup(site),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await update.message.reply_text("⌨️", reply_markup=await main_keyboard_with_autopost(site, False, uid))


async def on_chat_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cjr = update.chat_join_request
    if not cjr or not cjr.from_user:
        return
    try:
        from services.closed_telegram_access import approve_chat_join_request_if_entitled

        await approve_chat_join_request_if_entitled(int(cjr.chat.id), int(cjr.from_user.id))
    except Exception as e:
        logger.info("chat_join_request handler: %s", e)


def closed_telegram_message_handler() -> MessageHandler:
    return MessageHandler(
        filters.ChatType.PRIVATE & filters.Regex(CLOSED_BTN_RX),
        closed_telegram_button_handler,
    )
