from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import jwt, JWTError
from config import settings
from db.database import database
from db.models import users, sessions
from auth.blocked_identities import login_denied_for_user_row_sync
from auth.owner import sync_owner_admin_role
from auth.ui_prefs import attach_screen_rim_prefs
from services.payment_plans_catalog import (
    ACCESS_TIERS,
    drawer_menu_effective,
    get_effective_plans,
    plan_drawer_lines,
)
from services.subscription_service import (
    check_subscription,
    paid_subscription_for_referral_program,
    user_ineligible_for_start_trial_offer,
)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30


def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    data = {"sub": str(user_id), "exp": expire}
    return jwt.encode(data, settings.JWT_SECRET, algorithm=ALGORITHM)


async def _resolve_primary_user_row(user_id: int) -> Optional[dict]:
    """Дойти до корня цепочки primary_user_id (как web.routes.account._resolve_primary_row)."""
    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not row:
        return None
    resolved = dict(row)
    seen: set[int] = set()
    while resolved.get("primary_user_id"):
        pid = int(resolved["primary_user_id"])
        if pid in seen:
            break
        seen.add(pid)
        p = await database.fetch_one(users.select().where(users.c.id == pid))
        if not p:
            break
        resolved = dict(p)
    return resolved


async def get_current_user(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        return None

    row = await _resolve_primary_user_row(user_id)
    if not row:
        return None
    u = dict(row)
    if login_denied_for_user_row_sync(u):
        return None
    await sync_owner_admin_role(u)
    attach_screen_rim_prefs(u)
    await attach_subscription_effective(u)
    from services.referral_shop_prefs import attach_referral_shop_context

    await attach_referral_shop_context(u)
    return u


async def attach_subscription_effective(u: dict) -> None:
    """effective_subscription_plan — для шаблонов и paywall; can_claim_start_trial — акция 3 дня (один раз навсегда)."""
    uid = u.get("id")
    if uid is None:
        return
    plan = await check_subscription(uid)
    u["effective_subscription_plan"] = plan
    role = (u.get("role") or "user").lower()
    plans = await get_effective_plans()

    def _pname(key: str | None) -> str:
        k = (key or "free").lower()
        return str((plans.get(k) or plans["free"]).get("name") or k)

    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        u["start_trial_active"] = False
        u["can_claim_start_trial"] = False
        u["drawer_trial_countdown"] = False
        u["start_trial_until_iso"] = None
        u["drawer_sub_head"] = _pname("free")
        u["drawer_sub_sub"] = "Без ограничения по времени"
        u["drawer_sub_show_countdown"] = False
        u["drawer_sub_until_iso"] = None
        u["drawer_sub_kind"] = "free"
        u["drawer_plan_bullets"] = plan_drawer_lines(plans.get("free"))
        u["plan_drawer_menu"] = drawer_menu_effective(plans.get("free"))
        u["plan_access_tier"] = "free"
        u["referral_program_unlocked"] = False
        return
    now = datetime.utcnow()
    tu = row.get("start_trial_until")
    u["start_trial_active"] = bool(tu and tu > now)
    sp = (row.get("subscription_plan") or "free").lower()
    admin_granted = bool(row.get("subscription_admin_granted"))
    sub_end = row.get("subscription_end")
    paid_life = bool(row.get("subscription_paid_lifetime"))
    paid_active = sp != "free" and (
        paid_life
        or (sub_end and sub_end > now)
        or (admin_granted and sub_end is None)
    )
    try:
        trial_offer_used = await user_ineligible_for_start_trial_offer(int(uid))
    except Exception:
        trial_offer_used = True
    u["can_claim_start_trial"] = role not in ("admin", "moderator") and not trial_offer_used

    trial_active = bool(tu and tu > now and not paid_active)

    # Единая карточка тарифа в бургере (пробный 3 дня не трогаем в БД — только отображение)
    u["drawer_trial_countdown"] = False
    u["start_trial_until_iso"] = None

    head = _pname("free")
    sub = "Без ограничения по времени"
    show_cd = False
    until_iso = None
    kind = "free"

    if role in ("admin", "moderator"):
        head = "Администратор" if role == "admin" else "Модератор"
        sub = "Служебный доступ"
        kind = "staff"
    elif paid_active and admin_granted:
        head = f"Подписка «{_pname(sp)}»"
        if sub_end is None:
            sub = "Бессрочно · назначено администратором"
            show_cd = False
            until_iso = None
        else:
            sub = "Назначено администратором · остаток до окончания периода"
            se_utc = sub_end.replace(tzinfo=timezone.utc) if sub_end.tzinfo is None else sub_end.astimezone(timezone.utc)
            until_iso = se_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            show_cd = bool(until_iso)
        kind = "paid_admin"
    elif paid_active:
        head = f"Подписка «{_pname(sp)}»"
        if paid_life and not admin_granted:
            sub = "Без ограничения по времени"
            show_cd = False
            until_iso = None
        else:
            sub = "Осталось до окончания оплаченного периода"
            se = row.get("subscription_end")
            if se:
                se_utc = se.replace(tzinfo=timezone.utc) if se.tzinfo is None else se.astimezone(timezone.utc)
                until_iso = se_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            show_cd = bool(until_iso)
        kind = "paid_self"
    elif trial_active:
        head = f"Пробный «{_pname('start')}»"
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

    def _norm_tier(raw: str | None) -> str:
        t = (raw or "free").strip().lower()
        return t if t in ACCESS_TIERS else "free"

    if role in ("admin", "moderator"):
        u["plan_access_tier"] = "maxi"
    elif trial_active and not paid_active:
        u["plan_access_tier"] = _norm_tier(str((plans.get("start") or {}).get("access_tier") or "start"))
    elif paid_active:
        u["plan_access_tier"] = _norm_tier(str((plans.get(sp) or {}).get("access_tier") or ("start" if sp != "free" else "free")))
    else:
        u["plan_access_tier"] = _norm_tier(str((plans.get("free") or {}).get("access_tier") or "free"))

    if role in ("admin", "moderator"):
        u["drawer_plan_bullets"] = []
        u["plan_drawer_menu"] = None
    elif trial_active:
        u["drawer_plan_bullets"] = plan_drawer_lines(plans.get("start") or plans["free"])
        u["plan_drawer_menu"] = drawer_menu_effective(plans.get("start") or plans["free"])
    elif paid_active:
        u["drawer_plan_bullets"] = plan_drawer_lines(plans.get(sp) or plans["free"])
        u["plan_drawer_menu"] = drawer_menu_effective(plans.get(sp) or plans["free"])
    else:
        u["drawer_plan_bullets"] = plan_drawer_lines(plans.get("free"))
        u["plan_drawer_menu"] = drawer_menu_effective(plans.get("free"))

    try:
        u["referral_program_unlocked"] = await paid_subscription_for_referral_program(int(uid))
    except Exception:
        u["referral_program_unlocked"] = False


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
