"""Оплата подписки через ЮKassa в Telegram (счёт + successful_payment)."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import unicodedata

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.ext import ContextTypes, filters

from bot.handlers.start import ensure_user, ensure_user_or_blocked_reply
from config import settings
from services.payment_plans_catalog import get_effective_plans
from services.payment_provider_settings import get_provider_settings
from services.subscription_service import activate_subscription, check_subscription
from services.subscription_checkout import (
    resolve_active_subscription_checkout,
    subscription_stars_amount,
    telegram_stars_subscription_meta,
)
from services.yookassa_bot_offerings import (
    get_merged_bot_offerings,
    load_raw_offerings,
    offering_by_id,
)

logger = logging.getLogger(__name__)

# sendInvoice: поле title — строго 1–32 символа (Unicode), иначе Telegram возвращает Bad Request.
TG_INVOICE_TITLE_MAX_LEN = 32

# Текст согласия при оплате (оферта на сайте /legal/offer).
TG_SUBSCRIPTION_PAYMENT_NOTICE = (
    "Оплачивая подписку, вы соглашаетесь с условиями. Возврат средств за уже оплаченный период не предусмотрен."
)

# Сумма в копейках; опционально |nonce — чтобы каждый счёт был уникален (повторная покупка того же тарифа).
_PAYLOAD_RX = re.compile(r"^nf\|(\d+)\|([a-z0-9_]+)(?:\|(\d+))?(?:\|([a-f0-9]+))?$")
# Подписка за Telegram Stars: nfs|user_id|plan_slug|expected_stars[|nonce]
_STARS_PAYLOAD_RX = re.compile(r"^nfs\|(\d+)\|([a-z0-9_]+)\|(\d+)(?:\|([a-f0-9]+))?$")


def _invoice_payload_nf(uid: int, offering_id: str, amount_kop: int) -> str:
    """Payload ≤ 128 байт (Telegram); короткий nonce — уникальность без переполнения."""
    oid = (offering_id or "").lower()[:32]
    nonce = secrets.token_hex(3)
    raw = f"nf|{int(uid)}|{oid}|{int(amount_kop)}|{nonce}"
    if len(raw.encode("utf-8")) > 127:
        raw = f"nf|{int(uid)}|{oid}|{int(amount_kop)}|{secrets.token_hex(2)}"
    return raw


def _telegram_send_invoice_title(display_name: str, offering_id: str, *, is_renew: bool) -> str:
    """Заголовок счёта в лимите Telegram (32 символа). Полное имя тарифа — в description."""
    base = (display_name or offering_id or "Подписка").strip() or "Подписка"
    core = f"Продл. {base}" if is_renew else base
    if len(core) <= TG_INVOICE_TITLE_MAX_LEN:
        return core
    return core[: TG_INVOICE_TITLE_MAX_LEN - 1] + "…"


def _invoice_payload_nfs(uid: int, offering_id: str, n_stars: int) -> str:
    oid = (offering_id or "").lower()[:32]
    nonce = secrets.token_hex(3)
    raw = f"nfs|{int(uid)}|{oid}|{int(n_stars)}|{nonce}"
    if len(raw.encode("utf-8")) > 127:
        raw = f"nfs|{int(uid)}|{oid}|{int(n_stars)}|{secrets.token_hex(2)}"
    return raw


def _offering_is_renewal(off: dict, user: dict, eff_plan: str) -> bool:
    """Тот же тариф, что сейчас действует (оплата или пробный Старт) — показываем «Продлить»."""
    from datetime import datetime

    oid = str(off.get("id") or "").lower()
    eff = str(off.get("effective_plan") or oid).lower()
    now = datetime.utcnow()
    sub_plan = (user.get("subscription_plan") or "free").lower()
    sub_end = user.get("subscription_end")
    trial_until = user.get("start_trial_until")
    life = bool(user.get("subscription_paid_lifetime"))
    admin_granted = bool(user.get("subscription_admin_granted"))
    paid_active = sub_plan != "free" and (
        life
        or (sub_end and sub_end > now)
        or (admin_granted and sub_end is None)
    )
    trial_on = bool(trial_until and trial_until > now)
    if paid_active and sub_plan == eff:
        return True
    if trial_on and eff_plan == "start" and eff == "start":
        return True
    return False


def _subscription_status_lines_html(user: dict, plans_eff: dict, eff_plan: str) -> str:
    from datetime import datetime
    from html import escape

    def pname(k: str) -> str:
        return str((plans_eff.get(k) or {}).get("name") or k)

    now = datetime.utcnow()
    sub_plan = (user.get("subscription_plan") or "free").lower()
    sub_end = user.get("subscription_end")
    trial_until = user.get("start_trial_until")
    life = bool(user.get("subscription_paid_lifetime"))
    admin_granted = bool(user.get("subscription_admin_granted"))
    paid_active = sub_plan != "free" and (
        life
        or (sub_end and sub_end > now)
        or (admin_granted and sub_end is None)
    )
    trial_on = bool(trial_until and trial_until > now)

    if paid_active:
        nm = escape(pname(sub_plan))
        if life and not admin_granted:
            return f"📌 <b>Сейчас у вас:</b> «{nm}» (без срока)."
        if sub_end and sub_end > now:
            d = sub_end.strftime("%d.%m.%Y")
            days = max(0, (sub_end - now).days)
            return f"📌 <b>Сейчас у вас:</b> «{nm}» до <b>{d}</b> UTC (~{days} дн.)."
        if admin_granted:
            return f"📌 <b>Сейчас у вас:</b> «{nm}» (назначено администратором)."
        return f"📌 <b>Сейчас у вас:</b> «{nm}»."
    if trial_on and eff_plan == "start":
        d = trial_until.strftime("%d.%m.%Y")
        days = max(0, (trial_until - now).days)
        return f"🎁 <b>Пробный «{escape(pname('start'))}»</b> до <b>{d}</b> UTC (~{days} дн.)."
    return "📌 <b>Сейчас:</b> бесплатный тариф — ниже можно оформить подписку."


class _SuccessfulPaymentFilter(filters.MessageFilter):
    def filter(self, message):
        return bool(message and message.successful_payment)


SUCCESSFUL_PAYMENT = _SuccessfulPaymentFilter()


class _SubscribeButtonFilter(filters.MessageFilter):
    """Кнопка «💳 Подписка»: Telegram может слать другой вариант эмодзи — сравниваем по NFC и по ключевым словам."""

    def filter(self, message):
        if not message or not message.text:
            return False
        t = unicodedata.normalize("NFC", message.text.strip())
        from bot.handlers.start import BTN_SUBSCRIBE

        ref = unicodedata.normalize("NFC", BTN_SUBSCRIBE.strip())
        if t == ref:
            return True
        tl = t.lower()
        if "подписка" in tl and ("💳" in t or "\U0001f4b3" in message.text):
            return True
        return False


SUBSCRIBE_BUTTON_TEXT = _SubscribeButtonFilter()


def _payment_provider_token(st: dict) -> str:
    """Токен из Render (TELEGRAM_PAYMENT_PROVIDER_TOKEN) надёжнее, чем только БД — должен быть от того же бота, что TELEGRAM_TOKEN."""
    return (getattr(settings, "TELEGRAM_PAYMENT_PROVIDER_TOKEN", "") or "").strip() or (st.get("provider_token") or "").strip()


async def _provider_ready() -> tuple[bool, dict]:
    st = await get_provider_settings("yookassa_bot")
    if not st.get("enabled"):
        return False, st
    if not _payment_provider_token(st):
        return False, st
    return True, st


async def subscribe_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["tg_ai_mode"] = False
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    ck = await resolve_active_subscription_checkout()
    yk_ready, _st = await _provider_ready()
    show_card = yk_ready and ck.get("kind") == "yookassa"
    show_stars = bool(ck.get("telegram_stars_subscriptions_enabled"))
    stars_spr = float(ck.get("telegram_stars_per_rub") or 0.55)

    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    if not show_card and not show_stars:
        await update.message.reply_text(
            f"💳 <b>Подписка</b>\n\n"
            f"В боте не включена оплата: нужны ЮKassa (бот) для карты и/или Telegram Stars в админке → Оплата.\n\n"
            f"Сайт: {site}/subscriptions\n\n"
            f"<i>{TG_SUBSCRIPTION_PAYMENT_NOTICE}</i>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    uid = int(user.get("primary_user_id") or user["id"])
    plans_eff = await get_effective_plans()
    eff_plan = await check_subscription(uid)
    status_html = _subscription_status_lines_html(user, plans_eff, eff_plan)

    offerings = await get_merged_bot_offerings()
    rows: list[list[InlineKeyboardButton]] = []
    for o in offerings:
        if not o.get("enabled"):
            continue
        oid = str(o["id"])
        price = int(o.get("price_rub") or 0)
        if price <= 0:
            continue
        disp = (o.get("display_name") or oid)[:20]
        renew = _offering_is_renewal(o, user, eff_plan)
        btns: list[InlineKeyboardButton] = []
        if show_card:
            card_lbl = f"💳 Продлить · {disp} {price}₽" if renew else f"💳 {disp} {price}₽"
            btns.append(
                InlineKeyboardButton(
                    card_lbl[:64],
                    callback_data=f"tgpay_{oid}",
                )
            )
        if show_stars:
            nst = subscription_stars_amount(float(price), stars_spr)
            if nst > 0:
                star_lbl = f"⭐ Продлить {nst}" if renew else f"⭐ {nst}"
                btns.append(
                    InlineKeyboardButton(
                        star_lbl[:64],
                        callback_data=f"tgstars_{oid}",
                    )
                )
        if btns:
            rows.append(btns)

    if not rows:
        await update.message.reply_text(
            "💳 <b>Подписка</b>\n\n"
            "Нет доступных платных тарифов в каталоге. Настройте «Тарифы подписок» в админке (Оплата).",
            parse_mode="HTML",
        )
        return

    if show_card and show_stars:
        intro = (
            "Ниже все тарифы из каталога. <b>«Продлить»</b> — тот же уровень, что сейчас; срок после оплаты "
            "<b>прибавится</b> к оставшемуся. <b>💳</b> — карта (ЮKassa), <b>⭐</b> — Stars."
        )
    elif show_card:
        intro = (
            "Выберите тариф — откроется счёт ЮKassa. Для текущего тарифа кнопка с пометкой "
            "<b>«Продлить»</b>: оплаченный период удлинится."
        )
    else:
        intro = "Выберите тариф — оплата звёздами Telegram (XTR). Текущий тариф можно продлить — кнопка «Продлить»."

    await update.message.reply_text(
        f"💳 <b>Подписка</b>\n\n{status_html}\n\n{intro}\n\n"
        f"<i>{TG_SUBSCRIPTION_PAYMENT_NOTICE}</i>\n"
        f'<a href="{site}/legal/offer">Оферта</a> · <a href="{site}/legal/privacy">Конфиденциальность</a>',
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def tgpay_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    ck = await resolve_active_subscription_checkout()
    if ck.get("kind") != "yookassa":
        site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
        try:
            await q.message.reply_text(
                f"Сейчас подписка оформляется на сайте ({site}/subscriptions) — в админке выбран другой способ оплаты.",
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        return
    m = re.match(r"^tgpay_([a-z0-9_]+)$", q.data or "", re.I)
    if not m:
        return
    offering_id = m.group(1).lower()
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

    offerings = await get_merged_bot_offerings()
    off = offering_by_id(offerings, offering_id)
    if not off or not off.get("enabled"):
        await q.message.reply_text("Это предложение недоступно. Запросите меню снова.")
        return

    price_rub = float(off.get("price_rub") or 0)
    uid = int(user.get("primary_user_id") or user["id"])
    amount_kop = int(round(price_rub * 100))
    if amount_kop <= 0:
        await q.message.reply_text("Цена не настроена для этого предложения.")
        return

    eff_plan = await check_subscription(uid)
    is_renew = _offering_is_renewal(off, user, eff_plan)
    payload = _invoice_payload_nf(uid, offering_id, amount_kop)
    provider_token = _payment_provider_token(st)
    disp_name = str(off.get("display_name") or offering_id or "").strip()
    inv_title = _telegram_send_invoice_title(disp_name, offering_id, is_renew=is_renew)
    disp_short = (off.get("display_name") or offering_id)[:40]
    pay_ref = secrets.randbelow(899_999) + 100_000
    price_label = (
        (f"Продл.·{disp_short}" if is_renew else disp_short)
    )[:40] + f"#{pay_ref}"
    dur_h = off.get("duration_label") or ""
    site = (settings.SITE_URL or "").rstrip("/")
    extra = f" {TG_SUBSCRIPTION_PAYMENT_NOTICE}"
    if site:
        extra += f" {site}/legal/offer"
    renew_note = f"Продление подписки «{disp_name}». " if is_renew and disp_name else ("Продление подписки. " if is_renew else "")
    desc = f"{renew_note}NEUROFUNGI AI — {dur_h}.{extra}"[:255]

    try:
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title=inv_title,
            description=desc,
            payload=payload,
            provider_token=provider_token,
            currency="RUB",
            prices=[LabeledPrice(price_label[:32], amount_kop)],
            start_parameter=None,
        )
    except Exception as e:
        logger.exception(
            "send_invoice failed uid=%s offering=%s err=%s",
            uid,
            offering_id,
            getattr(e, "message", None) or str(e),
        )
        err = (str(e) or "").lower()
        hint = ""
        if any(x in err for x in ("token", "provider", "payment", "bot", "method")):
            hint = (
                "\n\nПроверьте в @BotFather: этот бот → Bot Settings → Payments — "
                "подключена ЮKassa и скопирован provider token в админку (тот же бот, что и TELEGRAM_TOKEN)."
            )
        await q.message.reply_text(
            "Не удалось выставить счёт. Попробуйте позже или оплатите на сайте." + hint,
            disable_web_page_preview=True,
        )


async def tgstars_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    ck = await resolve_active_subscription_checkout()
    if not ck.get("telegram_stars_subscriptions_enabled"):
        try:
            await q.message.reply_text("Оплата звёздами для подписок отключена в админке.")
        except Exception:
            pass
        return
    m = re.match(r"^tgstars_([a-z0-9_]+)$", q.data or "", re.I)
    if not m:
        return
    offering_id = m.group(1).lower()
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

    offerings = await get_merged_bot_offerings()
    off = offering_by_id(offerings, offering_id)
    if not off or not off.get("enabled"):
        await q.message.reply_text("Этот тариф недоступен. Запросите меню снова.")
        return

    price_rub = float(off.get("price_rub") or 0)
    spr = float(ck.get("telegram_stars_per_rub") or 0.55)
    n_stars = subscription_stars_amount(price_rub, spr)
    if n_stars <= 0:
        await q.message.reply_text("Цена тарифа не настроена.")
        return

    uid = int(user.get("primary_user_id") or user["id"])
    eff_plan = await check_subscription(uid)
    is_renew = _offering_is_renewal(off, user, eff_plan)
    payload = _invoice_payload_nfs(uid, offering_id, n_stars)
    disp_name = str(off.get("display_name") or offering_id or "").strip()
    inv_title = _telegram_send_invoice_title(disp_name, offering_id, is_renew=is_renew)
    disp_short = (off.get("display_name") or offering_id)[:36]
    pay_ref = secrets.randbelow(899_999) + 100_000
    lp_lbl = ((f"Продл·{disp_short}" if is_renew else disp_short)[:22] + f"#{pay_ref}")[:32]
    dur_h = off.get("duration_label") or ""
    site = (settings.SITE_URL or "").rstrip("/")
    extra = f" {TG_SUBSCRIPTION_PAYMENT_NOTICE}"
    if site:
        extra += f" {site}/legal/offer"
    renew_note = f"Продление «{disp_name}». " if is_renew and disp_name else ("Продление. " if is_renew else "")
    desc = f"{renew_note}NEUROFUNGI AI — {dur_h}. {n_stars} ⭐.{extra}"[:255]

    try:
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title=inv_title,
            description=desc,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice((lp_lbl or "Подписка")[:32], n_stars)],
            start_parameter=None,
        )
    except Exception:
        logger.exception("send_invoice stars failed uid=%s offering=%s", uid, offering_id)
        await q.message.reply_text(
            "Не удалось выставить счёт в Stars. Проверьте, что бот может принимать платежи в Telegram.",
            disable_web_page_preview=True,
        )


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.pre_checkout_query
    if not q:
        return
    payload = (q.invoice_payload or "").strip()
    sm = _STARS_PAYLOAD_RX.match(payload)
    if sm:
        uid_payload = int(sm.group(1))
        offering_id = sm.group(2).lower()
        exp_stars = int(sm.group(3))
        tg = update.effective_user
        if not tg:
            await q.answer(ok=False, error_message="Нет пользователя.")
            return
        user, plans, tsm = await asyncio.gather(
            ensure_user(tg), get_effective_plans(), telegram_stars_subscription_meta()
        )
        if not user:
            await q.answer(ok=False, error_message="Аккаунт недоступен.")
            return
        uid = int(user.get("primary_user_id") or user["id"])
        if uid != uid_payload:
            await q.answer(ok=False, error_message="Счёт выписан на другой аккаунт.")
            return
        if not tsm.get("available_for_subscriptions"):
            await q.answer(ok=False, error_message="Оплата Stars отключена.")
            return
        offerings = await load_raw_offerings(plans)
        off = offering_by_id(offerings, offering_id)
        if not off or not off.get("enabled"):
            await q.answer(ok=False, error_message="Тариф недоступен.")
            return
        spr = float(tsm.get("stars_per_rub") or 0.55)
        price_rub = float(off.get("price_rub") or 0)
        want = subscription_stars_amount(price_rub, spr)
        got = int(q.total_amount)
        cur = (getattr(q, "currency", None) or "").upper()
        if want <= 0 or want != exp_stars or got != exp_stars:
            logger.warning(
                "pre_checkout stars mismatch off=%s want=%s exp_payload=%s got=%s",
                offering_id,
                want,
                exp_stars,
                got,
            )
            await q.answer(ok=False, error_message="Сумма в Stars не совпадает. Запросите счёт снова.")
            return
        if cur and cur != "XTR":
            await q.answer(ok=False, error_message="Неверная валюта счёта.")
            return
        await q.answer(ok=True)
        return

    mm = _PAYLOAD_RX.match(payload)
    if not mm:
        logger.warning("pre_checkout bad payload: %s", payload[:200])
        await q.answer(ok=False, error_message="Некорректный счёт.")
        return
    uid_payload = int(mm.group(1))
    offering_id = mm.group(2).lower()
    amount_in_payload = mm.group(3)
    tg = update.effective_user
    if not tg:
        await q.answer(ok=False, error_message="Нет пользователя.")
        return
    user, plans = await asyncio.gather(ensure_user(tg), get_effective_plans())
    if not user:
        await q.answer(ok=False, error_message="Аккаунт недоступен.")
        return
    uid = int(user.get("primary_user_id") or user["id"])
    if uid != uid_payload:
        await q.answer(ok=False, error_message="Счёт выписан на другой аккаунт.")
        return

    # Без merge display_name — быстрее (у Telegram ~10 с на ответ pre_checkout)
    offerings = await load_raw_offerings(plans)
    off = offering_by_id(offerings, offering_id)
    if not off or not off.get("enabled"):
        logger.warning("pre_checkout offering disabled or missing id=%s", offering_id)
        await q.answer(ok=False, error_message="Предложение недоступно. Запросите счёт снова.")
        return

    if amount_in_payload:
        expected_kop = int(amount_in_payload)
    else:
        price_rub = float(off.get("price_rub") or 0)
        expected_kop = int(round(price_rub * 100))
    got = int(q.total_amount)
    if expected_kop <= 0 or got != expected_kop:
        logger.warning(
            "pre_checkout amount mismatch offering=%s expected_kop=%s got=%s payload_amt=%s",
            offering_id,
            expected_kop,
            got,
            amount_in_payload,
        )
        await q.answer(ok=False, error_message="Сумма не совпадает с тарифом. Запросите счёт снова.")
        return

    await q.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.successful_payment:
        return
    sp = msg.successful_payment
    payload = (sp.invoice_payload or "").strip()
    sm = _STARS_PAYLOAD_RX.match(payload)
    if sm:
        uid = int(sm.group(1))
        offering_id = sm.group(2).lower()
        exp_stars = int(sm.group(3))
        tsm = await telegram_stars_subscription_meta()
        if not tsm.get("available_for_subscriptions"):
            await msg.reply_text("Оплата Stars для подписок отключена. Напишите в поддержку.")
            return
        offerings = await get_merged_bot_offerings()
        off = offering_by_id(offerings, offering_id)
        if not off or not off.get("enabled"):
            await msg.reply_text("Тариф устарел. Обратитесь в поддержку.")
            return
        eff = str(off.get("effective_plan") or offering_id).strip().lower()
        price_rub = float(off.get("price_rub") or 0)
        spr = float(tsm.get("stars_per_rub") or 0.55)
        want = subscription_stars_amount(price_rub, spr)
        cur = (getattr(sp, "currency", None) or "").upper()
        paid_stars = int(sp.total_amount)
        if want <= 0 or want != exp_stars or paid_stars != exp_stars:
            logger.warning(
                "successful_payment stars mismatch uid=%s off=%s want=%s exp=%s paid=%s",
                uid,
                offering_id,
                want,
                exp_stars,
                paid_stars,
            )
            await msg.reply_text("Сумма не совпала с тарифом. Обратитесь в поддержку.")
            return
        if cur and cur != "XTR":
            await msg.reply_text("Неверная валюта платежа. Обратитесь в поддержку.")
            return
        ok = await activate_subscription(
            uid,
            eff,
            months=1,
            paid_price_rub=price_rub,
        )
        if ok:
            pname = off.get("display_name") or eff
            await msg.reply_text(
                f"✅ Оплата {paid_stars} ⭐ получена.\n\n«{pname}» активно ({off.get('duration_label') or ''}). "
                f"Управление: {(settings.SITE_URL or '').rstrip('/')}/subscriptions"
            )
        else:
            await msg.reply_text("Оплата прошла, но не удалось активировать тариф. Напишите в поддержку.")
        return

    mm = _PAYLOAD_RX.match(payload)
    if not mm:
        await msg.reply_text("Не удалось распознать оплату. Напишите в поддержку.")
        return
    uid = int(mm.group(1))
    offering_id = mm.group(2).lower()
    payload_amount_kop = mm.group(3)

    offerings = await get_merged_bot_offerings()
    off = offering_by_id(offerings, offering_id)
    if not off or not off.get("enabled"):
        await msg.reply_text("Предложение устарело. Обратитесь в поддержку.")
        return

    eff = str(off.get("effective_plan") or offering_id).strip().lower()
    price_rub = float(off.get("price_rub") or 0)
    if payload_amount_kop:
        expected_kop = int(payload_amount_kop)
    else:
        expected_kop = int(round(price_rub * 100))

    if expected_kop <= 0 or int(sp.total_amount) != expected_kop:
        logger.warning(
            "successful_payment mismatch uid=%s off=%s got=%s want=%s",
            uid,
            offering_id,
            sp.total_amount,
            expected_kop,
        )
        await msg.reply_text("Сумма не совпала с тарифом. Обратитесь в поддержку.")
        return

    ok = await activate_subscription(
        uid,
        eff,
        months=1,
        paid_price_rub=price_rub,
    )
    if ok:
        pname = off.get("display_name") or eff
        await msg.reply_text(
            f"✅ Оплата получена.\n\n«{pname}» активно ({off.get('duration_label') or ''}). "
            f"Управление: {(settings.SITE_URL or '').rstrip('/')}/subscriptions"
        )
    else:
        await msg.reply_text("Оплата прошла, но не удалось активировать тариф. Напишите в поддержку, указав время оплаты.")


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Алиас /subscribe"""
    await subscribe_menu_handler(update, context)
