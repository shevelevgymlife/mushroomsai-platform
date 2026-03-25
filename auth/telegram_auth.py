from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.parse
from typing import Any

from config import settings
from db.database import database
from db.models import users
from auth.session import create_access_token
from auth.blocked_identities import is_identity_blocked, login_denied_for_user_row_sync
from services.referral_service import generate_referral_code, finalize_web_referral

logger = logging.getLogger(__name__)


def _parse_init_data(init_data: str) -> dict[str, str]:
    # initData format: key=value&key2=value2 (values are URL-encoded)
    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    return {k: v for k, v in pairs}


def _telegram_webapp_secret_key(bot_token: str) -> bytes:
    # Telegram docs: secret_key = HMAC_SHA256("WebAppData", bot_token)
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def _check_signature(data_check_string: str, provided_hash: str, bot_token: str) -> bool:
    secret_key = _telegram_webapp_secret_key(bot_token)
    computed = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, provided_hash)


def verify_telegram_webapp_init_data(init_data: str) -> dict[str, Any]:
    """
    Verify Telegram WebApp initData signature.
    Tries all configured bot tokens (main + notify) so the WebApp works
    regardless of which bot the user opened it from.
    Returns parsed initData fields if valid; raises ValueError otherwise.
    """
    if not init_data or not isinstance(init_data, str):
        raise ValueError("initData is empty")

    parsed = _parse_init_data(init_data)
    provided_hash = parsed.get("hash") or ""
    if not provided_hash:
        raise ValueError("initData.hash is missing")

    check_parts = [f"{k}={parsed[k]}" for k in sorted(parsed.keys()) if k != "hash"]
    data_check_string = "\n".join(check_parts)

    # Collect all tokens to try: TELEGRAM_BOT_TOKEN, TELEGRAM_TOKEN, NOTIFY_BOT_TOKEN
    candidates: list[str] = []
    for attr in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "NOTIFY_BOT_TOKEN"):
        t = (getattr(settings, attr, "") or "").strip()
        if t and t not in candidates:
            candidates.append(t)

    if not candidates:
        raise ValueError("Ни один токен бота не настроен (TELEGRAM_TOKEN/TELEGRAM_BOT_TOKEN)")

    for token in candidates:
        if _check_signature(data_check_string, provided_hash, token):
            logger.debug("initData verified with token ending …%s", token[-4:])
            # Parse `user` JSON if present.
            user_raw = parsed.get("user")
            if user_raw:
                try:
                    parsed["user"] = json.loads(user_raw)
                except Exception:
                    parsed["user"] = {}
            return parsed

    raise ValueError("initData signature mismatch")


async def telegram_webapp_login(
    init_data: str,
    *,
    request,
    redirect_to: str = "/dashboard",
) -> tuple[int, str]:
    """
    Verify initData, create/find user by tg_id, and return:
    - user_id that should be stored in JWT sub
    - redirect destination path
    """
    parsed = verify_telegram_webapp_init_data(init_data)
    user = parsed.get("user") or {}
    tg_id = user.get("id")
    if tg_id is None:
        raise ValueError("Telegram user.id is missing in initData")
    tg_id_int = int(tg_id)

    if await is_identity_blocked("tg_id", str(tg_id_int)):
        raise PermissionError("This Telegram identity is blocked")

    username = user.get("username") or ""
    first_name = user.get("first_name") or ""
    last_name = user.get("last_name") or ""
    full_name = (first_name + " " + last_name).strip() or username or None

    # Resolve existing user (tg_id or linked_tg_id).
    row = await database.fetch_one(
        users.select()
        .where((users.c.tg_id == tg_id_int) | (users.c.linked_tg_id == tg_id_int))
    )

    if row:
        if login_denied_for_user_row_sync(dict(row)):
            raise PermissionError("User login is denied")

        user_id = row["primary_user_id"] or row["id"]
        # Best-effort: update name if we have something new.
        if full_name:
            await database.execute(users.update().where(users.c.id == row["id"]).values(name=full_name))
    else:
        ref_code = await generate_referral_code()
        await database.execute(
            users.insert().values(
                tg_id=tg_id_int,
                name=full_name,
                referral_code=ref_code,
                # role/subscription_plan use defaults
            )
        )
        row = await database.fetch_one(users.select().where(users.c.tg_id == tg_id_int))
        if not row:
            raise RuntimeError("Failed to create Telegram user")
        user_id = row["primary_user_id"] or row["id"]

    # We can finalize referral even for Telegram login (invite_ref cookie may exist).
    # Response cookie is set by caller (auth_routes); finalize uses response object.
    return int(user_id), redirect_to


async def telegram_finalize_login_cookie(
    *,
    response,
    request,
    user_id: int,
) -> None:
    # Create access token and finalize referrals in one place for consistency.
    token = create_access_token(user_id)
    response.set_cookie("access_token", token, httponly=True, max_age=30 * 24 * 3600)
    await finalize_web_referral(request, response, int(user_id))

