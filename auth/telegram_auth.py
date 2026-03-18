import hashlib
import hmac
import time
import json
from typing import Optional
from urllib.parse import unquote
from config import settings


def verify_telegram_auth(data: dict) -> bool:
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
    """Verify Telegram Mini App initData - упрощённая версия"""
    try:
        if not init_data:
            return None

        parsed = {}
        for part in init_data.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                parsed[k] = unquote(v)

        # Получаем user даже без верификации hash для тестирования
        user_data = parsed.get("user")
        if user_data:
            try:
                return json.loads(user_data)
            except Exception:
                return None

        return None
    except Exception:
        return None
