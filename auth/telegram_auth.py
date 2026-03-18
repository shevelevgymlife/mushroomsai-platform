import hashlib
import hmac
import time
import json
from typing import Optional
from urllib.parse import unquote
from config import settings


def verify_telegram_auth(data: dict) -> bool:
    """Verify Telegram Login Widget data."""
    check_hash = data.pop("hash", None)
    if not check_hash:
        return False
    auth_date = int(data.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        return False
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(data.items())
    )
    secret_key = hashlib.sha256(settings.TELEGRAM_TOKEN.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_hash, check_hash)


def verify_telegram_miniapp(init_data: str) -> Optional[dict]:
    """Verify Telegram Mini App initData and return user info."""
    try:
        parsed = {}
        for part in init_data.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                parsed[k] = unquote(v)

        check_hash = parsed.pop("hash", None)
        if not check_hash:
            return None

        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            settings.TELEGRAM_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, check_hash):
            return None

        user_data = parsed.get("user")
        if user_data:
            return json.loads(user_data)

        return None
    except Exception:
        return None
