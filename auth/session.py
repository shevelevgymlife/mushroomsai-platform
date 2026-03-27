from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError
from config import settings
from db.database import database
from db.models import users, sessions
from auth.blocked_identities import login_denied_for_user_row_sync
from auth.owner import sync_owner_admin_role
from auth.ui_prefs import attach_screen_rim_prefs
from services.subscription_service import check_subscription

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
