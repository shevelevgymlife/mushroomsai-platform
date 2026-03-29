from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import jwt, JWTError
from config import settings
from db.database import database
from db.models import users, sessions
from auth.blocked_identities import login_denied_for_user_row_sync
from auth.owner import sync_owner_admin_role
from auth.ui_prefs import attach_screen_rim_prefs
from services.subscription_service import PLANS, check_subscription


def _plan_display_name(plan_key: str | None) -> str:
    k = (plan_key or "free").lower()
    return (PLANS.get(k) or PLANS["free"])["name"]

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30


def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    data = {"sub": str(user_id), "exp": expire}
    return jwt.encode(data, settings.JWT_SECRET, algorithm=ALGORITHM)


async def get_current_user(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        return None

    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return None
    # Always resolve to the primary account
    if row["primary_user_id"]:
        primary = await database.fetch_one(users.select().where(users.c.id == row["primary_user_id"]))
        if primary:
            row = primary
    u = dict(row)
    if login_denied_for_user_row_sync(u):
        return None
    await sync_owner_admin_role(u)
    attach_screen_rim_prefs(u)
    await attach_subscription_effective(u)
    return u


async def attach_subscription_effective(u: dict) -> None:
    """effective_subscription_plan — для шаблонов и paywall; can_claim_start_trial — акция 3 дня."""
    uid = u.get("id")
    if uid is None:
        return
    plan = await check_subscription(uid)
    u["effective_subscription_plan"] = plan
    role = (u.get("role") or "user").lower()
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        u["start_trial_active"] = False
        u["can_claim_start_trial"] = False
        u["drawer_trial_countdown"] = False
        u["start_trial_until_iso"] = None
        u["drawer_sub_head"] = _plan_display_name("free")
        u["drawer_sub_sub"] = "Без ограничения по времени"
        u["drawer_sub_show_countdown"] = False
        u["drawer_sub_until_iso"] = None
        u["drawer_sub_kind"] = "free"
        return
    now = datetime.utcnow()
    tu = row.get("start_trial_until")
    u["start_trial_active"] = bool(tu and tu > now)
    claimed = row.get("start_trial_claimed_at")
    sp = (row.get("subscription_plan") or "free").lower()
    paid_active = bool(
        row.get("subscription_end") and row["subscription_end"] > now and sp in ("start", "pro", "maxi")
    )
    u["can_claim_start_trial"] = role not in ("admin", "moderator") and not claimed and not paid_active

    admin_granted = bool(row.get("subscription_admin_granted"))
    trial_active = bool(tu and tu > now and not paid_active)

    # Единая карточка тарифа в бургере (пробный 3 дня не трогаем в БД — только отображение)
    u["drawer_trial_countdown"] = False
    u["start_trial_until_iso"] = None

    head = _plan_display_name("free")
    sub = "Без ограничения по времени"
    show_cd = False
    until_iso = None
    kind = "free"

    if role in ("admin", "moderator"):
        head = "Администратор" if role == "admin" else "Модератор"
        sub = "Служебный доступ"
        kind = "staff"
    elif paid_active and admin_granted:
        head = f"Подписка «{_plan_display_name(sp)}»"
        sub = "Назначена администратором · срок в меню не отображается"
        kind = "paid_admin"
    elif paid_active:
        head = f"Подписка «{_plan_display_name(sp)}»"
        sub = "Осталось до окончания оплаченного периода"
        se = row.get("subscription_end")
        if se:
            se_utc = se.replace(tzinfo=timezone.utc) if se.tzinfo is None else se.astimezone(timezone.utc)
            until_iso = se_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        show_cd = bool(until_iso)
        kind = "paid_self"
    elif trial_active:
        head = "Пробный «Старт»"
        sub = "Осталось до окончания пробного доступа"
        show_cd = True
        tu_utc = tu.replace(tzinfo=timezone.utc) if tu.tzinfo is None else tu.astimezone(timezone.utc)
        until_iso = tu_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        kind = "trial"
        u["drawer_trial_countdown"] = True
        u["start_trial_until_iso"] = until_iso

    u["drawer_sub_head"] = head
    u["drawer_sub_sub"] = sub
    u["drawer_sub_show_countdown"] = show_cd
    u["drawer_sub_until_iso"] = until_iso
    u["drawer_sub_kind"] = kind


async def get_user_from_request(request) -> Optional[dict]:
    """Один раз за запрос: кэш в request.state (тема/ободок профиля во всех шаблонах)."""
    if getattr(request.state, "_auth_user_resolved", False):
        return getattr(request.state, "_auth_user", None)

    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        request.state._auth_user_resolved = True
        request.state._auth_user = None
        return None

    u = await get_current_user(token)
    request.state._auth_user_resolved = True
    request.state._auth_user = u
    return u
