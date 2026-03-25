"""Временные токены для входа через Telegram-бот (in-memory, живут 10 минут)."""
from __future__ import annotations
import secrets
import time
from typing import Optional

# token -> {"user_id": int, "confirmed": bool, "expires": float}
_TOKENS: dict[str, dict] = {}

_TTL = 600  # 10 минут


def create_token() -> str:
    token = "wl_" + secrets.token_hex(16)
    _TOKENS[token] = {"user_id": None, "confirmed": False, "expires": time.time() + _TTL}
    _cleanup()
    return token


def confirm_token(token: str, user_id: int) -> bool:
    entry = _TOKENS.get(token)
    if not entry or entry["expires"] < time.time():
        return False
    entry["user_id"] = user_id
    entry["confirmed"] = True
    return True


def consume_token(token: str) -> Optional[int]:
    """Возвращает user_id если токен подтверждён, иначе None. Удаляет токен."""
    entry = _TOKENS.pop(token, None)
    if not entry:
        return None
    if not entry["confirmed"] or entry["expires"] < time.time():
        return None
    return entry["user_id"]


def is_pending(token: str) -> bool:
    entry = _TOKENS.get(token)
    return bool(entry and entry["expires"] >= time.time())


def _cleanup():
    now = time.time()
    expired = [k for k, v in _TOKENS.items() if v["expires"] < now]
    for k in expired:
        del _TOKENS[k]
