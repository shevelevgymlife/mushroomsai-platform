"""Кнопки закрытого канала/чатов в боте и обработка заявок на вступление."""
from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, MessageHandler, filters

from bot.handlers.channel_autopost import main_keyboard_with_autopost
from bot.handlers.start import ensure_user_or_blocked_reply
from config import settings

logger = logging.getLogger(__name__)

BTN_CLOSED_CHANNEL = "📢 Закрытый канал"
BTN_CLOSED_GROUP = "👥 Закрытая группа"
BTN_CLOSED_CONSULT = "💬 Закрытый чат (консультации)"

CLOSED_BTN_RX = "^(" + "|".join(re.escape(x) for x in (BTN_CLOSED_CHANNEL, BTN_CLOSED_GROUP, BTN_CLOSED_CONSULT)) + ")$"


async def closed_telegram_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["tg_ai_mode"] = False
    u = await ensure_user_or_blocked_reply(update)
    if not u or not update.message:
        return
    uid = int(u.get("primary_user_id") or u["id"])
    text = (update.message.text or "").strip()
    from services.closed_telegram_access import (
        closed_access_entitlement_for_user,
        load_closed_telegram_config,
    )
    from services.payment_plans_catalog import get_effective_plans, drawer_menu_effective
    from services.subscription_service import check_subscription

    role = (u.get("role") or "user").lower()
    is_staff = role in ("admin", "moderator")
    eff_plan = await check_subscription(uid)
    plans = await get_effective_plans()
    plan_meta = plans.get(eff_plan) or plans.get("free") or {}
    ent = await closed_access_entitlement_for_user(uid, is_staff=is_staff, plan_meta=plan_meta)
    if not is_staff and eff_plan == "free":
        await update.message.reply_text(
            "Этот раздел доступен с активной подпиской. Оформите тариф в приложении или через «💳 Подписка».",
        )
        site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
        await update.message.reply_text("⌨️", reply_markup=await main_keyboard_with_autopost(site, False, uid))
        return

    if not is_staff:
        pdm = drawer_menu_effective(plan_meta)
        if pdm.get("closed_telegram") is False:
            await update.message.reply_text(
                "Пункт «Закрытый канал и чаты» не включён для вашего тарифа.",
            )
            site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
            await update.message.reply_text("⌨️", reply_markup=await main_keyboard_with_autopost(site, False, uid))
            return

    key_map = {
        BTN_CLOSED_CHANNEL: "channel",
        BTN_CLOSED_GROUP: "group",
        BTN_CLOSED_CONSULT: "consult",
    }
    rk = key_map.get(text, "channel")
    r = ent["resources"].get(rk) or {}
    url = r.get("url")
    if url:
        label = "Перейти"
        await update.message.reply_text(
            "Нажмите кнопку ниже. После входа бот сможет одобрить заявку, если чат настроен на приём через бота.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(label, url=url)]]),
        )
    else:
        cfg = await load_closed_telegram_config()
        if not cfg.get(f"{rk}_enabled"):
            await update.message.reply_text("Этот ресурс выключен в настройках платформы.")
        elif not (cfg.get(f"{rk}_invite_url") or "").strip():
            await update.message.reply_text("Ссылка ещё не задана администратором.")
        else:
            await update.message.reply_text(
                "Приобретите подписку с нужным уровнем доступа, чтобы открыть эту ссылку.",
            )
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
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
