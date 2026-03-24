import logging
import urllib.parse

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from web.templates_utils import Jinja2Templates
from starlette.responses import JSONResponse
from auth.session import get_user_from_request
from db.database import database
from db.models import users, messages

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
    if updates:
        await database.execute(users.update().where(users.c.id == primary_id).values(**updates))

    await database.execute(
        users.update().where(users.c.id == secondary_id).values(primary_user_id=primary_id)
    )


@router.get("/link-telegram", response_class=HTMLResponse)
async def link_telegram_page(request: Request):
    return RedirectResponse("/dashboard", status_code=302)


@router.get("/link-telegram-callback")
async def link_telegram_callback(request: Request):
    return RedirectResponse("/dashboard", status_code=302)


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


@router.get("/link-google-start")
async def link_google_start(request: Request):
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
