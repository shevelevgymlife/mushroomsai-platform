"""Подписанный OAuth state для привязки Google без cookie-сессии (Telegram Mini App → внешний браузер)."""
import hashlib
import hmac
import time

from config import settings


def _secret() -> bytes:
    raw = (settings.JWT_SECRET or "change-me-in-production") + "|" + (settings.GOOGLE_CLIENT_SECRET or "")
    return hashlib.sha256(raw.encode()).digest()


def sign_google_link_user_id(user_id: int) -> str:
    ts = int(time.time())
    base = f"{user_id}.{ts}"
    sig = hmac.new(_secret(), base.encode(), hashlib.sha256).hexdigest()[:24]
    return f"lg1.{base}.{sig}"


def verify_google_link_state(state: str) -> int | None:
    if not state or not state.startswith("lg1."):
        return None
    parts = state.split(".", 3)
    if len(parts) != 4 or parts[0] != "lg1":
        return None
    try:
        uid = int(parts[1])
        ts = int(parts[2])
    except ValueError:
        return None
    sig = parts[3]
    base = f"{uid}.{ts}"
    expected = hmac.new(_secret(), base.encode(), hashlib.sha256).hexdigest()[:24]
    if not hmac.compare_digest(sig, expected):
        return None
    if abs(int(time.time()) - ts) > 900:
        return None
    return uid
