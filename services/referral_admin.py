"""
Запросы для админки: реферальная программа, сегменты, рейтинги.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import func

from db.database import database
from db.models import referrals, referral_promo_links, referral_withdrawals, subscriptions, users


async def count_referrals_in_period(
    date_from: Optional[datetime], date_to: Optional[datetime]
) -> int:
    q = sa.select(func.count()).select_from(referrals)
    if date_from is not None:
        q = q.where(referrals.c.created_at >= date_from)
    if date_to is not None:
        q = q.where(referrals.c.created_at <= date_to)
    return int(await database.fetch_val(q) or 0)


async def sum_bonuses_in_period(
    date_from: Optional[datetime], date_to: Optional[datetime]
) -> float:
    q = sa.select(func.coalesce(func.sum(referrals.c.referral_bonus_amount), 0)).select_from(
        referrals
    )
    if date_from is not None:
        q = q.where(referrals.c.created_at >= date_from)
    if date_to is not None:
        q = q.where(referrals.c.created_at <= date_to)
    v = await database.fetch_val(q)
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


async def top_ambassadors_by_earnings(
    limit: int,
    date_from: Optional[datetime],
    date_to: Optional[datetime],
) -> list[dict[str, Any]]:
    """Сумма referral_bonus_amount по приглашениям за период."""
    q = sa.select(
        referrals.c.referrer_id,
        func.coalesce(func.sum(referrals.c.referral_bonus_amount), 0).label("total_bonus"),
        func.count().label("cnt"),
    )
    if date_from is not None:
        q = q.where(referrals.c.created_at >= date_from)
    if date_to is not None:
        q = q.where(referrals.c.created_at <= date_to)
    q = q.group_by(referrals.c.referrer_id).order_by(sa.desc("total_bonus")).limit(limit)
    rows = await database.fetch_all(q)
    out: list[dict[str, Any]] = []
    for r in rows:
        uid = int(r["referrer_id"])
        u = await database.fetch_one(users.select().where(users.c.id == uid))
        out.append(
            {
                "user_id": uid,
                "name": (dict(u).get("name") if u else None) or f"#{uid}",
                "tg_id": dict(u).get("tg_id") or dict(u).get("linked_tg_id") if u else None,
                "total_bonus": float(r["total_bonus"] or 0),
                "invites_in_period": int(r["cnt"] or 0),
            }
        )
    return out


async def top_ambassadors_by_invite_count(
    limit: int,
    date_from: Optional[datetime],
    date_to: Optional[datetime],
) -> list[dict[str, Any]]:
    q = sa.select(referrals.c.referrer_id, func.count().label("cnt"))
    if date_from is not None:
        q = q.where(referrals.c.created_at >= date_from)
    if date_to is not None:
        q = q.where(referrals.c.created_at <= date_to)
    q = q.group_by(referrals.c.referrer_id).order_by(sa.desc("cnt")).limit(limit)
    rows = await database.fetch_all(q)
    out: list[dict[str, Any]] = []
    for r in rows:
        uid = int(r["referrer_id"])
        u = await database.fetch_one(users.select().where(users.c.id == uid))
        bal = float(dict(u).get("referral_balance") or 0) if u else 0
        out.append(
            {
                "user_id": uid,
                "name": (dict(u).get("name") if u else None) or f"#{uid}",
                "tg_id": dict(u).get("tg_id") or dict(u).get("linked_tg_id") if u else None,
                "invite_count": int(r["cnt"] or 0),
                "referral_balance": bal,
            }
        )
    return out


async def leaderboard_balance_now(limit: int) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        users.select()
        .where(users.c.primary_user_id.is_(None))
        .where(users.c.referral_balance > 0)
        .order_by(users.c.referral_balance.desc())
        .limit(limit)
    )
    return [
        {
            "user_id": r["id"],
            "name": r.get("name") or f"#{r['id']}",
            "tg_id": r.get("tg_id") or r.get("linked_tg_id"),
            "referral_balance": float(r.get("referral_balance") or 0),
        }
        for r in rows
    ]


async def pending_withdrawals_list() -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        referral_withdrawals.select()
        .where(referral_withdrawals.c.status == "pending")
        .order_by(referral_withdrawals.c.created_at.asc())
    )
    out: list[dict[str, Any]] = []
    for w in rows:
        uid = int(w["user_id"])
        u = await database.fetch_one(users.select().where(users.c.id == uid))
        out.append(
            {
                "id": w["id"],
                "user_id": uid,
                "amount_rub": float(w.get("amount_rub") or 0),
                "created_at": w.get("created_at"),
                "user_name": (dict(u).get("name") if u else None) or "",
                "tg_id": dict(u).get("tg_id") or dict(u).get("linked_tg_id") if u else None,
            }
        )
    return out


async def paid_withdrawals_in_period(
    date_from: Optional[datetime], date_to: Optional[datetime]
) -> list[dict[str, Any]]:
    q = referral_withdrawals.select().where(referral_withdrawals.c.status == "paid")
    if date_from is not None:
        q = q.where(referral_withdrawals.c.processed_at >= date_from)
    if date_to is not None:
        q = q.where(referral_withdrawals.c.processed_at <= date_to)
    q = q.order_by(referral_withdrawals.c.processed_at.desc()).limit(500)
    rows = await database.fetch_all(q)
    out: list[dict[str, Any]] = []
    for w in rows:
        uid = int(w["user_id"])
        u = await database.fetch_one(users.select().where(users.c.id == uid))
        out.append(
            {
                "id": w["id"],
                "user_id": uid,
                "amount_rub": float(w.get("amount_rub") or 0),
                "processed_at": w.get("processed_at"),
                "admin_note": w.get("admin_note"),
                "user_name": (dict(u).get("name") if u else None) or "",
                "tg_id": dict(u).get("tg_id") or dict(u).get("linked_tg_id") if u else None,
            }
        )
    return out


async def list_promo_links() -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        referral_promo_links.select().order_by(referral_promo_links.c.created_at.desc()).limit(200)
    )
    return [dict(r) for r in rows]


async def renewal_ranking(limit: int = 40) -> list[dict[str, Any]]:
    """Количество записей в subscriptions за последние 12 мес — прокси «продлений»."""
    since = datetime.utcnow()
    from datetime import timedelta

    since = since - timedelta(days=365)
    q = (
        sa.select(subscriptions.c.user_id, func.count().label("cnt"))
        .where(subscriptions.c.start_date >= since)
        .group_by(subscriptions.c.user_id)
        .order_by(sa.desc("cnt"))
        .limit(limit)
    )
    rows = await database.fetch_all(q)
    out: list[dict[str, Any]] = []
    for r in rows:
        uid = int(r["user_id"])
        u = await database.fetch_one(users.select().where(users.c.id == uid))
        out.append(
            {
                "user_id": uid,
                "name": (dict(u).get("name") if u else None) or f"#{uid}",
                "subscription_rows_12m": int(r["cnt"] or 0),
            }
        )
    return out


def _user_channel(ud: dict) -> str:
    if ud.get("tg_id") or ud.get("linked_tg_id"):
        return "telegram"
    if ud.get("google_id") or ud.get("linked_google_id"):
        return "google"
    return "web"


async def referred_users_segment(
    segment: str,
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    plan_filter: Optional[str] = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    segment: all_referred | no_sub_activation | trial_active | paid_active |
             referral_attributed | organic | active_sub_now | inactive_after_trial | never_trial
    """
    now = datetime.utcnow()
    q = users.select().where(users.c.primary_user_id.is_(None))

    if segment == "organic":
        q = q.where(users.c.referred_by.is_(None))
    elif segment in (
        "all_referred",
        "no_sub_activation",
        "trial_active",
        "paid_active",
        "referral_attributed",
        "never_trial",
    ):
        q = q.where(users.c.referred_by.isnot(None))
    # active_sub_now, inactive_after_trial — по всей базе

    if date_from is not None:
        q = q.where(users.c.created_at >= date_from)
    if date_to is not None:
        q = q.where(users.c.created_at <= date_to)

    rows = await database.fetch_all(q)
    filtered: list[dict[str, Any]] = []
    for r in rows:
        ud = dict(r)
        if plan_filter and (ud.get("subscription_plan") or "free").lower() != plan_filter.lower():
            continue
        sp = (ud.get("subscription_plan") or "free").lower()
        trial_until = ud.get("start_trial_until")
        trial_on = bool(trial_until and trial_until > now)
        sub_end = ud.get("subscription_end")
        paid_active = sp != "free" and sub_end and sub_end > now
        never_trial = ud.get("start_trial_claimed_at") is None and ud.get("start_trial_until") is None

        if segment == "no_sub_activation":
            if not (sp == "free" and not trial_on and never_trial and ud.get("referred_by")):
                continue
        elif segment == "trial_active":
            if not trial_on:
                continue
        elif segment == "paid_active":
            if not paid_active:
                continue
        elif segment == "active_sub_now":
            if not paid_active:
                continue
        elif segment == "inactive_after_trial":
            if not (
                ud.get("start_trial_claimed_at")
                and not trial_on
                and sp == "free"
                and (not sub_end or sub_end <= now)
            ):
                continue
        elif segment == "never_trial":
            if not (never_trial and sp == "free"):
                continue
        elif segment == "referral_attributed":
            if not ud.get("referred_by"):
                continue
        elif segment == "organic":
            pass
        elif segment == "all_referred":
            if not ud.get("referred_by"):
                continue

        ch = _user_channel(ud)
        filtered.append(
            {
                "id": ud["id"],
                "name": ud.get("name") or f"#{ud['id']}",
                "tg_id": ud.get("tg_id") or ud.get("linked_tg_id"),
                "email": ud.get("email"),
                "subscription_plan": sp,
                "referred_by": ud.get("referred_by"),
                "channel": ch,
                "created_at": ud.get("created_at"),
            }
        )

    total = len(filtered)
    return filtered[:800], total


async def search_users(q: str, limit: int = 30) -> list[dict[str, Any]]:
    s = (q or "").strip()
    if not s:
        return []
    if s.isdigit():
        uid = int(s)
        row = await database.fetch_one(users.select().where(users.c.id == uid))
        if row:
            return [dict(row)]
        row = await database.fetch_one(users.select().where(users.c.tg_id == uid))
        if row:
            return [dict(row)]
        row = await database.fetch_one(users.select().where(users.c.linked_tg_id == uid))
        return [dict(row)] if row else []
    like = f"%{s}%"
    rows = await database.fetch_all(
        users.select()
        .where(users.c.primary_user_id.is_(None))
        .where(
            sa.or_(
                users.c.name.ilike(like),
                users.c.email.ilike(like),
            )
        )
        .limit(limit)
    )
    return [dict(r) for r in rows]


async def invites_for_referrer(referrer_id: int) -> list[dict[str, Any]]:
    from services.referral_service import get_referrer_invites_detailed

    return await get_referrer_invites_detailed(referrer_id)
