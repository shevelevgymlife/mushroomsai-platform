from __future__ import annotations

import base64
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


# Публичные ключи Telegram для поля initData.signature (Ed25519), см. core.telegram.org/bots/webapps
_TELEGRAM_WEBAPP_ED25519_PUBKEYS: tuple[bytes, ...] = (
    bytes.fromhex("e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d"),  # production
    bytes.fromhex("40055058a4ee38156a06562e52eece92a771bcd8346a8c4615cb7376eddf72ec"),  # test
)


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s or "").strip() + pad)


def _bot_ids_from_tokens(tokens: list[str]) -> list[int]:
    out: list[int] = []
    for tok in tokens:
        if ":" not in tok:
            continue
        left = tok.split(":", 1)[0].strip()
        if left.isdigit():
            bid = int(left)
            if bid not in out:
                out.append(bid)
    return out


def _verify_init_data_ed25519(parsed_flat: dict[str, str], signature_b64url: str, bot_ids: list[int]) -> bool:
    """Проверка initData.signature по спецификации «Validating data for Third-Party Use» (Telegram)."""
    if not signature_b64url or not bot_ids:
        return False
    try:
        sig = _b64url_decode(signature_b64url)
    except Exception:
        return False
    if len(sig) != 64:
        return False
    skip = frozenset({"hash", "signature"})
    body = "\n".join(f"{k}={parsed_flat[k]}" for k in sorted(parsed_flat.keys()) if k not in skip)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception as e:
        logger.warning("cryptography Ed25519 unavailable: %s", e)
        return False
    for bot_id in bot_ids:
        payload = f"{bot_id}:WebAppData\n{body}".encode("utf-8")
        for raw_pub in _TELEGRAM_WEBAPP_ED25519_PUBKEYS:
            try:
                pub = Ed25519PublicKey.from_public_bytes(raw_pub)
                pub.verify(sig, payload)
                logger.info("initData OK via Ed25519 (bot_id=%s)", bot_id)
                return True
            except Exception:
                continue
    return False


def _collect_webapp_bot_tokens() -> list[str]:
    candidates: list[str] = []
    for attr in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_TOKEN",
        "NOTIFY_BOT_TOKEN",
        "TRAINING_BOT_TOKEN",
        "CHANNEL_INGEST_BOT_TOKEN",
    ):
        t = (getattr(settings, attr, "") or "").strip()
        if t and t not in candidates:
            candidates.append(t)
    extra = (getattr(settings, "TELEGRAM_WEBAPP_EXTRA_BOT_TOKENS", "") or "").strip()
    for part in extra.split(","):
        t = part.strip()
        if t and t not in candidates:
            candidates.append(t)
    return candidates


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

    provided_hash = (parsed.get("hash") or "").strip()
    sig_field = (parsed.get("signature") or "").strip()
    if not provided_hash and not sig_field:
        raise ValueError("initData.hash and initData.signature are missing")

    # Поля hash и signature не участвуют в подписи (signature — Ed25519 от Telegram)
    skip_keys = frozenset({"hash", "signature"})
    check_parts: list[str] = []
    for k in sorted(parsed.keys()):
        if k in skip_keys:
            continue
        check_parts.append(f"{k}={parsed[k]}")
    data_check_string = "\n".join(check_parts)

    # Подпись считается токеном того бота, через которого открыт Mini App — перебираем все известные токены.
    candidates = _collect_webapp_bot_tokens()
    if not candidates:
        raise ValueError("TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN is not configured")

    matched = False
    if provided_hash:
        for token in candidates:
            secret_key = _telegram_webapp_secret_key(token)
            computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(computed_hash, provided_hash):
                logger.debug("initData HMAC OK (token …%s)", token[-4:])
                matched = True
                break

    if not matched and sig_field:
        # Новые клиенты Telegram: поле signature (Ed25519), data-check-string с префиксом bot_id:WebAppData
        flat: dict[str, str] = {k: str(v) for k, v in parsed.items()}
        bot_ids = _bot_ids_from_tokens(candidates)
        if _verify_init_data_ed25519(flat, sig_field, bot_ids):
            matched = True

    if not matched:
        # Allow bypassing verification via env var for debugging only
        skip_verify = bool(getattr(settings, "TELEGRAM_WEBAPP_SKIP_VERIFY", False))
        if skip_verify:
            logger.warning("SIGNATURE VERIFICATION SKIPPED (TELEGRAM_WEBAPP_SKIP_VERIFY=true) — DEBUG ONLY")
        else:
            logger.warning(
                "Telegram WebApp initData verify failed (HMAC + Ed25519).\n"
                "data_check_string (first 200): %s\n"
                "provided_hash: %s\n"
                "has_signature_field: %s\n"
                "initData (first 200): %s",
                data_check_string[:200],
                provided_hash or "(empty)",
                bool(sig_field),
                init_data[:200],
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


def verify_telegram_auth(data: dict[str, Any]) -> bool:
    """
    Telegram Login Widget verification (web auth via data-auth-url).
    Expects query params dict that contains `hash` and auth fields.
    """
    if not isinstance(data, dict):
        return False
    provided_hash = str(data.pop("hash", "") or "").strip()
    if not provided_hash:
        return False
    try:
        check = []
        for k in sorted(data.keys()):
            v = data[k]
            if v is None:
                continue
            check.append(f"{k}={v}")
        data_check_string = "\n".join(check)
        secret = hashlib.sha256((settings.TELEGRAM_TOKEN or "").encode("utf-8")).digest()
        computed = hmac.new(secret, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, provided_hash):
            return False
        # Optional auth_date freshness check (24h)
        auth_date_raw = data.get("auth_date")
        if auth_date_raw is not None:
            auth_date = int(auth_date_raw)
            if time.time() - auth_date > 86400:
                return False
        return True
    except Exception:
        return False


def verify_telegram_miniapp(init_data: str) -> dict[str, Any] | None:
    """
    Backward-compatible wrapper used by existing auth routes.
    Returns parsed `user` dict on success, otherwise None.
    """
    try:
        parsed = verify_telegram_webapp_init_data(init_data)
        user = parsed.get("user")
        if isinstance(user, dict) and user.get("id") is not None:
            return user
    except Exception:
        return None
    return None


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
        # Имя при повторном входе не трогаем — пользователь задаёт его в профиле.
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

