from passlib.context import CryptContext
from db.database import database
from db.models import users
from typing import Optional

from auth.blocked_identities import is_identity_blocked, login_denied_for_user_row

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


async def authenticate_user(email: str, password: str) -> Optional[dict]:
    row = await database.fetch_one(users.select().where(users.c.email == email))
    if not row:
        return None
    if not row["password_hash"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    u = dict(row)
    if await login_denied_for_user_row(u):
        return None
    return u


async def register_user(email: str, password: str, name: str) -> Optional[dict]:
    import secrets, string

    em = (email or "").strip().lower()
    if await is_identity_blocked("email", em):
        return None

    existing = await database.fetch_one(users.select().where(users.c.email == email))
    if existing:
        return None

    referral_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    password_hash = hash_password(password)

    user_id = await database.execute(
        users.insert().values(
            email=email,
            password_hash=password_hash,
            name=name,
            referral_code=referral_code,
            role="user",
            subscription_plan="free",
            needs_tariff_choice=True,
        )
    )
    return await database.fetch_one(users.select().where(users.c.id == user_id))
