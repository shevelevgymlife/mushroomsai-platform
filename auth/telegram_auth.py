from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from typing import Any

from config import settings
from db.database import database
from db.models import users
from auth.session import create_access_token
from auth.blocked_identities import is_identity_blocked, login_denied_for_user_row_sync
from services.referral_service import generate_referral_code, finalize_web_referral

logger = logging.getLogger(__name__)


def _telegram_webapp_secret_key(bot_token: str) -> bytes:
    # Telegram docs: secret_key = HMAC_SHA256("WebAppData", bot_token)
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def verify_telegram_webapp_init_data(init_data: str) -> dict[str, Any]:
    """
    Verify Telegram WebApp initData signature (same rules as aiogram.utils.web_app).
    Returns parsed initData fields if valid; raises ValueError otherwise.
    """
    if not init_data or not isinstance(init_data, str):
        raise ValueError("initData is empty")

    # parse_qsl + dict — как в aiogram (значения декодированы; так же строится data_check_string)
    try:
        parsed = dict(
            urllib.parse.parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)  # type: ignore[call-arg]
        )
    except (TypeError, ValueError):
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))

    provided_hash = parsed.get("hash") or ""
    if not provided_hash:
        raise ValueError("initData.hash is missing")

    # Поля hash и signature не участвуют в подписи (signature — новый формат для third-party)
    skip_keys = frozenset({"hash", "signature"})
    check_parts: list[str] = []
    for k in sorted(parsed.keys()):
        if k in skip_keys:
            continue
        check_parts.append(f"{k}={parsed[k]}")
    data_check_string = "\n".join(check_parts)

    # Пробуем все токены: TELEGRAM_BOT_TOKEN, TELEGRAM_TOKEN, NOTIFY_BOT_TOKEN.
    # Это нужно если у пользователя несколько ботов с WebApp (подпись делается токеном того бота,
    # через которого открыт WebApp).
    candidates: list[str] = []
    for attr in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "NOTIFY_BOT_TOKEN"):
        t = (getattr(settings, attr, "") or "").strip()
        if t and t not in candidates:
            candidates.append(t)
    if not candidates:
        raise ValueError("TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN is not configured")

    matched = False
    for token in candidates:
        secret_key = _telegram_webapp_secret_key(token)
        computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(computed_hash, provided_hash):
            logger.debug("initData verified with token ending …%s", token[-4:])
            matched = True
            break

    if not matched:
        logger.warning(
            "Telegram WebApp initData HMAC mismatch (check TELEGRAM_BOT_TOKEN matches WebApp bot)"
        )
        raise ValueError("initData signature mismatch")

    # Свежесть initData (как у Login Widget, до 24 ч)
    try:
        auth_date = int(parsed.get("auth_date") or 0)
    except (TypeError, ValueError):
        auth_date = 0
    if auth_date <= 0:
        raise ValueError("initData.auth_date is missing")

    now = time.time()
    # Допуск сдвига часов: auth_date не должна быть «из будущего» дольше чем на 5 мин
    if auth_date - now > 300:
        raise ValueError("initData.auth_date is in the future")
    # Данные не старше 24 ч (как в документации Telegram)
    if now - auth_date > 86400:
        raise ValueError("initData expired (auth_date too old)")

    # Parse `user` JSON if present.
    user_raw = parsed.get("user")
    if user_raw:
        try:
            parsed["user"] = json.loads(user_raw)
        except Exception:
            parsed["user"] = {}

    return parsed


async def telegram_webapp_login(
    init_data: str,
    *,
    request,
    redirect_to: str = "/dashboard",
) -> tuple[int, str, int]:
    """
    Verify initData, create/find user by tg_id, and return:
    - user_id that should be stored in JWT sub
    - redirect destination path
    - tg_id (Telegram user id) for admin / notifications
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
        display_name = full_name or username or "Пользователь"
        await database.execute(
            users.insert().values(
                tg_id=tg_id_int,
                name=display_name,
                referral_code=ref_code,
                # role/subscription_plan use defaults
            )
        )
        row = await database.fetch_one(users.select().where(users.c.tg_id == tg_id_int))
        if not row:
            raise RuntimeError("Failed to create Telegram user")
        user_id = row["primary_user_id"] or row["id"]
        try:
            from services.tg_notify import notify_new_user

            await notify_new_user(int(user_id), display_name, "Telegram WebApp")
        except Exception:
            pass

    # We can finalize referral even for Telegram login (invite_ref cookie may exist).
    # Response cookie is set by caller (auth_routes); finalize uses response object.
    return int(user_id), redirect_to, tg_id_int


async def telegram_finalize_login_cookie(
    *,
    response,
    request,
    user_id: int,
) -> None:
    # Create access token and finalize referrals in one place for consistency.
    token = create_access_token(user_id)
    secure = (settings.SITE_URL or "").lower().startswith("https://")
    # В WebView Telegram часто нужен SameSite=None + Secure, иначе сессия не цепляется после редиректа.
    same_site = "none" if secure else "lax"
    response.set_cookie(
        "access_token",
        token,
        httponly=True,
        max_age=30 * 24 * 3600,
        samesite=same_site,
        secure=secure,
        path="/",
    )
    await finalize_web_referral(request, response, int(user_id))

