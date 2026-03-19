from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import JSONResponse
from auth.session import get_user_from_request
from auth.telegram_auth import verify_telegram_auth
from db.database import database
from db.models import users, messages
import urllib.parse

router = APIRouter(prefix="/account")
templates = Jinja2Templates(directory="web/templates")


async def merge_accounts(primary_id: int, secondary_id: int):
    """Transfer all data from secondary account to primary, mark secondary as merged."""
    primary = await database.fetch_one(users.select().where(users.c.id == primary_id))
    secondary = await database.fetch_one(users.select().where(users.c.id == secondary_id))
    if not primary or not secondary:
        return

    # Transfer chat messages
    await database.execute(
        messages.update().where(messages.c.user_id == secondary_id).values(user_id=primary_id)
    )

    # Transfer active subscription if secondary has one and primary is on free plan
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

    # Copy identifiers to primary
    updates = {}
    if secondary["tg_id"] and not primary["tg_id"]:
        updates["tg_id"] = secondary["tg_id"]
        updates["linked_tg_id"] = secondary["tg_id"]
    if secondary["google_id"] and not primary["google_id"]:
        updates["google_id"] = secondary["google_id"]
        updates["linked_google_id"] = secondary["google_id"]
    if updates:
        await database.execute(users.update().where(users.c.id == primary_id).values(**updates))

    # Mark secondary as merged into primary
    await database.execute(
        users.update().where(users.c.id == secondary_id).values(primary_user_id=primary_id)
    )


@router.get("/link-telegram", response_class=HTMLResponse)
async def link_telegram_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    from config import settings
    return templates.TemplateResponse(
        "account/link_telegram.html",
        {"request": request, "user": user, "site_url": settings.SITE_URL,
         "bot_username": settings.TELEGRAM_BOT_USERNAME},
    )


@router.get("/link-telegram-callback")
async def link_telegram_callback(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")

    data = dict(request.query_params)
    if not verify_telegram_auth(data.copy()):
        return RedirectResponse("/dashboard?error=tg_auth_failed")

    tg_id = int(data.get("id"))
    name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    photo = data.get("photo_url", "")

    tg_user = await database.fetch_one(users.select().where(users.c.tg_id == tg_id))

    if tg_user and tg_user["id"] != user["id"]:
        # Existing separate TG account — merge it into current user
        await merge_accounts(primary_id=user["id"], secondary_id=tg_user["id"])
    elif not tg_user:
        # No TG account — link tg_id directly to current user
        await database.execute(
            users.update().where(users.c.id == user["id"]).values(
                tg_id=tg_id, linked_tg_id=tg_id
            )
        )
        # Update name/avatar from Telegram if missing
        if not user.get("avatar") and photo:
            await database.execute(
                users.update().where(users.c.id == user["id"]).values(avatar=photo)
            )
        if not user.get("name") and name:
            await database.execute(
                users.update().where(users.c.id == user["id"]).values(name=name)
            )
    # else: tg_user["id"] == user["id"] → already linked, nothing to do

    return RedirectResponse("/dashboard?linked=telegram")


@router.get("/link-google")
async def link_google(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    from config import settings
    request.session["link_user_id"] = user["id"]
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{settings.SITE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "state": "link",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


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
