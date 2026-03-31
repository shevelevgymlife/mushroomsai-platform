import html
import logging
import secrets
import string
from datetime import datetime, timedelta
from typing import Any, Optional

import sqlalchemy as sa
from db.database import database
from db.models import users, referrals, referral_withdrawals, referral_promo_links, subscriptions
from services.subscription_service import PLANS

logger = logging.getLogger(__name__)


def referral_bonus_per_invite_rub() -> int:
    """10% от месячной цены тарифа Старт (баллы на продление)."""
    return max(1, int(PLANS["start"]["price"] * 0.1))


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
        "Когда приглашённый оформит <b>платную</b> подписку «Старт» или выше "
        "(не пробный период), придёт ещё одно сообщение о бонусе."
    )
    try:
        from services.notify_user_stub import notify_user

        await notify_user(int(chat_id), text)
    except Exception:
        logger.debug("referrer new-ref telegram notify failed", exc_info=True)


async def _notify_referrer_telegram_bonus_credited(referrer_id: int, bonus_rub: float) -> None:
    """Сообщение в бот рефереру: бонус за платную подписку приглашённого."""
    chat_id = await _telegram_chat_id_for_user(referrer_id)
    if not chat_id:
        return
    from config import settings

    site = (settings.SITE_URL or "").strip().rstrip("/")
    if site:
        ref_href = html.escape(f"{site}/referral", quote=True)
        where = f'<a href="{ref_href}">Реферальная программа</a> — баланс и бонусы'
    else:
        where = "страница «Реферальная программа» в приложении — баланс и бонусы"
    amt = max(0.0, float(bonus_rub))
    text = (
        f"💰 <b>Приглашённый оформил платную подписку</b> (не пробный период).\n\n"
        f"Твоё вознаграждение <b>{amt:.0f} ₽</b> уже на балансе в приложении.\n"
        f"Смотри: {where}."
    )
    try:
        from services.notify_user_stub import notify_user

        await notify_user(int(chat_id), text)
    except Exception:
        logger.debug("referrer bonus telegram notify failed", exc_info=True)


async def generate_referral_code() -> str:
    while True:
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        existing = await database.fetch_one(users.select().where(users.c.referral_code == code))
        if not existing:
            return code


async def process_referral(new_user_id: int, referral_code: str) -> bool:
    """
    Привязать приглашённого к рефереру. Один раз на пользователя.
    Баланс рефереру начисляется только после первой платной подписки приглашённого
    (см. credit_referrer_bonus_for_paid_subscription), не за пробный 3 дня.
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

    dup = await database.fetch_one(
        referrals.select().where(referrals.c.referred_id == new_user_id)
    )
    if dup:
        return False

    bonus = float(referral_bonus_per_invite_rub())

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


async def credit_referrer_bonus_for_paid_subscription(referred_user_id: int) -> bool:
    """
    Однократно начислить рефереру баланс, когда приглашённый оформил платный тариф
    (Старт / Про / Макси). Пробный период не вызывает эту функцию.
    """
    row = await database.fetch_one(users.select().where(users.c.id == int(referred_user_id)))
    if not row:
        return False
    uid = int(row.get("primary_user_id") or row["id"])

    ref_row = await database.fetch_one(
        referrals.select()
        .where(referrals.c.referred_id == uid)
        .where(referrals.c.bonus_applied == False)  # noqa: E712
    )
    if not ref_row:
        return False

    bonus = float(ref_row.get("referral_bonus_amount") or referral_bonus_per_invite_rub())
    if bonus <= 0:
        return False

    rid = int(ref_row["referrer_id"])
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
        await _notify_referrer_telegram_bonus_credited(rid, bonus)
    except Exception:
        logger.debug("notify bonus after credit_referrer", exc_info=True)
    return True


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
    try:
        bal_f = float(bal or 0)
    except (TypeError, ValueError):
        bal_f = 0.0
    return {
        "total": len(refs),
        "bonus_applied": sum(1 for r in refs if r["bonus_applied"]),
        "balance_rub": round(bal_f, 2),
    }


def _bonus_from_row(r: dict) -> float:
    v = r.get("referral_bonus_amount")
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return float(referral_bonus_per_invite_rub())


async def get_referrer_invites_detailed(referrer_id: int) -> list[dict[str, Any]]:
    """Список приглашённых с бонусом и кратким статусом подписки."""
    rows = await database.fetch_all(
        referrals.select()
        .where(referrals.c.referrer_id == referrer_id)
        .order_by(referrals.c.created_at.desc())
    )
    out: list[dict[str, Any]] = []
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
                "bonus_rub": _bonus_from_row(dict(r)),
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


async def _format_withdrawal_text(
    uid: int,
    amount: float,
    invites: list[dict],
    stats: dict,
    site: str,
) -> str:
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return ""
    u = dict(row)
    tg = u.get("tg_id") or u.get("linked_tg_id")
    name = u.get("name") or ""
    email = u.get("email") or ""
    ref_code = (u.get("referral_code") or "").strip()
    default_bonus = float(referral_bonus_per_invite_rub())
    lines = [
        "💸 <b>Запрос вывода реферального баланса</b>",
        f"ID: <code>{uid}</code>",
        f"Имя: {name}",
        f"Email: {email or '—'}",
        f"Telegram ID: <code>{tg}</code>" if tg else "Telegram: не привязан",
        f"Код реферала: <code>{ref_code}</code>" if ref_code else "",
        f"Сумма к выводу: <b>{amount:.2f} ₽</b>",
        f"Текущий баланс на момент запроса: <b>{stats.get('balance_rub', 0)} ₽</b>",
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


async def request_referral_withdrawal(user_id: int) -> tuple[bool, str]:
    """Создать заявку на вывод и уведомить админа в Telegram."""
    from services.subscription_service import paid_subscription_for_referral_program

    if not await paid_subscription_for_referral_program(int(user_id)):
        return False, "Вывод доступен при активной подписке Старт и выше (не пробный период)."

    stats = await get_referral_stats(user_id)
    bal = float(stats.get("balance_rub") or 0)
    if bal < 1:
        return False, "Баланс меньше минимума для вывода"

    pend = await database.fetch_one(
        referral_withdrawals.select()
        .where(referral_withdrawals.c.user_id == user_id)
        .where(referral_withdrawals.c.status == "pending")
        .limit(1)
    )
    if pend:
        return False, "Заявка на вывод уже отправлена — дождитесь обработки"

    invites = await get_referrer_invites_detailed(user_id)
    from config import settings

    site = (settings.SITE_URL or "").rstrip("/") or ""

    await database.execute(
        referral_withdrawals.insert().values(
            user_id=user_id,
            amount_rub=bal,
            status="pending",
        )
    )

    text = await _format_withdrawal_text(user_id, bal, invites, stats, site)

    from services.notify_admin import notify_admin_telegram

    await notify_admin_telegram(text[:3900])
    return True, "ok"


async def admin_clear_referral_balance(user_id: int, admin_note: str = "") -> tuple[bool, str]:
    """Обнулить баланс после подтверждённого вывода (админ)."""
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return False, "not_found"
    prev = float(row.get("referral_balance") or 0)
    await database.execute(
        users.update().where(users.c.id == user_id).values(referral_balance=0)
    )
    await database.execute(
        referral_withdrawals.update()
        .where(referral_withdrawals.c.user_id == user_id)
        .where(referral_withdrawals.c.status == "pending")
        .values(
            status="paid",
            processed_at=datetime.utcnow(),
            admin_note=(admin_note or "")[:2000],
        )
    )
    plain = (
        f"Баланс реферальной программы обнулён после подтверждённого вывода.\n"
        f"Выведено: {prev:.2f} ₽\n"
        f"{(admin_note or '').strip()}"
    ).strip()
    tg_html = (
        f"✅ <b>Баланс реферальной программы обнулён</b>\n"
        f"Выведено: <b>{prev:.2f} ₽</b>\n"
        f"{admin_note or ''}"
    )[:3900]
    try:
        from services.system_support_delivery import deliver_system_support_notification

        await deliver_system_support_notification(
            recipient_user_id=int(user_id),
            body_plain=plain,
            telegram_html=tg_html,
        )
    except Exception:
        pass
    return True, "ok"


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
    plan = (r.get("plan_key") or "start").lower()
    if plan not in PLANS:
        plan = "start"
    days = max(1, int(r.get("period_days") or 30))
    end = datetime.utcnow() + timedelta(days=days)
    price = float(PLANS[plan]["price"]) * (days / 30.0)
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
    pk = (plan_key or "start").lower()
    if pk not in PLANS or pk == "free":
        pk = "start"
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