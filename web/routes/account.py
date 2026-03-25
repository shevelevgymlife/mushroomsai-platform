import json
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from web.templates_utils import Jinja2Templates
from starlette.responses import JSONResponse
from auth.session import get_user_from_request
from auth.ui_prefs import DEFAULT_SCREEN_RIM, attach_screen_rim_prefs
from db.database import database
from db.models import users, messages
from services.user_permanent_delete import (
    permanently_delete_user,
    is_protected_super_admin,
)

DELETE_ACCOUNT_CONFIRM = "DELETE_MY_ACCOUNT"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account")
templates = Jinja2Templates(directory="web/templates")


async def merge_accounts(primary_id: int, secondary_id: int):
    """Transfer all data from secondary account to primary, mark secondary as merged."""
    primary = await database.fetch_one(users.select().where(users.c.id == primary_id))
    secondary = await database.fetch_one(users.select().where(users.c.id == secondary_id))
    if not primary or not secondary:
        return

    await database.execute(
        messages.update().where(messages.c.user_id == secondary_id).values(user_id=primary_id)
    )

    if (
        secondary["subscription_plan"] != "free"
        and primary["subscription_plan"] == "free"
    ):
        await database.execute(
            users.update().where(users.c.id == primary_id).values(
                subscription_plan=secondary["subscription_plan"],
                subscription_end=secondary["subscription_end"],
            )
        )

    updates = {}
    if secondary["tg_id"] and not primary["tg_id"]:
        updates["tg_id"] = secondary["tg_id"]
        updates["linked_tg_id"] = secondary["tg_id"]
    if secondary["google_id"] and not primary["google_id"]:
        updates["google_id"] = secondary["google_id"]
        updates["linked_google_id"] = secondary["google_id"]
    if secondary.get("email") and not primary.get("email"):
        updates["email"] = secondary["email"]
    if updates:
        await database.execute(users.update().where(users.c.id == primary_id).values(**updates))

    await database.execute(
        users.update().where(users.c.id == secondary_id).values(primary_user_id=primary_id)
    )


@router.get("/link", response_class=HTMLResponse)
async def link_account_page(request: Request):
    """Страница привязки аккаунтов — показывает нужные варианты в зависимости от способа входа."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    from config import settings
    return templates.TemplateResponse(
        "account/link_account.html",
        {"request": request, "user": user, "site_url": settings.SITE_URL,
         "bot_username": settings.TELEGRAM_BOT_USERNAME},
    )


@router.post("/link-telegram-start")
async def link_telegram_start(request: Request):
    """Генерирует deeplink-токен для привязки Telegram через бот."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from config import settings
    if not settings.TELEGRAM_BOT_USERNAME:
        return JSONResponse({"error": "bot_not_configured"}, status_code=503)

    token = "lt_" + secrets.token_hex(16)
    expires = datetime.utcnow() + timedelta(minutes=15)
    await database.execute(
        users.update().where(users.c.id == user["id"]).values(
            link_token=token, link_token_expires=expires
        )
    )
    deeplink = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}"
    return JSONResponse({"ok": True, "deeplink": deeplink, "expires_in": 900})


@router.get("/check-link-status")
async def check_link_status(request: Request):
    """Проверяет, привязан ли Telegram к аккаунту (для polling с фронтенда)."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    row = await database.fetch_one(users.select().where(users.c.id == user["id"]))
    if row and row["tg_id"]:
        return JSONResponse({"linked": True})
    return JSONResponse({"linked": False})


@router.get("/link-telegram", response_class=HTMLResponse)
async def link_telegram_page(request: Request):
    return RedirectResponse("/account/link")


@router.get("/link-telegram-callback")
async def link_telegram_callback(request: Request):
    return RedirectResponse("/dashboard")


@router.get("/link-google", response_class=HTMLResponse)
async def link_google_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    from config import settings

    return templates.TemplateResponse(
        "account/link_google.html",
        {"request": request, "user": user, "site_url": settings.SITE_URL},
    )


@router.get("/link-google-url")
async def link_google_url(request: Request):
    """Возвращает Google OAuth URL как JSON — для AJAX из Telegram Mini App."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from config import settings
    from auth.google_link_state import sign_google_link_user_id

    # Сессия для браузера; подписанный state — для внешнего браузера (без cookie Mini App).
    request.session["link_user_id"] = user["id"]
    signed = sign_google_link_user_id(user["id"])
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{settings.SITE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "state": signed,
    }
    import urllib.parse as _up
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + _up.urlencode(params)
    return JSONResponse({"ok": True, "url": url})


@router.get("/check-google-link-status")
async def check_google_link_status(request: Request):
    """Polling: привязан ли Google к текущему аккаунту."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    row = await database.fetch_one(users.select().where(users.c.id == user["id"]))
    if row and (row["google_id"] or row["linked_google_id"]):
        return JSONResponse({"linked": True, "email": row.get("email") or ""})
    return JSONResponse({"linked": False})


@router.get("/link-google-start")
async def link_google_start(request: Request):
    """Редирект на Google OAuth для привязки (обычный браузер)."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    from config import settings
    from auth.google_link_state import sign_google_link_user_id

    request.session["link_user_id"] = user["id"]
    state = sign_google_link_user_id(user["id"])
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{settings.SITE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "state": state,
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@router.post("/sync-history")
async def sync_history(request: Request):
    """Transfer messages from all secondary (linked) accounts to the current primary account."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    primary_id = user["id"]

    secondary_accounts = await database.fetch_all(
        users.select().where(users.c.primary_user_id == primary_id)
    )

    if not secondary_accounts:
        return JSONResponse({"ok": True, "transferred": 0, "secondaries": 0})

    total_transferred = 0
    for secondary in secondary_accounts:
        secondary_id = secondary["id"]
        rowcount = await database.execute(
            messages.update()
            .where(messages.c.user_id == secondary_id)
            .values(user_id=primary_id)
        )
        total_transferred += rowcount or 0

    return JSONResponse({
        "ok": True,
        "transferred": total_transferred,
        "secondaries": len(secondary_accounts),
    })


@router.post("/merge")
async def manual_merge(
    request: Request,
    primary_id: int = Form(...),
    secondary_id: int = Form(...),
):
    user = await get_user_from_request(request)
    if not user or user["role"] != "admin":
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if primary_id == secondary_id:
        return JSONResponse({"error": "Same account"}, status_code=400)
    await merge_accounts(primary_id=primary_id, secondary_id=secondary_id)
    return JSONResponse({"ok": True, "merged": secondary_id, "into": primary_id})


@router.post("/delete-my-account")
async def delete_my_account(request: Request):
    """Безвозвратное удаление текущего пользователя (не админа). Привязанные вторичные аккаунты к этому primary тоже удаляются."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if (user.get("role") or "") == "admin":
        return JSONResponse(
            {"error": "admin", "message": "Аккаунт администратора можно удалить только через другого админа."},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if (body.get("confirm") or "").strip() != DELETE_ACCOUNT_CONFIRM:
        return JSONResponse({"error": "confirm_required"}, status_code=400)

    row = await database.fetch_one(users.select().where(users.c.id == user["id"]))
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if is_protected_super_admin(dict(row)):
        return JSONResponse({"error": "protected"}, status_code=403)

    uid = int(user["id"])
    secondaries = await database.fetch_all(users.select().where(users.c.primary_user_id == uid))
    for s in secondaries:
        ok_sub, err_sub = await permanently_delete_user(int(s["id"]))
        if not ok_sub:
            logger.error("delete_my_account secondary failed id=%s: %s", s["id"], err_sub)
            return JSONResponse(
                {"error": "delete_failed", "detail": err_sub or "secondary"},
                status_code=500,
            )

    ok, err = await permanently_delete_user(uid)
    if not ok:
        return JSONResponse({"error": "delete_failed", "detail": err or "unknown"}, status_code=500)

    request.session.clear()
    return JSONResponse({"ok": True, "redirect": "/"})


@router.get("/screen-rim", response_class=HTMLResponse)
async def screen_rim_settings_page(request: Request):
    """Выбор цвета и яркости подсветки по периметру экрана (как палитра в Tilda)."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/screen-rim", status_code=302)
    from config import settings

    return templates.TemplateResponse(
        "account/screen_rim.html",
        {
            "request": request,
            "user": user,
            "site_url": settings.SITE_URL,
        },
    )


@router.post("/screen-rim")
async def screen_rim_save(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    cur = dict(user.get("screen_rim") or DEFAULT_SCREEN_RIM)
    if "on" in body:
        cur["on"] = bool(body["on"])
    for k in ("r", "g", "b"):
        if k in body:
            cur[k] = max(0, min(255, int(body[k])))
    if "s" in body:
        cur["s"] = max(0.05, min(1.0, float(body["s"])))
    payload = json.dumps(cur, separators=(",", ":"))
    await database.execute(
        users.update().where(users.c.id == user["id"]).values(screen_rim_json=payload)
    )
    attach_screen_rim_prefs(user)
    user["screen_rim"] = cur
    user["screen_rim_json"] = payload
    return JSONResponse({"ok": True, "screen_rim": cur})
