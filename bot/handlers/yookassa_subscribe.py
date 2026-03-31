"""Оплата подписки через ЮKassa в Telegram (счёт + successful_payment)."""
from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.ext import ContextTypes, filters

from bot.handlers.start import ensure_user, ensure_user_or_blocked_reply
from config import settings
from services.payment_plans_catalog import get_effective_plans
from services.payment_provider_settings import get_provider_settings
from services.subscription_service import activate_subscription

logger = logging.getLogger(__name__)

_PAYLOAD_RX = re.compile(r"^nf\|(\d+)\|(start|pro|maxi)$")


class _SuccessfulPaymentFilter(filters.BaseFilter):
    def filter(self, message):
        return bool(message and message.successful_payment)


SUCCESSFUL_PAYMENT = _SuccessfulPaymentFilter()


async def _provider_ready() -> tuple[bool, dict]:
    st = await get_provider_settings("yookassa_bot")
    if not st.get("enabled"):
        return False, st
    pt = (st.get("provider_token") or "").strip()
    if not pt:
        return False, st
    return True, st


async def subscribe_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["tg_ai_mode"] = False
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    ok, _st = await _provider_ready()
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    if not ok:
        await update.message.reply_text(
            f"💳 <b>Подписка</b>\n\n"
            f"Оплата через бота ещё не включена в админке (Оплата → ЮKassa Бот) или не указан provider token.\n\n"
            f"Оформите подписку на сайте: {site}/subscriptions",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    plans = await get_effective_plans()
    rows = []
    for key in ("start", "pro", "maxi"):
        p = plans[key]
        rows.append(
            [
                InlineKeyboardButton(
                    f"{p['name']} — {p['price']} ₽/мес",
                    callback_data=f"tgpay_{key}",
                )
            ]
        )
    await update.message.reply_text(
        "💳 <b>Подписка на 1 месяц</b>\n\nВыберите тариф — откроется оплата через ЮKassa.",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


async def tgpay_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    m = re.match(r"^tgpay_(start|pro|maxi)$", q.data or "")
    if not m:
        return
    plan = m.group(1)
    tg = update.effective_user
    if not tg:
        return
    user = await ensure_user(tg)
    if not user:
        try:
            await q.answer("Доступ ограничен.", show_alert=True)
        except Exception:
            pass
        return

    ok, st = await _provider_ready()
    if not ok:
        await q.message.reply_text("Оплата в боте не настроена. Откройте сайт в разделе подписок.")
        return

    plans = await get_effective_plans()
    meta = plans[plan]
    price_rub = float(meta["price"])
    uid = int(user.get("primary_user_id") or user["id"])
    # Telegram для RUB: сумма в копейках (см. currencies.json)
    amount_kop = int(round(price_rub * 100))
    if amount_kop <= 0:
        await q.message.reply_text("Цена тарифа не настроена.")
        return

    payload = f"nf|{uid}|{plan}"
    provider_token = (st.get("provider_token") or "").strip()

    try:
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title=f"«{meta['name']}» — 1 мес.",
            description="Подписка NEUROFUNGI AI на 1 месяц",
            payload=payload,
            provider_token=provider_token,
            currency="RUB",
            prices=[LabeledPrice(f"Тариф {plan}", amount_kop)],
            start_parameter=f"sub_{plan}_{uid}",
        )
    except Exception:
        logger.exception("send_invoice failed uid=%s plan=%s", uid, plan)
        await q.message.reply_text("Не удалось выставить счёт. Попробуйте позже или оплатите на сайте.")


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.pre_checkout_query
    if not q:
        return
    payload = (q.invoice_payload or "").strip()
    mm = _PAYLOAD_RX.match(payload)
    if not mm:
        await q.answer(ok=False, error_message="Некорректный счёт.")
        return
    uid_payload = int(mm.group(1))
    plan = mm.group(2)
    tg = update.effective_user
    if not tg:
        await q.answer(ok=False, error_message="Нет пользователя.")
        return
    user = await ensure_user(tg)
    if not user:
        await q.answer(ok=False, error_message="Аккаунт недоступен.")
        return
    uid = int(user.get("primary_user_id") or user["id"])
    if uid != uid_payload:
        await q.answer(ok=False, error_message="Счёт выписан на другой аккаунт.")
        return

    plans = await get_effective_plans()
    price_rub = float((plans.get(plan) or {}).get("price") or 0)
    expected_kop = int(round(price_rub * 100))
    if expected_kop <= 0 or q.total_amount != expected_kop:
        await q.answer(ok=False, error_message="Сумма не совпадает с тарифом. Запросите счёт снова.")
        return

    await q.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.successful_payment:
        return
    sp = msg.successful_payment
    payload = (sp.invoice_payload or "").strip()
    mm = _PAYLOAD_RX.match(payload)
    if not mm:
        await msg.reply_text("Не удалось распознать оплату. Напишите в поддержку.")
        return
    uid = int(mm.group(1))
    plan = mm.group(2)

    plans = await get_effective_plans()
    price_rub = float((plans.get(plan) or {}).get("price") or 0)
    expected_kop = int(round(price_rub * 100))
    if expected_kop <= 0 or sp.total_amount != expected_kop:
        logger.warning(
            "successful_payment amount mismatch uid=%s plan=%s got=%s want=%s",
            uid,
            plan,
            sp.total_amount,
            expected_kop,
        )
        await msg.reply_text("Сумма платежа не совпала с тарифом. Обратитесь в поддержку.")
        return

    ok = await activate_subscription(uid, plan, months=1)
    if ok:
        pname = (plans.get(plan) or {}).get("name") or plan
        await msg.reply_text(
            f"✅ Оплата получена.\n\nТариф «{pname}» активен на 1 месяц. "
            f"Управление: {(settings.SITE_URL or '').rstrip('/')}/subscriptions"
        )
    else:
        await msg.reply_text("Оплата прошла, но не удалось активировать тариф. Напишите в поддержку, указав время оплаты.")


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Алиас /subscribe"""
    await subscribe_menu_handler(update, context)
