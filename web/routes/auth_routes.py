from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from web.templates_utils import Jinja2Templates
from starlette.responses import JSONResponse
from auth.telegram_auth import verify_telegram_auth, verify_telegram_miniapp
from auth.email_auth import authenticate_user, register_user
from auth.session import create_access_token
from db.database import database
from db.models import users
from services.referral_service import attach_invite_ref_from_query, finalize_web_referral
from auth.blocked_identities import is_identity_blocked, login_denied_for_user_row
import secrets
import string
import httpx
import urllib.parse
import logging
from services.ops_alerts import notify_security_event

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")
_logger = logging.getLogger(__name__)


async def _find_user_by_tg_login_id(tg_id: int):
    rows = await database.fetch_all(
        users.select().where(
            (users.c.tg_id == tg_id) | (users.c.linked_tg_id == tg_id)
        )
    )
    if not rows:
        return None
    chosen = None
    for row in rows:
        r = dict(row)
        if not r.get("primary_user_id"):
            chosen = r
            break
    if chosen is None:
        chosen = dict(rows[0])
    if chosen.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == chosen["primary_user_id"]))
        if primary:
            chosen = dict(primary)
    return chosen


async def _find_user_by_google_login_id(google_id: str):
    rows = await database.fetch_all(
        users.select().where(
            (users.c.google_id == google_id) | (users.c.linked_google_id == google_id)
        )
    )
    if not rows:
        return None
    chosen = None
    for row in rows:
        r = dict(row)
        if not r.get("primary_user_id"):
            chosen = r
            break
    if chosen is None:
        chosen = dict(rows[0])
    if chosen.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == chosen["primary_user_id"]))
        if primary:
            chosen = dict(primary)
    return chosen


def _safe_next_path(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s.startswith("/") or s.startswith("//"):
        return None
    return s.split("?")[0][:512]


def _pop_login_redirect(request: Request) -> str:
    return _safe_next_path(request.session.pop("login_next", None)) or "/dashboard"


async def _login_blocked_response(request: Request):
    from config import settings as _s

    _tg_bot_username = (_s.TELEGRAM_BOT_USERNAME or "").strip() or "mushrooms_ai_bot"
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "user": None,
            "error": "Этот аккаунт заблокирован администратором.",
            "site_url": _s.SITE_URL,
            "tg_bot_username": _tg_bot_username,
        },
        status_code=403,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    from auth.session import get_user_from_request
    user = await get_user_from_request(request)
    nxt_param = _safe_next_path(request.query_params.get("next"))
    if nxt_param:
        request.session["login_next"] = nxt_param
    elif "next" not in request.query_params and "login_next" in request.session:
        del request.session["login_next"]

    if user:
        go = nxt_param or _safe_next_path(request.session.pop("login_next", None)) or "/dashboard"
        return RedirectResponse(go, status_code=302)

    from config import settings
    tg_bot_username = (settings.TELEGRAM_BOT_USERNAME or "").strip() or "mushrooms_ai_bot"
    response = templates.TemplateResponse(
        "login.html",
        {"request": request, "user": None, "site_url": settings.SITE_URL, "tg_bot_username": tg_bot_username},
    )
    attach_invite_ref_from_query(request, response)
    return response


@router.post("/login/email")
async def login_email(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    user = await authenticate_user(email, password)
    if not user:
        try:
            safe_email = (email or "").strip()[:120]
            await notify_security_event(
                event="Неуспешный вход по email",
                details=f"Email: {safe_email or '—'}",
            )
        except Exception:
            pass
        from config import settings as _s
        _tg_bot_username = (_s.TELEGRAM_BOT_USERNAME or "").strip() or "mushrooms_ai_bot"
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "user": None,
                "error": "Неверный email или пароль",
                "site_url": _s.SITE_URL,
                "tg_bot_username": _tg_bot_username,
            },
        )
    token = create_access_token(user["id"])
    dest = _pop_login_redirect(request)
    resp = RedirectResponse(dest, status_code=302)
    resp.set_cookie("access_token", token, httponly=True, max_age=30 * 24 * 3600)
    await finalize_web_referral(request, resp, user["id"])
    return resp


@router.post("/register/email")
async def register_email(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
):
    user = await register_user(email, password, name)
    if not user:
        from config import settings as _s
        _tg_bot_username = (_s.TELEGRAM_BOT_USERNAME or "").strip() or "mushrooms_ai_bot"
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "user": None,
                "error": "Email уже зарегистрирован",
                "site_url": _s.SITE_URL,
                "tg_bot_username": _tg_bot_username,
            },
        )
    token = create_access_token(user["id"])
    dest = _pop_login_redirect(request)
    resp = RedirectResponse(dest, status_code=302)
    resp.set_cookie("access_token", token, httponly=True, max_age=30 * 24 * 3600)
    await finalize_web_referral(request, resp, user["id"])
    return resp


@router.get("/auth/telegram")
async def telegram_auth(request: Request):
    data = dict(request.query_params)
    if not verify_telegram_auth(data.copy()):
        try:
            await notify_security_event(
                event="Невалидный Telegram auth payload",
                details=f"IP: {request.client.host if request.client else '—'}",
            )
        except Exception:
            pass
        return JSONResponse({"error": "Invalid auth"}, status_code=400)

    tg_id = int(data.get("id"))
    name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    photo = data.get("photo_url", "")

    if await is_identity_blocked("tg_id", str(tg_id)):
        return await _login_blocked_response(request)

    from auth.avatar_policy import is_server_uploaded_avatar

    row = await _find_user_by_tg_login_id(tg_id)
    if row:
        if await login_denied_for_user_row(dict(row)):
            return await _login_blocked_response(request)
        user_id = row["primary_user_id"] or row["id"]
        vals = {"name": name}
        if not is_server_uploaded_avatar(row.get("avatar")) and photo:
            vals["avatar"] = photo
        existing_tg = row.get("tg_id")
        if not existing_tg or int(existing_tg) == int(tg_id):
            vals["tg_id"] = tg_id
        vals["linked_tg_id"] = tg_id
        await database.execute(users.update().where(users.c.id == user_id).values(**vals))
    else:
        ref_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        user_id = await database.execute(
            users.insert().values(
                tg_id=tg_id,
                linked_tg_id=tg_id,
                name=name,
                avatar=photo,
                referral_code=ref_code,
            )
        )

    token = create_access_token(user_id)
    dest = _pop_login_redirect(request)
    resp = RedirectResponse(dest, status_code=302)
    resp.set_cookie("access_token", token, httponly=True, max_age=30 * 24 * 3600)
    await finalize_web_referral(request, resp, int(user_id))
    return resp


@router.post("/auth/telegram/miniapp")
async def telegram_miniapp_auth(request: Request):
    try:
        body = await request.json()
        init_data = body.get("init_data", "")

        user_data = verify_telegram_miniapp(init_data)
        if not user_data:
            try:
                await notify_security_event(
                    event="Невалидный Telegram MiniApp auth",
                    details=f"IP: {request.client.host if request.client else '—'}",
                )
            except Exception:
                pass
            return JSONResponse({"error": "Invalid Telegram data"}, status_code=400)

        tg_id = int(user_data.get("id"))
        name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
        avatar = user_data.get("photo_url", "")

        if await is_identity_blocked("tg_id", str(tg_id)):
            return JSONResponse({"error": "Аккаунт заблокирован"}, status_code=403)

        from auth.avatar_policy import is_server_uploaded_avatar

        row = await _find_user_by_tg_login_id(tg_id)
        if row:
            if await login_denied_for_user_row(dict(row)):
                return JSONResponse({"error": "Аккаунт заблокирован"}, status_code=403)
            user_id = row["primary_user_id"] or row["id"]
            vals = {"name": name}
            if not is_server_uploaded_avatar(row.get("avatar")) and avatar:
                vals["avatar"] = avatar
            existing_tg = row.get("tg_id")
            if not existing_tg or int(existing_tg) == int(tg_id):
                vals["tg_id"] = tg_id
            vals["linked_tg_id"] = tg_id
            await database.execute(users.update().where(users.c.id == user_id).values(**vals))
        else:
            ref_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            user_id = await database.execute(
                users.insert().values(
                    tg_id=tg_id,
                    linked_tg_id=tg_id,
                    name=name,
                    avatar=avatar,
                    referral_code=ref_code,
                )
            )

        token = create_access_token(user_id)
        # Mini App: always open single in-app dashboard view.
        request.session.pop("login_next", None)
        dest = "/dashboard#feed"
        resp = JSONResponse({"redirect": dest})
        resp.set_cookie("access_token", token, httponly=True, max_age=30 * 24 * 3600)
        await finalize_web_referral(request, resp, int(user_id))
        return resp

    except Exception as e:
        _logger.warning("telegram_miniapp_auth error: %s", e)
        try:
            await notify_security_event(
                event="Ошибка авторизации Telegram MiniApp",
                details=str(e)[:600],
            )
        except Exception:
            pass
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/auth/google")
async def google_login(request: Request):
    from config import settings
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{settings.SITE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@router.get("/auth/google/callback")
async def google_callback(request: Request):
    try:
        from config import settings
        code = request.query_params.get("code")
        if not code:
            raise Exception("Нет кода авторизации")

        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": f"{settings.SITE_URL}/auth/google/callback",
                    "grant_type": "authorization_code",
                }
            )
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise Exception(f"Не удалось получить токен: {token_data}")

            user_resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            user_info = user_resp.json()

        google_id = str(user_info.get("sub", ""))
        email = user_info.get("email", "")
        name = user_info.get("name", "")
        avatar = user_info.get("picture", "")

        if google_id and await is_identity_blocked("google_id", google_id):
            return await _login_blocked_response(request)
        if email and await is_identity_blocked("email", email.strip().lower()):
            return await _login_blocked_response(request)

        # Check if this is an account-linking request
        link_user_id = request.session.pop("link_user_id", None)
        state = request.query_params.get("state", "")
        if link_user_id and state == "link":
            from web.routes.account import attach_google_login
            ok, _msg = await attach_google_login(
                primary_user_id=int(link_user_id),
                google_id=google_id,
                email=email,
                name=name,
                avatar=avatar,
            )
            if not ok:
                return RedirectResponse("/dashboard?error=google_link_conflict", status_code=302)
            token_str = create_access_token(link_user_id)
            resp = RedirectResponse("/dashboard?linked=google", status_code=302)
            resp.set_cookie("access_token", token_str, httponly=True, max_age=30 * 24 * 3600)
            return resp

        from auth.avatar_policy import is_server_uploaded_avatar

        row = await _find_user_by_google_login_id(google_id)
        if row:
            if await login_denied_for_user_row(dict(row)):
                return await _login_blocked_response(request)
            user_id = row["primary_user_id"] or row["id"]
            vals = {"name": name}
            if not is_server_uploaded_avatar(row.get("avatar")) and avatar:
                vals["avatar"] = avatar
            existing_google = (row.get("google_id") or "").strip()
            if not existing_google or existing_google == google_id:
                vals["google_id"] = google_id
            vals["linked_google_id"] = google_id
            if email and not row.get("email"):
                vals["email"] = email
            await database.execute(users.update().where(users.c.id == user_id).values(**vals))
        else:
            ref_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            user_id = await database.execute(
                users.insert().values(
                    google_id=google_id,
                    linked_google_id=google_id,
                    email=email,
                    name=name,
                    avatar=avatar,
                    referral_code=ref_code,
                )
            )

        token_str = create_access_token(user_id)
        dest = _pop_login_redirect(request)
        resp = RedirectResponse(dest, status_code=302)
        resp.set_cookie("access_token", token_str, httponly=True, max_age=30 * 24 * 3600)
        await finalize_web_referral(request, resp, int(user_id))
        return resp

    except Exception as e:
        _logger.warning("google_callback error: %s", e)
        try:
            await notify_security_event(
                event="Ошибка входа через Google",
                details=str(e)[:600],
            )
        except Exception:
            pass
        from config import settings as _s
        _tg_bot_username = (_s.TELEGRAM_BOT_USERNAME or "").strip() or "mushrooms_ai_bot"
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "user": None,
                "error": f"Ошибка входа: {str(e)}",
                "site_url": _s.SITE_URL,
                "tg_bot_username": _tg_bot_username,
            },
        )


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("access_token")
    return resp
