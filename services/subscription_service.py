import html
import logging
import time
from datetime import datetime, timedelta, date

from config import settings

logger = logging.getLogger(__name__)
from db.database import database
from db.models import users, subscriptions, subscription_events, direct_messages
from services.payment_plans_catalog import (
    DEFAULT_PLANS,
    get_effective_plans,
    plan_billing_timedelta,
    plan_display_name,
)

# Статические значения по умолчанию (для обратной совместимости импортов).
PLANS = DEFAULT_PLANS

START_TRIAL_DAYS = 3


async def format_admin_subscription_assigned_message(
    plan_key: str, end_date: datetime | None, *, unlimited: bool
) -> str:
    """Текст уведомления пользователю о назначении/смене тарифа из админки."""
    pk = (plan_key or "free").lower()
    if pk == "free":
        return (
            "Ваш тариф изменён на «Бесплатный». Расширенные функции недоступны до оформления подписки."
        )
    plans = await get_effective_plans()
    meta = plans.get(pk) or plans.get("free") or next(iter(plans.values()))
    pname = meta["name"]
    if unlimited:
        return f"Вам назначен тариф «{pname}» без срока окончания (бессрочно)."
    if end_date:
        d = end_date.strftime("%d.%m.%Y")
        return f"Вам назначен тариф «{pname}». Действует до {d} (дата окончания, UTC)."
    return f"Вам назначен тариф «{pname}»."


async def record_subscription_event(
    subject_user_id: int,
    kind: str,
    plan: str,
    price: float,
    valid_from: datetime | None,
    valid_to: datetime | None,
    counterparty_user_id: int | None = None,
) -> None:
    """Запись в историю подписок (лента в кабинете). Ошибки БД не пробрасываем."""
    try:
        pk = (plan or "free").lower()[:20]
        await database.execute(
            subscription_events.insert().values(
                subject_user_id=int(subject_user_id),
                kind=(kind or "")[:32],
                plan=pk,
                price=float(price or 0),
                valid_from=valid_from,
                valid_to=valid_to,
                counterparty_user_id=counterparty_user_id,
            )
        )
    except Exception:
        pass


async def _notify_paid_subscription_activated(
    user_id: int, plan_key: str, end_date: datetime | None
) -> None:
    """ЛС (чат) + Telegram + колокольчик: оформлена или продлена платная подписка."""
    from services.system_support_delivery import deliver_system_support_notification
    from services.in_app_notifications import create_notification

    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    notify_uid = int(row.get("primary_user_id") or user_id) if row else int(user_id)
    pname = await plan_display_name(plan_key)
    site = (settings.SITE_URL or "").rstrip("/")
    sub_url = f"{site}/subscriptions" if site else "/subscriptions"
    if end_date is None:
        d = "бессрочно"
        plain = (
            f"Ваша подписка оформлена: тариф «{pname}».\n"
            f"Срок: без ограничения по времени.\n\n"
            f"Управление: {sub_url}"
        )
    else:
        d = end_date.strftime("%d.%m.%Y")
        plain = (
            f"Ваша подписка оформлена: тариф «{pname}».\n"
            f"Действует до {d} (дата окончания, UTC).\n\n"
            f"Управление и продление: {sub_url}"
        )
    if (plan_key or "").lower() != "free":
        plain += (
            "\n\nПартнёрская программа поставщиков: в Telegram-боте — «Стать партнёром» "
            "(на платных тарифах)."
        )
    tg_html = (
        f"✅ <b>Подписка активирована</b>\n"
        f"Тариф: <b>{html.escape(pname)}</b>\n"
        f"{'Срок: <b>без ограничения</b>' if end_date is None else f'До: <b>{html.escape(d)}</b> (UTC)'}\n\n"
        f'<a href="{html.escape(sub_url, quote=True)}">Раздел подписок</a>'
    )
    if (plan_key or "").lower() != "free":
        tg_html += "\n\n<i>Партнёрская программа: в боте — «Стать партнёром».</i>"
    try:
        await deliver_system_support_notification(
            recipient_user_id=notify_uid,
            body_plain=plain,
            telegram_html=tg_html,
        )
    except Exception:
        logger.exception("subscription activated notify (support) failed uid=%s", user_id)
    try:
        await create_notification(
            recipient_id=notify_uid,
            actor_id=None,
            ntype="subscription_update",
            title="Подписка активирована",
            body=(f"Тариф «{pname}» без ограничения срока." if end_date is None else f"Тариф «{pname}» до {d} (UTC)."),
            link_url="/subscriptions",
            source_kind="subscription_activate",
            source_id=int(time.time() * 1000) % (2**31 - 1),
            skip_prefs=True,
        )
    except Exception:
        logger.debug("subscription activated in_app notify failed uid=%s", user_id, exc_info=True)


async def notify_subscription_manual_free(user_id: int, previous_plan: str) -> None:
    """Пользователь в кабинете перешёл на бесплатный тариф (был платный)."""
    if (previous_plan or "free").lower() == "free":
        return
    await _notify_subscription_became_free(int(user_id), previous_plan, reason="manual")


async def _notify_subscription_became_free(
    user_id: int, previous_plan: str, *, reason: str
) -> None:
    """
    ЛС + Telegram + колокольчик: больше нет платного тарифа.
    reason: 'expired' — срок оплаты истёк; 'manual' — пользователь или система перешли на free.
    """
    from services.system_support_delivery import deliver_system_support_notification
    from services.in_app_notifications import create_notification

    pp = await plan_display_name(previous_plan)
    site = (settings.SITE_URL or "").rstrip("/")
    sub_url = f"{site}/subscriptions" if site else "/subscriptions"
    if reason == "manual":
        plain = (
            f"Ваш тариф изменён на «Бесплатный» (ранее был «{pp}»).\n"
            "Расширенные функции платных планов недоступны до оформления подписки.\n\n"
            f"Тарифы: {sub_url}"
        )
        tg_html = (
            "📋 <b>Тариф изменён</b>\n"
            f"Сейчас: <b>Бесплатный</b> (ранее «{html.escape(pp)}»).\n"
            "Расширенные функции доступны после оформления подписки.\n\n"
            f'<a href="{html.escape(sub_url, quote=True)}">Выбрать подписку</a>'
        )
        title = "Тариф изменён"
        body = f"Сейчас «Бесплатный» (ранее «{pp}»)."
    else:
        plain = (
            f"Срок действия подписки «{pp}» истёк.\n"
            "Сейчас у вас тариф «Бесплатный» — расширенные функции недоступны до продления.\n\n"
            f"Продлить подписку: {sub_url}"
        )
        tg_html = (
            "⏳ <b>Подписка завершилась</b>\n"
            f"Истёк тариф «{html.escape(pp)}».\n"
            "Сейчас: <b>Бесплатный</b>.\n\n"
            f'<a href="{html.escape(sub_url, quote=True)}">Оформить подписку снова</a>'
        )
        title = "Подписка завершилась"
        body = f"Тариф «{pp}» истёк. Сейчас «Бесплатный»."

    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    notify_uid = int(row.get("primary_user_id") or user_id) if row else int(user_id)
    try:
        await deliver_system_support_notification(
            recipient_user_id=notify_uid,
            body_plain=plain,
            telegram_html=tg_html,
        )
    except Exception:
        logger.exception("subscription became free notify (support) failed uid=%s", user_id)
    try:
        await create_notification(
            recipient_id=notify_uid,
            actor_id=None,
            ntype="subscription_update",
            title=title,
            body=body,
            link_url="/subscriptions",
            source_kind=f"subscription_free_{reason}",
            source_id=int(time.time() * 1000) % (2**31 - 1),
            skip_prefs=True,
        )
    except Exception:
        logger.debug("subscription became free in_app notify failed uid=%s", user_id, exc_info=True)


async def activate_subscription(
    user_id: int,
    plan: str,
    months: int = 1,
    *,
    duration_minutes: int | None = None,
    paid_price_rub: float | None = None,
    skip_event_log: bool = False,
    credit_referrer_bonus: bool = True,
    skip_user_notify: bool = False,
    referral_bonus_payment_channel: str | None = None,
):
    eff = await get_effective_plans()
    if plan not in eff:
        return False
    meta = eff.get(plan) or eff["free"]
    now = datetime.utcnow()
    use_duration = False
    delta_minutes: int | None = None
    if duration_minutes is not None:
        try:
            delta_minutes = int(duration_minutes)
        except (TypeError, ValueError):
            delta_minutes = None
        if delta_minutes is not None and delta_minutes > 0:
            use_duration = True

    paid_lifetime = False
    period_delta: timedelta | None = None
    end_date: datetime | None = None
    base_price = float((meta or {}).get("price") or 0)

    if use_duration and delta_minutes is not None:
        end_date = now + timedelta(minutes=delta_minutes)
        price = float(paid_price_rub) if paid_price_rub is not None else base_price
    elif plan != "free" and bool(meta.get("billing_period_unlimited")):
        end_date = None
        paid_lifetime = True
        price = float(paid_price_rub) if paid_price_rub is not None else base_price
    elif plan != "free":
        period_delta = plan_billing_timedelta(meta)
        end_date = now + period_delta
        price = float(paid_price_rub) if paid_price_rub is not None else base_price
    else:
        m = max(1, int(months or 1))
        period_delta = timedelta(days=30 * m)
        end_date = now + period_delta
        price = base_price * m

    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if row:
        cur_plan = (row.get("subscription_plan") or "free").lower()
        cur_end = row.get("subscription_end")
        cur_life = bool(row.get("subscription_paid_lifetime"))
        if use_duration and delta_minutes is not None:
            if cur_plan == plan and cur_end and cur_end > now:
                end_date = cur_end + timedelta(minutes=delta_minutes)
            paid_lifetime = False
        elif paid_lifetime:
            end_date = None
        elif period_delta is not None:
            if cur_plan == plan and cur_end and cur_end > now:
                end_date = cur_end + period_delta
            elif cur_plan == plan and cur_life:
                end_date = now + period_delta

    await database.execute(
        subscriptions.insert().values(
            user_id=user_id,
            plan=plan,
            price=price,
            end_date=end_date,
            active=True,
        )
    )
    upd_vals: dict = {
        "subscription_plan": plan,
        "subscription_end": end_date,
        "subscription_admin_granted": False,
        "subscription_paid_lifetime": paid_lifetime,
        "marketplace_seller": (plan == "maxi"),
        "wellness_renewal_nudge_for_end": None,
    }
    if plan == "maxi":
        upd_vals["maxi_perks_grace_until"] = None
        upd_vals["maxi_shop_banner_until"] = None
    await database.execute(users.update().where(users.c.id == user_id).values(**upd_vals))
    if not skip_event_log:
        await record_subscription_event(
            int(user_id),
            "activation",
            plan,
            float(price),
            now,
            end_date,
            None,
        )
    bonus_rub = 0.0
    if credit_referrer_bonus and plan != "free":
        from services.referral_service import credit_referrer_bonus_for_paid_subscription

        bonus_rub = await credit_referrer_bonus_for_paid_subscription(
            int(user_id),
            float(price or 0.0),
            payment_channel=referral_bonus_payment_channel,
        )
    if plan != "free":
        try:
            from services.referral_service import notify_referrer_about_referred_subscription

            pname = str((eff.get(plan) or {}).get("name") or plan)
            await notify_referrer_about_referred_subscription(
                int(user_id),
                plan_label=f"подписку «{pname}»",
                bonus_rub=bonus_rub if bonus_rub > 0 else None,
            )
        except Exception:
            logger.debug("notify referrer about referred subscription failed uid=%s", user_id, exc_info=True)
    try:
        from services.wellness_journal_service import schedule_wellness_journal_if_paid

        await schedule_wellness_journal_if_paid(int(user_id))
    except Exception:
        pass
    if not skip_user_notify:
        try:
            await _notify_paid_subscription_activated(user_id, plan, end_date)
        except Exception:
            logger.exception("subscription activated user notify failed uid=%s", user_id)
    return True


async def gift_subscription(giver_id: int, recipient_id: int, plan: str) -> tuple[bool, str]:
    """Подарок тарифа на 1 месяц получателю (без оплаты; позже привяжете к оплате)."""
    eff = await get_effective_plans()
    pk = (plan or "").strip().lower()
    if pk not in eff or pk == "free":
        return False, "invalid_plan"
    if int((eff[pk].get("price") or 0)) <= 0:
        return False, "invalid_plan"
    if int(giver_id) == int(recipient_id):
        return False, "self"
    row = await database.fetch_one(users.select().where(users.c.id == int(recipient_id)))
    if not row:
        return False, "recipient_not_found"
    ok = await activate_subscription(
        int(recipient_id),
        pk,
        1,
        skip_event_log=True,
        credit_referrer_bonus=False,
        skip_user_notify=True,
    )
    if not ok:
        return False, "activate_failed"
    now = datetime.utcnow()
    refreshed = await database.fetch_one(users.select().where(users.c.id == int(recipient_id)))
    end_date = refreshed.get("subscription_end") if refreshed else None
    eff = await get_effective_plans()
    p = float(eff[pk]["price"])
    await record_subscription_event(
        int(recipient_id), "gift_in", pk, p, now, end_date, int(giver_id)
    )
    await record_subscription_event(
        int(giver_id), "gift_out", pk, p, now, end_date, int(recipient_id)
    )
    await _notify_subscription_gift_recipient(int(recipient_id), int(giver_id), pk)
    return True, "ok"


async def _notify_subscription_gift_recipient(recipient_id: int, giver_id: int, plan_key: str) -> None:
    """Telegram + личное сообщение в чатах сайта о подаренной подписке."""
    giver = await database.fetch_one(users.select().where(users.c.id == int(giver_id)))
    recipient = await database.fetch_one(users.select().where(users.c.id == int(recipient_id)))
    if not recipient:
        return
    gname = ((giver.get("name") or "").strip() or "Участник") if giver else "Участник"
    pname = await plan_display_name((plan_key or "start").lower())
    site = (settings.SITE_URL or "").rstrip("/")

    dm_text = (
        f"🎁 Вам подарили подписку «{pname}» на месяц.\n\n"
        f"Подарок от: {gname}.\n\n"
        "Тариф уже активен — доступны функции выбранного плана."
    )
    try:
        dm_row = await database.fetch_one_write(
            direct_messages.insert()
            .values(
                sender_id=int(giver_id),
                recipient_id=int(recipient_id),
                text=dm_text,
                is_read=False,
                is_system=True,
            )
            .returning(direct_messages.c.id)
        )
        mid = int(dm_row["id"]) if dm_row else None
        if mid:
            from services.legacy_dm_chat_sync import sync_direct_messages_pair

            await sync_direct_messages_pair(
                int(giver_id), int(recipient_id), broadcast_legacy_dm_id=mid
            )
    except Exception:
        pass

    try:
        from services.in_app_notifications import create_notification

        await create_notification(
            recipient_id=int(recipient_id),
            actor_id=int(giver_id),
            ntype="subscription_gift",
            title="Подарок подписки",
            body=f"{gname} подарил(а) вам «{pname}» на месяц. Тариф уже активен.",
            link_url="/subscriptions",
            source_kind="subscription_gift",
            source_id=int(time.time() * 1000) % (2**31 - 1) or int(recipient_id),
            skip_prefs=True,
        )
    except Exception:
        pass

    tg = recipient.get("tg_id") or recipient.get("linked_tg_id")
    if not tg:
        return
    from services.tg_notify import notify_user_telegram

    sub_url = f"{site}/subscriptions" if site else "/subscriptions"
    tg_html = (
        f"🎁 <b>Вам подарили подписку «{html.escape(pname)}»</b> на месяц.\n"
        f"От: <b>{html.escape(gname)}</b>.\n"
        "Тариф уже активен.\n"
        f'<a href="{html.escape(sub_url, quote=True)}">Открыть раздел подписок</a>'
    )
    await notify_user_telegram(int(tg), tg_html)


async def _notify_trial_started(user_id: int) -> None:
    from services.system_support_delivery import deliver_system_support_notification

    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return
    notify_uid = int(row.get("primary_user_id") or user_id)
    site = (settings.SITE_URL or "").rstrip("/")
    sub_url = f"{site}/subscriptions" if site else "/subscriptions"
    plain = (
        "Пробный доступ «Старт» на 3 дня активирован.\n"
        "Открыты лента, магазин, сообщения и остальные возможности тарифа Старт.\n"
        f"После окончания пробного периода можно оформить подписку: {sub_url}"
    )
    tg_html = (
        "🎁 <b>Пробный доступ «Старт» на 3 дня</b>\n"
        "Открыты лента, магазин, сообщения и остальные возможности тарифа Старт.\n"
        f"<a href=\"{html.escape(sub_url, quote=True)}\">Оформить подписку после окончания пробного периода</a>"
    )
    try:
        await deliver_system_support_notification(
            recipient_user_id=notify_uid,
            body_plain=plain,
            telegram_html=tg_html,
        )
    except Exception:
        pass


async def _notify_trial_ended(user_id: int) -> None:
    from services.system_support_delivery import deliver_system_support_notification

    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return
    notify_uid = int(row.get("primary_user_id") or user_id)
    site = (settings.SITE_URL or "").rstrip("/")
    sub_url = f"{site}/subscriptions" if site else "/subscriptions"
    plain = (
        "Пробный период «Старт» завершён.\n"
        "Доступ к ленте и функциям тарифа Старт приостановлен.\n"
        f"Выберите подписку: {sub_url}"
    )
    tg_html = (
        "⏳ <b>Пробный период «Старт» завершён</b>\n"
        "Доступ к ленте и функциям тарифа Старт приостановлен.\n"
        f"<a href=\"{html.escape(sub_url, quote=True)}\">Выбрать подписку Старт, Про или Макси</a>"
    )
    try:
        await deliver_system_support_notification(
            recipient_user_id=notify_uid,
            body_plain=plain,
            telegram_html=tg_html,
        )
    except Exception:
        pass


async def claim_start_trial(user_id: int) -> dict:
    """Одноразовая пробная подписка «как Старт» на START_TRIAL_DAYS дней."""
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return {"ok": False, "error": "not_found"}
    role = (row.get("role") or "user").lower()
    if role in ("admin", "moderator"):
        return {"ok": False, "error": "staff"}
    if row.get("start_trial_claimed_at"):
        return {"ok": False, "error": "already_used"}
    now = datetime.utcnow()
    if row.get("subscription_end") and row["subscription_end"] > now:
        p = (row.get("subscription_plan") or "free").lower()
        if p != "free":
            return {"ok": False, "error": "has_paid"}
    until = now + timedelta(days=START_TRIAL_DAYS)
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(
            start_trial_claimed_at=now,
            start_trial_until=until,
            start_trial_end_notified=False,
            wellness_renewal_nudge_for_end=None,
        )
    )
    await _notify_trial_started(user_id)
    try:
        from services.referral_service import notify_referrer_about_referred_subscription

        await notify_referrer_about_referred_subscription(
            int(user_id),
            plan_label="пробный доступ «Старт» на 3 дня",
            bonus_rub=None,
        )
    except Exception:
        logger.debug("notify referrer trial failed uid=%s", user_id, exc_info=True)
    await record_subscription_event(
        int(user_id), "trial_start", "start", 0.0, now, until, None
    )
    try:
        from services.wellness_journal_service import schedule_wellness_journal_if_paid

        await schedule_wellness_journal_if_paid(int(user_id))
    except Exception:
        pass
    return {"ok": True, "until": until.isoformat() + "Z"}


async def check_subscription(user_id: int) -> str:
    """
    Эффективный тариф для доступа: оплаченный активный, иначе активный пробный «Старт»,
    иначе free. При истечении оплаченного — сброс в free; при истечении пробного — уведомление в Telegram.
    """
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return "free"

    now = datetime.utcnow()
    sub_end = row.get("subscription_end")
    stored_plan = (row.get("subscription_plan") or "free").lower()
    admin_granted = bool(row.get("subscription_admin_granted"))

    # Активная оплата / назначение админом / бессрочная оплата из каталога
    if stored_plan != "free":
        if bool(row.get("subscription_paid_lifetime")):
            return stored_plan
        if sub_end and sub_end > now:
            return stored_plan
        if admin_granted and sub_end is None:
            return stored_plan

    # Просроченная оплата → free в БД (бессрочная выдача админом: subscription_end IS NULL)
    if stored_plan != "free" and (not sub_end or sub_end <= now):
        if not (admin_granted and sub_end is None) and not bool(row.get("subscription_paid_lifetime")):
            prev_plan = stored_plan
            if prev_plan == "maxi" and bool(row.get("marketplace_seller")):
                try:
                    from services.shop_referral_hub import schedule_maxi_perks_grace

                    await schedule_maxi_perks_grace(int(user_id))
                except Exception:
                    logger.debug("schedule_maxi_perks_grace failed uid=%s", user_id, exc_info=True)
            await database.execute(
                users.update()
                .where(users.c.id == user_id)
                .values(
                    subscription_plan="free",
                    subscription_end=None,
                    subscription_admin_granted=False,
                    subscription_paid_lifetime=False,
                )
            )
            row = await database.fetch_one(users.select().where(users.c.id == user_id)) or row
            try:
                await _notify_subscription_became_free(int(user_id), prev_plan, reason="expired")
            except Exception:
                logger.debug("subscription expired notify failed uid=%s", user_id, exc_info=True)

    # Пробный «Старт»
    trial_until = row.get("start_trial_until")
    if trial_until and trial_until > now:
        return "start"

    # Пробный истёк — одноразовое уведомление
    if (
        row.get("start_trial_claimed_at")
        and trial_until
        and trial_until <= now
        and not row.get("start_trial_end_notified")
    ):
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(start_trial_end_notified=True)
        )
        await _notify_trial_ended(user_id)

    return "free"


async def paid_subscription_for_referral_program(user_id: int) -> bool:
    """
    Персональные реферальные ссылки и партнёрство магазина: только активная
    оплаченная подписка Старт+ (не пробный 3-дневный «Старт»).
    Администраторы и модераторы — всегда.
    """
    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not row:
        return False
    uid = int(row.get("primary_user_id") or row["id"])
    if uid != int(user_id):
        row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return False
    role = (row.get("role") or "user").lower()
    if role in ("admin", "moderator"):
        return True
    now = datetime.utcnow()
    sp = (row.get("subscription_plan") or "free").lower()
    sub_end = row.get("subscription_end")
    admin_granted = bool(row.get("subscription_admin_granted"))
    if sp == "free":
        return False
    if bool(row.get("subscription_paid_lifetime")):
        return True
    if admin_granted and sub_end is None:
        return True
    if sub_end and sub_end > now:
        return True
    return False


async def web_default_home_path(user_id: int) -> str:
    """
    Куда вести с главной / после входа, если нет явного next:
    без доступа к ленте (free без пробного) → страница подписок;
    с доступом (оплата или активный пробный «Старт») → профиль в сообществе.
    """
    uid = int(user_id)
    plan = await check_subscription(uid)
    if plan == "free":
        return "/subscriptions"
    return f"/community/profile/{uid}"


async def can_ask_question(user_id: int) -> bool:
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return False

    role = (row.get("role") or "user").lower()
    if role in ("admin", "moderator"):
        return True

    plan = await check_subscription(user_id)
    if plan != "free":
        return True

    eff = await get_effective_plans()
    daily_cap = int(eff.get("free", {}).get("questions_per_day") or 5)
    if daily_cap < 0:
        return True

    today = date.today()
    if row["last_reset"] != today:
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(daily_questions=0, daily_recipes=0, last_reset=today)
        )
        return True

    return (row["daily_questions"] or 0) < daily_cap


async def increment_question_count(user_id: int):
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if row and (row.get("role") or "user").lower() in ("admin", "moderator"):
        return
    plan = await check_subscription(user_id)
    if plan != "free":
        return
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(daily_questions=users.c.daily_questions + 1)
    )


_KIND_LABEL_RU = {
    "trial_start": "Пробный период «Старт»",
    "activation": "Подключение тарифа",
    "free": "Бесплатный тариф",
    "gift_in": "Подарок от пользователя",
    "gift_out": "Подарок другому пользователю",
    "promo": "Промо-активация",
    "admin": "Назначение администратором",
}


async def fetch_subscription_history_display(user_id: int) -> list[dict]:
    """События истории + старые строки subscriptions без дубликатов с событиями."""
    uid = int(user_id)
    ev_rows = await database.fetch_all(
        subscription_events.select()
        .where(subscription_events.c.subject_user_id == uid)
        .order_by(subscription_events.c.created_at.desc())
    )
    sub_rows = await database.fetch_all(
        subscriptions.select()
        .where(subscriptions.c.user_id == uid)
        .order_by(subscriptions.c.start_date.desc())
    )
    items: list[dict] = []
    for r in ev_rows:
        d = dict(r)
        d["source"] = "event"
        d["row_id"] = f"e-{d['id']}"
        d["kind_label"] = _KIND_LABEL_RU.get(d.get("kind") or "", d.get("kind") or "—")
        items.append(d)

    used_sub_ids: set[int] = set()
    for e in items:
        vf = e.get("valid_from") or e.get("created_at")
        if vf is None:
            continue
        for s in sub_rows:
            if (s.get("plan") or "") != (e.get("plan") or ""):
                continue
            sd = s.get("start_date")
            if sd is None:
                continue
            try:
                if abs((vf - sd).total_seconds()) < 180:
                    used_sub_ids.add(int(s["id"]))
            except Exception:
                pass

    for s in sub_rows:
        sid = int(s["id"])
        if sid in used_sub_ids:
            continue
        items.append(
            {
                "source": "legacy_sub",
                "row_id": f"s-{sid}",
                "id": sid,
                "kind": "activation",
                "kind_label": _KIND_LABEL_RU["activation"],
                "plan": s.get("plan") or "free",
                "price": float(s.get("price") or 0),
                "valid_from": s.get("start_date"),
                "valid_to": s.get("end_date"),
                "counterparty_user_id": None,
                "created_at": s.get("start_date"),
            }
        )

    items.sort(
        key=lambda x: x.get("created_at") or datetime.min,
        reverse=True,
    )

    cp_ids = {int(i["counterparty_user_id"]) for i in items if i.get("counterparty_user_id")}
    names: dict[int, str] = {}
    if cp_ids:
        for crow in await database.fetch_all(users.select().where(users.c.id.in_(cp_ids))):
            cid = int(crow["id"])
            names[cid] = ((crow.get("name") or "").strip() or f"Участник #{cid}")
    for i in items:
        cid = i.get("counterparty_user_id")
        i["counterparty_name"] = names.get(int(cid), "") if cid else ""

    def _fmt(dt):
        if dt is None:
            return ""
        if hasattr(dt, "strftime"):
            return dt.strftime("%d.%m.%Y %H:%M")
        return str(dt)

    eff = await get_effective_plans()
    for i in items:
        pk = (i.get("plan") or "free").lower()
        i["plan_name"] = (eff.get(pk) or eff["free"])["name"]
        i["valid_from_s"] = _fmt(i.get("valid_from"))
        i["valid_to_s"] = _fmt(i.get("valid_to"))
        i["created_s"] = _fmt(i.get("created_at"))
        try:
            i["price_display"] = float(i.get("price") or 0)
        except (TypeError, ValueError):
            i["price_display"] = 0.0

    return items
