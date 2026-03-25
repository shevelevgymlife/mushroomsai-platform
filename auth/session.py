from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError
from config import settings
from db.database import database
from db.models import users, sessions
from auth.blocked_identities import login_denied_for_user_row_sync
from auth.owner import sync_owner_admin_role

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
    return u


async def get_user_from_request(request) -> Optional[dict]:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return None
    return await get_current_user(token)
