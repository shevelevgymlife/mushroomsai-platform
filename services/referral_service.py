import html
import logging
import re
import secrets
import string
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import sqlalchemy as sa
from db.database import database
from db.models import (
    users,
    referrals,
    referral_withdrawals,
    referral_promo_links,
    subscriptions,
    referral_bonus_events,
)
from services.payment_plans_catalog import DEFAULT_PLANS, get_effective_plans, resolve_promo_plan_key

logger = logging.getLogger(__name__)

# Бонусы только за оплату подписки в рублях через ЮKassa / CloudPayments / счёт на карту ₽ в Telegram (не Stars).
REFERRAL_BONUS_PAYMENT_CHANNELS = frozenset({"yookassa", "cloudpayments", "telegram_card_rub"})


def _digits_inn(s: str | None) -> str:
    return re.sub(r"\D", "", str(s or ""))[:12]


def _payment_source_label(src: str | None) -> str:
    k = (src or "").strip().lower()
    return {
        "yookassa": "ЮKassa",
        "cloudpayments": "CloudPayments",
        "telegram_card_rub": "Карта ₽ (Telegram)",
        "activation": "—",
    }.get(k, k or "—")


async def _referral_withdraw_moscow_window_ok() -> tuple[bool, str]:
    from services.referral_payout_settings import get_referral_wd_moscow_days, moscow_calendar_day_in_window

    now = datetime.now(ZoneInfo("Europe/Moscow"))
    d = now.day
    lo, hi = await get_referral_wd_moscow_days()
    if moscow_calendar_day_in_window(d, lo, hi):
        return True, ""
    return (
        False,
        f"Заявки на вывод принимаются с {lo} по {hi} число каждого месяца (время Москвы). "
        f"Сейчас {now.strftime('%d.%m.%Y')}.",
    )


def _referral_bonus_from_paid_price(price_rub: float, bonus_percent: float) -> float:
    """Процент от фактически оплаченной суммы с округлением до копеек."""
    p = max(0.0, float(price_rub or 0.0))
    pct = max(0.0, min(100.0, float(bonus_percent)))
    rate = Decimal(str(pct)) / Decimal("100")
    return float((Decimal(str(p)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def referral_bonus_per_invite_rub(referrer_id: int | None = None) -> int:
    """Оценка «со Старт»: процент × цена Старт (для подсказок в UI). referrer_id — персональный % если задан."""
    from services.referral_bonus_settings import (
        get_effective_referrer_bonus_percent,
        get_referral_bonus_percent_global,
    )

    eff = await get_effective_plans()
    base = float((eff.get("start") or DEFAULT_PLANS.get("start") or {}).get("price") or 0)
    if referrer_id is not None:
        pct = await get_effective_referrer_bonus_percent(int(referrer_id))
    else:
        pct = await get_referral_bonus_percent_global()
    hint = float(
        (Decimal(str(base)) * Decimal(str(pct)) / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
    return max(1, int(round(hint))) if hint >= 1 else max(1, int(hint + 0.999))


async def _telegram_chat_id_for_user(user_id: int) -> Optional[int]:
    """Chat id для уведомлений: свой tg_id или у primary при связке аккаунтов."""
    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not row:
        return None
    tg = row.get("tg_id") or row.get("linked_tg_id")
    if tg:
        try:
            return int(tg)
        except (TypeError, ValueError):
            pass
    pid = row.get("primary_user_id")
    if pid:
        prow = await database.fetch_one(users.select().where(users.c.id == int(pid)))
        if prow:
            tg = prow.get("tg_id") or prow.get("linked_tg_id")
            if tg:
                try:
                    return int(tg)
                except (TypeError, ValueError):
                    pass
    return None


async def _notify_referrer_telegram_new_referral(referrer_id: int, referred_user_id: int) -> None:
    """Сообщение в бот рефереру: новый реферал по ссылке."""
    chat_id = await _telegram_chat_id_for_user(referrer_id)
    if not chat_id:
        return
    ref_row = await database.fetch_one(users.select().where(users.c.id == int(referred_user_id)))
    name = (ref_row.get("name") if ref_row else None) or "Новый участник"
    name_esc = html.escape((name or "")[:120])
    text = (
        f"🎉 <b>У тебя новый реферал</b>\n"
        f"{name_esc}\n\n"
        "Когда приглашённый оформит пробный или платный тариф, придёт отдельное уведомление."
    )
    try:
        from services.notify_user_stub import notify_user

        await notify_user(int(chat_id), text)
    except Exception:
        logger.debug("referrer new-ref telegram notify failed", exc_info=True)


async def notify_referrer_about_referred_subscription(
    referred_user_id: int,
    *,
    plan_label: str,
    bonus_rub: float | None = None,
) -> None:
    """
    Рефереру (в т.ч. админу платформы): приглашённый оформил пробный или платный тариф.
    ЛС в приложении + Telegram — имя, кликабельный профиль, id; при начислении бонуса — сумма.
    """
    row = await database.fetch_one(users.select().where(users.c.id == int(referred_user_id)))
    if not row:
        return
    uid = int(row.get("primary_user_id") or row["id"])
    if uid != int(referred_user_id):
        row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return
    rb = row.get("referred_by")
    if not rb:
        return
    referrer_id = int(rb)
    if referrer_id == uid:
        return

    rref = await database.fetch_one(users.select().where(users.c.id == referrer_id))
    if not rref:
        return
    notify_referrer_uid = int(rref.get("primary_user_id") or rref["id"])

    ref_name = ((row.get("name") or "").strip() or f"Участник #{uid}")[:160]
    from config import settings

    site = (settings.SITE_URL or "").strip().rstrip("/") or "https://mushroomsai.ru"
    profile_url = f"{site}/community/profile/{uid}"
    profile_esc = html.escape(profile_url, quote=True)
    name_esc = html.escape(ref_name, quote=True)
    label = (plan_label or "").strip() or "подписку"

    plain = (
        f"Ваш приглашённый оформил: {label}.\n"
        f"{ref_name} · id {uid}\n"
        f"Профиль: {profile_url}\n"
    )
    br = bonus_rub if bonus_rub is not None else None
    if br is not None and br > 0:
        plain += f"\nБонус {br:.0f} ₽ начислен на ваш баланс (платная подписка приглашённого)."

    tg_html = (
        f"📬 <b>Приглашённый оформил</b> {html.escape(label)}\n\n"
        f'<a href="{profile_esc}">{name_esc}</a> · id <code>{uid}</code>'
    )
    if br is not None and br > 0:
        ref_href = html.escape(f"{site}/referral", quote=True)
        tg_html += (
            f"\n\n💰 Бонус <b>{br:.0f} ₽</b> на балансе. "
            f'<a href="{ref_href}">Реферальная программа</a>'
        )

    try:
        from services.system_support_delivery import deliver_system_support_notification

        await deliver_system_support_notification(
            recipient_user_id=notify_referrer_uid,
            body_plain=plain,
            telegram_html=tg_html,
        )
    except Exception:
        logger.debug("notify_referrer_about_referred_subscription failed", exc_info=True)

    try:
        from services.in_app_notifications import create_notification
        import time as _t

        body = f"{ref_name} (id {uid}) — {label}."
        if br is not None and br > 0:
            body += f" Бонус {br:.0f} ₽."
        await create_notification(
            recipient_id=notify_referrer_uid,
            actor_id=None,
            ntype="subscription_update",
            title="Приглашённый оформил подписку",
            body=body[:500],
            link_url=f"/community/profile/{uid}",
            source_kind="referrer_referred_sub",
            source_id=int(_t.time() * 1000) % (2**31 - 1),
            skip_prefs=True,
        )
    except Exception:
        logger.debug("referrer in_app notify failed", exc_info=True)


async def generate_referral_code() -> str:
    while True:
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        existing = await database.fetch_one(users.select().where(users.c.referral_code == code))
        if not existing:
            return code


async def process_referral(new_user_id: int, referral_code: str) -> bool:
    """
    Привязать приглашённого к рефереру. Один раз на пользователя.
    Баланс рефереру начисляется при каждой платной покупке приглашённого
    (см. credit_referrer_bonus_for_paid_subscription), не за пробный 3 дня.
    Если владелец кода без активной Старт+ (не админ/модератор), закрепление идёт
    за платформенным аккаунтом (код default_admin_referral_code), как по ссылке админа.
    """
    ref = (referral_code or "").strip().upper()
    if not ref or len(ref) > 20:
        return False

    new_u = await database.fetch_one(users.select().where(users.c.id == new_user_id))
    if not new_u or new_u.get("referred_by"):
        return False

    referrer = await database.fetch_one(users.select().where(users.c.referral_code == ref))
    if not referrer or referrer["id"] == new_user_id:
        return False

    # Без активной Старт+ владелец кода не получает закреплений — как ссылки платформы (админ)
    role = (referrer.get("role") or "user").lower()
    if role not in ("admin", "moderator"):
        from services.subscription_service import paid_subscription_for_referral_program

        if not await paid_subscription_for_referral_program(int(referrer["id"])):
            admin_code = await default_admin_referral_code()
            if not admin_code:
                return False
            return await process_referral(new_user_id, admin_code)

    dup = await database.fetch_one(
        referrals.select().where(referrals.c.referred_id == new_user_id)
    )
    if dup:
        return False

    bonus = float(await referral_bonus_per_invite_rub(int(referrer["id"])))  # Оценка «со Старт» для карточек UI.

    await database.execute(
        users.update()
        .where(users.c.id == new_user_id)
        .values(referred_by=referrer["id"])
    )
    await database.execute(
        referrals.insert().values(
            referrer_id=referrer["id"],
            referred_id=new_user_id,
            bonus_applied=False,
            referral_bonus_amount=bonus,
        )
    )
    try:
        await _notify_referrer_telegram_new_referral(int(referrer["id"]), int(new_user_id))
    except Exception:
        logger.debug("notify new referral after process_referral", exc_info=True)
    return True


async def credit_referrer_bonus_for_paid_subscription(
    referred_user_id: int,
    paid_amount_rub: float,
    *,
    payment_channel: str | None = None,
) -> float:
    """
    Начислить рефереру % от фактической оплаченной суммы подписки приглашённого.
    Срабатывает на КАЖДУЮ платную покупку/продление (не trial), если у реферера активна любая
    платная подписка (любой не-free тариф из каталога) и оплата приглашённого через допустимый канал (₽, не Stars).

    Канонический реферер — users.referred_by (в т.ч. после слияния аккаунтов). Строка referrals
    синхронизируется с ним, чтобы список приглашённых и начисления не расходились.
    """
    ch = (payment_channel or "").strip().lower()
    if ch not in REFERRAL_BONUS_PAYMENT_CHANNELS:
        return 0.0

    row = await database.fetch_one(users.select().where(users.c.id == int(referred_user_id)))
    if not row:
        return 0.0
    uid = int(row.get("primary_user_id") or row["id"])

    rb = row.get("referred_by")
    if not rb:
        return 0.0
    rid = int(rb)
    if rid == uid:
        return 0.0

    ref_row = await database.fetch_one(
        referrals.select()
        .where(referrals.c.referred_id == uid)
        .order_by(referrals.c.id.desc())
        .limit(1)
    )
    if ref_row and int(ref_row["referrer_id"]) != rid:
        ref_id_fix = int(ref_row["id"])
        await database.execute(
            referrals.update().where(referrals.c.id == ref_id_fix).values(referrer_id=rid)
        )
        logger.info(
            "referral bonus: synced referrals.referrer_id to users.referred_by referred=%s -> referrer=%s",
            uid,
            rid,
        )
        ref_row = await database.fetch_one(referrals.select().where(referrals.c.id == ref_id_fix))
    elif not ref_row:
        bonus_est = float(await referral_bonus_per_invite_rub(rid))
        await database.execute(
            referrals.insert().values(
                referrer_id=rid,
                referred_id=uid,
                bonus_applied=False,
                referral_bonus_amount=bonus_est,
            )
        )
        ref_row = await database.fetch_one(
            referrals.select()
            .where(referrals.c.referred_id == uid)
            .order_by(referrals.c.id.desc())
            .limit(1)
        )
    if not ref_row:
        return 0.0

    from services.subscription_service import paid_subscription_for_referral_program

    if not await paid_subscription_for_referral_program(rid):
        return 0.0

    from services.referral_bonus_settings import get_effective_referrer_bonus_percent

    pct = await get_effective_referrer_bonus_percent(rid)
    bonus = _referral_bonus_from_paid_price(float(paid_amount_rub or 0.0), pct)
    if bonus <= 0:
        return 0.0

    ref_id = int(ref_row["id"])

    await database.execute(
        sa.text(
            "UPDATE users SET referral_balance = COALESCE(referral_balance, 0) + :b "
            "WHERE id = :uid"
        ),
        {"b": bonus, "uid": rid},
    )
    await database.execute(
        referrals.update().where(referrals.c.id == ref_id).values(bonus_applied=True)
    )
    try:
        sub = await database.fetch_one(
            subscriptions.select()
            .where(subscriptions.c.user_id == uid)
            .order_by(subscriptions.c.id.desc())
            .limit(1)
        )
        sub_id = int(sub["id"]) if sub and sub.get("id") is not None else None
        plan_key = str((sub.get("plan") if sub else None) or "start").strip().lower()[:20]
        await database.execute(
            referral_bonus_events.insert().values(
                referral_id=ref_id,
                referrer_id=rid,
                referred_id=uid,
                subscription_id=sub_id,
                plan_key=plan_key,
                paid_amount_rub=float(paid_amount_rub or 0.0),
                bonus_rub=float(bonus),
                payment_source=ch[:32],
            )
        )
    except Exception:
        logger.debug("referral bonus event insert skipped", exc_info=True)
    return bonus


async def apply_pending_web_invite(request, new_user_id: int) -> None:
    """После веб-регистрации: cookie invite_ref."""
    code = (request.cookies.get("invite_ref") or "").strip().upper()
    if code:
        await process_referral(new_user_id, code)


def clear_invite_cookie(response) -> None:
    response.delete_cookie("invite_ref", path="/")


def parse_invite_ref_code(request) -> Optional[str]:
    """Код приглашения из ?ref= или cookie invite_ref (та же валидация, что в attach_invite_ref_from_query)."""

    def _ok(code: str) -> bool:
        return bool(code) and 2 <= len(code) <= 20 and all(c.isalnum() for c in code)

    q = (request.query_params.get("ref") or "").strip().upper()
    if _ok(q):
        return q
    c = (request.cookies.get("invite_ref") or "").strip().upper()
    if _ok(c):
        return c
    return None


def attach_invite_ref_from_query(request, response) -> None:
    ref = (request.query_params.get("ref") or "").strip().upper()
    if ref and 2 <= len(ref) <= 20 and all(c.isalnum() for c in ref):
        response.set_cookie(
            "invite_ref",
            ref,
            max_age=90 * 24 * 3600,
            path="/",
            httponly=True,
            samesite="lax",
        )


async def finalize_web_referral(request, response, user_id: int) -> None:
    await apply_pending_web_invite(request, user_id)
    clear_invite_cookie(response)
    await apply_promo_token_from_cookie(request, response, user_id)
    try:
        await apply_default_referrer_if_absent(int(user_id))
    except Exception:
        logger.debug("apply_default_referrer_if_absent after web login failed", exc_info=True)


async def apply_referral_bonus(referral_id: int):
    """Устарело: бонус начисляется в credit_referrer_bonus_for_paid_subscription."""
    pass


async def ensure_user_referral_code(user_id: int) -> str:
    """Гарантирует referral_code у пользователя (для ссылок-приглашений)."""
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return ""
    code = (row.get("referral_code") or "").strip()
    if code:
        return code.upper()
    code = await generate_referral_code()
    await database.execute(users.update().where(users.c.id == user_id).values(referral_code=code))
    return code.upper()


async def resolve_default_admin_referral_user_id() -> Optional[int]:
    """Аккаунт платформы для «дефолтных» ссылок (как у администратора), если у пользователя нет оплаты."""
    from services.system_support_delivery import resolve_neurofungi_ai_user_id

    nid = await resolve_neurofungi_ai_user_id()
    if nid:
        return int(nid)
    row = await database.fetch_one(
        users.select().where(users.c.role == "admin").order_by(users.c.id.asc()).limit(1)
    )
    return int(row["id"]) if row else None


async def default_admin_referral_code() -> str:
    """Реферальный код для отображения при отсутствии платной подписки у участника."""
    uid = await resolve_default_admin_referral_user_id()
    if not uid:
        return ""
    return await ensure_user_referral_code(int(uid))


async def apply_default_referrer_if_absent(user_id: int) -> bool:
    """
    Прямой вход без реферального кода (t.me/bot, сайт без ?ref=): закрепить за платформенным
    аккаунтом — тот же код, что в default_admin_referral_code(). Тогда рефереру (админу)
    приходят уведомления о регистрации, пробном и платных подписках.
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
        return False
    admin_uid = await resolve_default_admin_referral_user_id()
    if not admin_uid or int(admin_uid) == uid:
        return False
    if row.get("referred_by"):
        return False
    code = await default_admin_referral_code()
    if not code:
        return False
    return await process_referral(uid, code)


async def invite_referral_code_for_sharing(user_id: int) -> str:
    """
    Код в ссылках приглашения (Telegram / сайт): свой при активной оплате Старт+,
    иначе код платформенного аккаунта (как у администратора).
    """
    from services.subscription_service import paid_subscription_for_referral_program

    if await paid_subscription_for_referral_program(user_id):
        return await ensure_user_referral_code(user_id)
    return await default_admin_referral_code()


def default_social_app_entry_url() -> str:
    """Общая ссылка входа (бот или /app) без реферального кода."""
    from config import settings

    bot_u = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    if bot_u:
        return f"https://t.me/{bot_u}"
    return f"{site}/app"


async def social_app_entry_url_for_channel_owner(user_id: int) -> str:
    """
    URL кнопки «войти в соцсеть» под постами канала владельца user_id.
    Если у владельца задана реферальная ссылка магазина (referral_shop_url), в ссылку
    добавляется его referral_code — как на странице /referral (бот ?start= или /login?ref=).
    """
    from config import settings

    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not row:
        return default_social_app_entry_url()

    if not (row.get("referral_shop_url") or "").strip():
        return default_social_app_entry_url()

    code = await invite_referral_code_for_sharing(int(user_id))
    if not code:
        return default_social_app_entry_url()

    bot_u = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    site = (settings.SITE_URL or "").strip().rstrip("/") or "https://mushroomsai.ru"
    if bot_u:
        return f"https://t.me/{bot_u}?start={code}"
    return f"{site}/login?ref={code}"


async def get_referral_stats(user_id: int) -> dict:
    refs = await database.fetch_all(
        referrals.select().where(referrals.c.referrer_id == user_id)
    )
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    bal = row.get("referral_balance") if row else 0
    res = row.get("referral_withdraw_reserved_rub") if row else 0
    try:
        bal_f = float(bal or 0)
    except (TypeError, ValueError):
        bal_f = 0.0
    try:
        res_f = float(res or 0)
    except (TypeError, ValueError):
        res_f = 0.0
    return {
        "total": len(refs),
        "bonus_applied": sum(1 for r in refs if r["bonus_applied"]),
        "balance_rub": round(bal_f, 2),
        "reserved_rub": round(res_f, 2),
    }


def _bonus_from_row(r: dict, default_bonus: float) -> float:
    v = r.get("referral_bonus_amount")
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return float(default_bonus)


async def get_referrer_invites_detailed(referrer_id: int) -> list[dict[str, Any]]:
    """Список приглашённых с бонусом и кратким статусом подписки."""
    rows = await database.fetch_all(
        referrals.select()
        .where(referrals.c.referrer_id == referrer_id)
        .order_by(referrals.c.created_at.desc())
    )
    out: list[dict[str, Any]] = []
    default_bonus = float(await referral_bonus_per_invite_rub(int(referrer_id)))
    for r in rows:
        rid = int(r["referred_id"])
        u = await database.fetch_one(users.select().where(users.c.id == rid))
        if not u:
            continue
        ud = dict(u)
        plan = (ud.get("subscription_plan") or "free").lower()
        trial = bool(ud.get("start_trial_until")) and (
            ud.get("start_trial_until") and ud["start_trial_until"] > datetime.utcnow()
        )
        sub_end = ud.get("subscription_end")
        ch = "web"
        if ud.get("tg_id") or ud.get("linked_tg_id"):
            ch = "telegram"
        elif ud.get("google_id") or ud.get("linked_google_id"):
            ch = "google"
        out.append(
            {
                "id": rid,
                "name": ud.get("name") or f"Участник #{rid}",
                "avatar": ud.get("avatar"),
                "created_at": r.get("created_at"),
                "bonus_rub": _bonus_from_row(dict(r), default_bonus),
                "bonus_credited": bool(r.get("bonus_applied")),
                "tg_id": ud.get("tg_id") or ud.get("linked_tg_id"),
                "google_id": ud.get("google_id") or ud.get("linked_google_id"),
                "subscription_plan": plan,
                "trial_active": trial,
                "subscription_end": sub_end,
                "registration_channel": ch,
            }
        )
    return out


async def list_referral_bonus_events_for_referrer(
    referrer_id: int,
    limit: int = 120,
) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        referral_bonus_events.select()
        .where(referral_bonus_events.c.referrer_id == int(referrer_id))
        .order_by(referral_bonus_events.c.credited_at.desc())
        .limit(int(limit))
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        rid = int(r.get("referred_id") or 0)
        u = await database.fetch_one(users.select().where(users.c.id == rid))
        out.append(
            {
                "id": int(r["id"]),
                "credited_at": r.get("credited_at"),
                "referred_id": rid,
                "referred_name": (u.get("name") if u else None) or f"Участник #{rid}",
                "plan_key": (r.get("plan_key") or "").strip().lower(),
                "paid_amount_rub": float(r.get("paid_amount_rub") or 0),
                "bonus_rub": float(r.get("bonus_rub") or 0),
                "subscription_id": r.get("subscription_id"),
                "payment_source": (r.get("payment_source") or "").strip().lower(),
                "payment_source_label": _payment_source_label(r.get("payment_source")),
            }
        )
    return out


async def _format_withdrawal_text(
    uid: int,
    amount: float,
    invites: list[dict],
    stats: dict,
    site: str,
    *,
    withdrawal_id: int | None = None,
) -> str:
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return ""
    u = dict(row)
    tg = u.get("tg_id") or u.get("linked_tg_id")
    name = u.get("name") or ""
    email = u.get("email") or ""
    ref_code = (u.get("referral_code") or "").strip()
    tax = (u.get("referral_tax_status") or "").strip()
    inn = _digits_inn(u.get("referral_partner_inn"))
    bank = (u.get("referral_payout_bank_note") or "").strip()
    default_bonus = float(await referral_bonus_per_invite_rub(int(uid)))
    lines = [
        "💸 <b>Запрос вывода реферального баланса</b>",
    ]
    if withdrawal_id:
        lines.append(f"<b>ID заявки:</b> <code>{withdrawal_id}</code>")
    lines += [
        f"ID пользователя: <code>{uid}</code>",
        f"Имя: {html.escape((name or '')[:200])}",
        f"Email: {html.escape(email or '—')}",
        f"Telegram ID: <code>{tg}</code>" if tg else "Telegram: не привязан",
        f"Код реферала: <code>{ref_code}</code>" if ref_code else "",
        f"Статус (налог): <b>{html.escape(tax or '—')}</b> · ИНН: <code>{html.escape(inn or '—')}</code>",
        f"Реквизиты: {html.escape(bank[:500] if bank else '—')}",
        f"Сумма к выводу: <b>{amount:.2f} ₽</b>",
        f"Доступный баланс (после резерва): <b>{stats.get('balance_rub', 0)} ₽</b>",
        f"В резерве на вывод (всего): <b>{stats.get('reserved_rub', 0)} ₽</b>",
        f"Всего приглашено: <b>{stats.get('total', 0)}</b>",
        "",
    ]
    if invites:
        dts = [inv.get("created_at") for inv in invites if inv.get("created_at")]
        if dts:
            dmin, dmax = min(dts), max(dts)
            lines.append(
                f"<b>Период приглашений:</b> {dmin.strftime('%d.%m.%Y %H:%M')} — {dmax.strftime('%d.%m.%Y %H:%M')}"
            )
    lines += [
        "",
        "<b>Приглашённые (профиль)</b>:",
    ]
    for inv in invites[:80]:
        dt = inv.get("created_at")
        ds = dt.strftime("%d.%m.%Y %H:%M") if dt else "—"
        lines.append(
            f"• <a href=\"{site}/community/profile/{inv['id']}\">{inv['name']}</a> "
            f"(id {inv['id']}) +{inv.get('bonus_rub', default_bonus):.0f} ₽ — {ds}"
        )
    if len(invites) > 80:
        lines.append(f"… и ещё {len(invites) - 80} чел.")
    return "\n".join(x for x in lines if x is not None)


async def _notify_partner_withdrawal_instructions(
    user_id: int, *, amount_rub: float, withdrawal_id: int
) -> None:
    """Инструкция партнёру в Telegram: чек самозанятого / документы ИП."""
    from config import settings

    chat_id = await _telegram_chat_id_for_user(int(user_id))
    if not chat_id:
        return
    try:
        from services.tg_notify import notify_user_telegram
    except Exception:
        return

    inn = (getattr(settings, "REFERRAL_CLIENT_INN", None) or "").strip()
    cname = (getattr(settings, "REFERRAL_CLIENT_NAME_LEGAL", None) or "").strip()
    site = (settings.SITE_URL or "").strip().rstrip("/")
    legal_url = f"{site}/legal/referral-payouts" if site else ""

    text = (
        f"💸 <b>Заявка на вывод #{withdrawal_id}</b>\n"
        f"Сумма: <b>{amount_rub:.2f} ₽</b> (зарезервирована; новые бонусы копятся на доступный баланс).\n\n"
        "После проверки реквизитов выплата производится <b>после</b> получения от вас закрывающего документа:\n"
        f"• <b>Самозанятый (НПД):</b> чек в приложении «Мой налог» на указанную сумму, заказчик — "
        f"<b>{html.escape(cname)}</b>, ИНН <code>{html.escape(inn)}</code>.\n"
        "• <b>ИП:</b> акт выполненных работ / счёт-фактура (как принято при вашем режиме).\n\n"
        "<b>Наименование услуги</b> (пример для самозанятого):\n"
        "<i>Рекламные услуги / привлечение клиентов по реферальной программе NEUROFUNGI AI</i>\n\n"
        "Пришлите <b>PDF или скрин</b> чека/документа в этот чат. Администратор проверит и переведёт средства "
        "на указанные в кабинете реквизиты.\n\n"
        + (
            f'<a href="{html.escape(legal_url, quote=True)}">Полные правила выплат</a>'
            if legal_url
            else ""
        )
    )
    try:
        await notify_user_telegram(int(chat_id), text[:4000], "HTML")
    except Exception:
        logger.debug("partner withdrawal instruction telegram failed", exc_info=True)


async def request_referral_withdrawal(user_id: int, amount_rub: float | None = None) -> tuple[bool, str]:
    """
    Создать заявку на вывод: сумма уходит в резерв (referral_withdraw_reserved_rub),
    с доступного referral_balance списывается; новые бонусы копятся только на referral_balance.
    """
    from config import settings

    from services.referral_payout_settings import get_referral_min_withdrawal_rub
    from services.subscription_service import paid_subscription_for_referral_program
    from services.tg_notify import notify_admin_referral_withdrawal_request

    uid = int(user_id)
    if not await paid_subscription_for_referral_program(uid):
        return False, "Вывод доступен при активной подписке Старт и выше (не пробный период)."

    urow = await database.fetch_one(users.select().where(users.c.id == uid))
    if not urow or not bool(urow.get("referral_shop_partner_self")):
        return (
            False,
            "Сначала оформите партнёрство: сохраните ссылку магазина в блоке «Стать партнёром магазина» на этой странице.",
        )

    tax = (urow.get("referral_tax_status") or "").strip().lower()
    if tax not in ("self_employed", "ip"):
        return False, "Укажите статус самозанятого или ИП и ИНН в форме «Данные для выплаты» ниже."

    inn = _digits_inn(urow.get("referral_partner_inn"))
    if len(inn) not in (10, 12):
        return False, "Укажите корректный ИНН (10 или 12 цифр)."

    bank = (urow.get("referral_payout_bank_note") or "").strip()
    if len(bank) < 5:
        return False, "Укажите реквизиты для перевода (карта / счёт) в форме ниже."

    ok_w, err_w = await _referral_withdraw_moscow_window_ok()
    if not ok_w:
        return False, err_w

    min_rub = float(await get_referral_min_withdrawal_rub())
    try:
        available = float(urow.get("referral_balance") or 0)
    except (TypeError, ValueError):
        available = 0.0
    available = round(max(0.0, available), 2)

    if amount_rub is None:
        amt = available
    else:
        amt = round(float(amount_rub), 2)
    if amt <= 0:
        return False, "Укажите сумму больше нуля."
    if amt - 1e-6 > available:
        return False, f"Недостаточно доступного баланса (доступно {available:.2f} ₽, в резерве уже не считается)."
    if amt + 1e-6 < min_rub:
        return False, f"Минимум для одной заявки: {min_rub:.0f} ₽ (запрошено {amt:.2f} ₽)."

    msk = datetime.now(ZoneInfo("Europe/Moscow"))
    ym = f"{msk.year}-{msk.month:02d}"

    exist_month = await database.fetch_one(
        referral_withdrawals.select()
        .where(referral_withdrawals.c.user_id == uid)
        .where(referral_withdrawals.c.withdraw_calendar_month == ym)
        .limit(1)
    )
    if exist_month:
        return False, "В этом календарном месяце заявка на вывод уже была подана."

    pend = await database.fetch_one(
        referral_withdrawals.select()
        .where(referral_withdrawals.c.user_id == uid)
        .where(referral_withdrawals.c.status == "pending")
        .limit(1)
    )
    if pend:
        return False, "Заявка на вывод уже на рассмотрении — дождитесь обработки."

    moved = await database.fetch_one(
        sa.text(
            """
            UPDATE users SET
              referral_balance = COALESCE(referral_balance, 0) - :a,
              referral_withdraw_reserved_rub = COALESCE(referral_withdraw_reserved_rub, 0) + :a
            WHERE id = :uid
              AND COALESCE(referral_balance, 0) + 1e-9 >= :a
            RETURNING id
            """
        ),
        {"a": amt, "uid": uid},
    )
    if not moved:
        return False, "Не удалось зарезервировать сумму. Обновите страницу и проверьте баланс."

    try:
        await database.execute(
            referral_withdrawals.insert().values(
                user_id=uid,
                amount_rub=amt,
                status="pending",
                withdraw_calendar_month=ym,
            )
        )
    except Exception:
        logger.exception("referral_withdrawals.insert failed; reverting reserve")
        await database.execute(
            sa.text(
                """
                UPDATE users SET
                  referral_balance = COALESCE(referral_balance, 0) + :a,
                  referral_withdraw_reserved_rub = GREATEST(
                    0, COALESCE(referral_withdraw_reserved_rub, 0) - :a
                  )
                WHERE id = :uid
                """
            ),
            {"a": amt, "uid": uid},
        )
        return False, "Ошибка записи заявки. Попробуйте позже."

    wid_row = await database.fetch_one(
        sa.select(referral_withdrawals.c.id)
        .where(referral_withdrawals.c.user_id == uid)
        .order_by(referral_withdrawals.c.id.desc())
        .limit(1)
    )
    wid_int = int(wid_row["id"]) if wid_row and wid_row.get("id") is not None else 0

    invites = await get_referrer_invites_detailed(uid)
    site = (settings.SITE_URL or "").rstrip("/") or ""
    stats = await get_referral_stats(uid)
    text = await _format_withdrawal_text(
        uid, amt, invites, stats, site, withdrawal_id=wid_int or None
    )

    await notify_admin_referral_withdrawal_request(text[:3900], withdrawal_id=wid_int)
    if wid_int:
        await _notify_partner_withdrawal_instructions(uid, amount_rub=amt, withdrawal_id=wid_int)
    return True, "ok"


REF_WITHDRAW_BTN_PREFIX = "💸 Вывести"


def referral_withdraw_button_caption(balance_rub: float) -> str:
    """Текст кнопки в Telegram (ReplyKeyboard)."""
    b = float(balance_rub or 0)
    if abs(b - round(b)) < 0.01:
        return f"{REF_WITHDRAW_BTN_PREFIX} {int(round(b))} ₽"
    return f"{REF_WITHDRAW_BTN_PREFIX} {b:.2f} ₽"


async def referral_withdraw_keyboard_row(internal_user_id: int):
    """Одна строка клавиатуры «Вывести N ₽» или None, если доступный баланс ниже минимума."""
    from telegram import KeyboardButton

    from services.referral_payout_settings import get_referral_min_withdrawal_rub

    row = await database.fetch_one(users.select().where(users.c.id == int(internal_user_id)))
    if not row:
        return None
    try:
        bal = float(row.get("referral_balance") or 0)
    except (TypeError, ValueError):
        bal = 0.0
    min_rub = float(await get_referral_min_withdrawal_rub())
    if bal + 1e-6 < min_rub:
        return None
    return [[KeyboardButton(referral_withdraw_button_caption(bal))]]


async def telegram_referral_withdraw_reply_html(user_id: int) -> tuple[bool, str]:
    """Текст ответа в HTML после нажатия «Вывести» в боте (та же логика, что POST /referral/withdraw)."""
    from config import settings

    ok, msg = await request_referral_withdrawal(int(user_id))
    site = (settings.SITE_URL or "https://mushroomsai.ru").strip().rstrip("/")
    ref_url = html.escape(f"{site}/referral", quote=True)
    if ok:
        return (
            True,
            "✅ <b>Заявка на вывод создана.</b>\n\n"
            "В этом чате вы получите сообщение с инструкцией по чеку (самозанятый / ИП). "
            f'Данные для выплаты можно править на сайте: <a href="{ref_url}">реферальный кабинет</a>.',
        )
    return (
        False,
        f"❌ {html.escape(str(msg))}\n\n"
        f'<a href="{ref_url}">Откройте сайт → реферальная программа</a> — укажите статус, ИНН и реквизиты, '
        "проверьте окно вывода (1–5 число, МСК) и партнёрство.",
    )


async def admin_mark_referral_withdrawal_paid(
    withdrawal_id: int, admin_note: str = ""
) -> tuple[bool, str]:
    """
    После фактического перевода: заявка paid, снимается только резерв (referral_withdraw_reserved_rub).
    referral_balance (доступный) не уменьшается — он уже был уменьшен при создании заявки.
    """
    wid = int(withdrawal_id)
    wrow = await database.fetch_one(
        referral_withdrawals.select().where(referral_withdrawals.c.id == wid)
    )
    if not wrow:
        return False, "not_found"
    if str(wrow.get("status") or "").strip().lower() != "pending":
        return False, "already_processed"
    uid = int(wrow["user_id"])
    amt = float(wrow.get("amount_rub") or 0)
    if amt < 0:
        return False, "bad_amount"

    await database.execute(
        sa.text(
            """
            UPDATE users SET referral_withdraw_reserved_rub = GREATEST(
              0,
              COALESCE(referral_withdraw_reserved_rub, 0) - :a
            )
            WHERE id = :uid
            """
        ),
        {"a": amt, "uid": uid},
    )
    await database.execute(
        referral_withdrawals.update()
        .where(referral_withdrawals.c.id == wid)
        .values(
            status="paid",
            processed_at=datetime.utcnow(),
            admin_note=(admin_note or "")[:2000],
        )
    )
    plain = (
        f"Выплата по заявке #{wid} подтверждена.\n"
        f"Сумма: {amt:.2f} ₽. Резерв снят; новые бонусы начисляются на доступный баланс.\n"
        f"{(admin_note or '').strip()}"
    ).strip()
    tg_html = (
        f"✅ <b>Выплата подтверждена</b>\n"
        f"Заявка <code>#{wid}</code> · <b>{amt:.2f} ₽</b>\n"
        f"Резерв снят. Доступный баланс при переводе не списывался повторно.\n"
        f"{admin_note or ''}"
    )[:3900]
    try:
        from services.system_support_delivery import deliver_system_support_notification

        await deliver_system_support_notification(
            recipient_user_id=int(uid),
            body_plain=plain,
            telegram_html=tg_html,
        )
    except Exception:
        pass
    return True, "ok"


async def admin_clear_referral_balance(user_id: int, admin_note: str = "") -> tuple[bool, str]:
    """Совместимость: найти pending-заявку пользователя и отметить оплаченной (снять резерв)."""
    row = await database.fetch_one(
        referral_withdrawals.select()
        .where(referral_withdrawals.c.user_id == int(user_id))
        .where(referral_withdrawals.c.status == "pending")
        .order_by(referral_withdrawals.c.id.desc())
        .limit(1)
    )
    if not row or row.get("id") is None:
        return False, "no_pending"
    return await admin_mark_referral_withdrawal_paid(int(row["id"]), admin_note)


async def apply_promo_token_from_cookie(request, response, user_id: int) -> None:
    """Активация подписки по промо-ссылке (cookie promo_token)."""
    token = (request.cookies.get("promo_token") or "").strip()
    if not token or len(token) > 64:
        return
    row = await database.fetch_one(
        referral_promo_links.select().where(referral_promo_links.c.token == token)
    )
    if not row:
        response.delete_cookie("promo_token", path="/")
        return
    r = dict(row)
    now = datetime.utcnow()
    if r.get("valid_until") and r["valid_until"] < now:
        response.delete_cookie("promo_token", path="/")
        return
    max_a = r.get("max_activations")
    cnt = int(r.get("activations_count") or 0)
    if max_a is not None and cnt >= int(max_a):
        response.delete_cookie("promo_token", path="/")
        return
    plan = await resolve_promo_plan_key(r.get("plan_key"))
    days = max(1, int(r.get("period_days") or 30))
    end = datetime.utcnow() + timedelta(days=days)
    eff = await get_effective_plans()
    price = float(eff[plan]["price"]) * (days / 30.0)
    await database.execute(
        subscriptions.insert().values(
            user_id=user_id,
            plan=plan,
            price=price,
            end_date=end,
            active=True,
        )
    )
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(
            subscription_plan=plan,
            subscription_end=end,
            subscription_admin_granted=False,
        )
    )
    await database.execute(
        referral_promo_links.update()
        .where(referral_promo_links.c.id == r["id"])
        .values(activations_count=cnt + 1)
    )
    from services.subscription_service import record_subscription_event

    await record_subscription_event(
        int(user_id), "promo", plan, float(price), now, end, None
    )
    response.delete_cookie("promo_token", path="/")


async def create_referral_promo_link(
    plan_key: str,
    period_days: int,
    max_activations: int | None,
    valid_until: datetime | None,
    created_by: int | None,
) -> dict | None:
    """Создать промо-ссылку (токен для cookie promo_token)."""
    pk = await resolve_promo_plan_key(plan_key)
    days = max(1, int(period_days or 30))
    token = secrets.token_urlsafe(48)[:64]
    await database.execute(
        referral_promo_links.insert().values(
            token=token,
            plan_key=pk,
            period_days=days,
            max_activations=max_activations,
            valid_until=valid_until,
            created_by=created_by,
        )
    )
    rid = await database.fetch_val(
        sa.select(referral_promo_links.c.id)
        .where(referral_promo_links.c.token == token)
        .limit(1)
    )
    return {"id": rid, "token": token, "plan_key": pk, "period_days": days}