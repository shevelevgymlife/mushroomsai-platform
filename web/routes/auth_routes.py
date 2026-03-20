from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from web.templates_utils import Jinja2Templates
from starlette.responses import JSONResponse
from auth.telegram_auth import verify_telegram_auth, verify_telegram_miniapp
from auth.email_auth import authenticate_user, register_user
from auth.session import create_access_token
from db.database import database
from db.models import users
import secrets
import string
import httpx
import urllib.parse

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    from auth.session import get_user_from_request
    user = await get_user_from_request(request)
    if user:
        return RedirectResponse("/dashboard")
    from config import settings
    tg_bot_id = settings.TELEGRAM_TOKEN.split(":")[0] if ":" in settings.TELEGRAM_TOKEN else ""
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "user": None, "site_url": settings.SITE_URL, "tg_bot_id": tg_bot_id},
    )


@router.post("/login/email")
async def login_email(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
):
    user = await authenticate_user(email, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "user": None, "error": "Неверный email или пароль"},
        )
    token = create_access_token(user["id"])
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("access_token", token, httponly=True, max_age=30 * 24 * 3600)
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
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "user": None, "error": "Email уже зарегистрирован"},
        )
    token = create_access_token(user["id"])
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("access_token", token, httponly=True, max_age=30 * 24 * 3600)
    return resp


@router.get("/auth/telegram")
async def telegram_auth(request: Request):
    data = dict(request.query_params)
    if not verify_telegram_auth(data.copy()):
        return JSONResponse({"error": "Invalid auth"}, status_code=400)

    tg_id = int(data.get("id"))
    name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    photo = data.get("photo_url", "")

    row = await database.fetch_one(users.select().where(users.c.tg_id == tg_id))
    if row:
        user_id = row["primary_user_id"] or row["id"]
        await database.execute(
            users.update().where(users.c.tg_id == tg_id).values(name=name, avatar=photo)
        )
    else:
        ref_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        user_id = await database.execute(
            users.insert().values(tg_id=tg_id, name=name, avatar=photo, referral_code=ref_code)
        )

    token = create_access_token(user_id)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("access_token", token, httponly=True, max_age=30 * 24 * 3600)
    return resp


@router.post("/auth/telegram/miniapp")
async def telegram_miniapp_auth(request: Request):
    try:
        body = await request.json()
        init_data = body.get("init_data", "")

        user_data = verify_telegram_miniapp(init_data)
        if not user_data:
            return JSONResponse({"error": "Invalid Telegram data"}, status_code=400)

        tg_id = int(user_data.get("id"))
        name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
        avatar = user_data.get("photo_url", "")

        row = await database.fetch_one(users.select().where(users.c.tg_id == tg_id))
        if row:
            user_id = row["primary_user_id"] or row["id"]
            await database.execute(
                users.update().where(users.c.tg_id == tg_id).values(name=name, avatar=avatar)
            )
        else:
            ref_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            user_id = await database.execute(
                users.insert().values(tg_id=tg_id, name=name, avatar=avatar, referral_code=ref_code)
            )

        token = create_access_token(user_id)
        resp = JSONResponse({"redirect": "/dashboard"})
        resp.set_cookie("access_token", token, httponly=True, max_age=30 * 24 * 3600)
        return resp

    except Exception as e:
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

        # Check if this is an account-linking request
        link_user_id = request.session.pop("link_user_id", None)
        state = request.query_params.get("state", "")
        if link_user_id and state == "link":
            from web.routes.account import merge_accounts
            existing_google_user = await database.fetch_one(
                users.select().where(users.c.google_id == google_id)
            )
            if existing_google_user and existing_google_user["id"] != link_user_id:
                # Separate Google account exists — merge into current user
                await merge_accounts(primary_id=link_user_id, secondary_id=existing_google_user["id"])
            elif not existing_google_user:
                # Link google_id directly to current user
                await database.execute(
                    users.update().where(users.c.id == link_user_id).values(
                        google_id=google_id,
                        linked_google_id=google_id,
                        email=email,
                    )
                )
            token_str = create_access_token(link_user_id)
            resp = RedirectResponse("/dashboard?linked=google", status_code=302)
            resp.set_cookie("access_token", token_str, httponly=True, max_age=30 * 24 * 3600)
            return resp

        row = await database.fetch_one(users.select().where(users.c.google_id == google_id))
        if row:
            user_id = row["primary_user_id"] or row["id"]
            await database.execute(
                users.update().where(users.c.google_id == google_id).values(name=name, avatar=avatar)
            )
        else:
            ref_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            user_id = await database.execute(
                users.insert().values(
                    google_id=google_id, email=email, name=name, avatar=avatar, referral_code=ref_code
                )
            )

        token_str = create_access_token(user_id)
        resp = RedirectResponse("/dashboard", status_code=302)
        resp.set_cookie("access_token", token_str, httponly=True, max_age=30 * 24 * 3600)
        return resp

    except Exception as e:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "user": None, "error": f"Ошибка входа: {str(e)}"},
        )


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("access_token")
    return resp
