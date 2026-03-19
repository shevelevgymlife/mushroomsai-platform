import logging
import urllib.parse

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import JSONResponse
from auth.session import get_user_from_request
from auth.telegram_auth import verify_telegram_auth
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
        logger.warning("link-telegram-callback: no session user, redirecting to login")
        return RedirectResponse("/login")

    logger.info(
        "link-telegram-callback: user_id=%s email=%s tg_id_before=%s params=%s",
        user["id"], user.get("email"), user.get("tg_id"), dict(request.query_params),
    )

    try:
        data = dict(request.query_params)

        # verify_telegram_auth mutates data (pops 'hash'), so pass a copy
        data_for_verify = data.copy()
        auth_ok = verify_telegram_auth(data_for_verify)
        logger.info("Telegram auth verification result: %s for user_id=%s", auth_ok, user["id"])
        if not auth_ok:
            logger.warning("Telegram auth verification failed for user_id=%s data=%s", user["id"], data)
            return RedirectResponse("/dashboard?error=tg_auth_failed")

        raw_id = data.get("id")
        if not raw_id:
            logger.error("Missing 'id' in Telegram callback params: %s", data)
            return RedirectResponse("/dashboard?error=tg_auth_failed")

        tg_id = int(raw_id)
        name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        photo = data.get("photo_url", "")
        logger.info("Parsed tg_id=%s name=%r photo=%r for user_id=%s", tg_id, name, photo, user["id"])

        # Check if this tg_id already belongs to another account
        tg_user = await database.fetch_one(users.select().where(users.c.tg_id == tg_id))
        logger.info(
            "Existing tg_user for tg_id=%s: %s",
            tg_id, {"id": tg_user["id"], "email": tg_user.get("email")} if tg_user else None,
        )

        if tg_user and tg_user["id"] != user["id"]:
            # Existing separate TG account — merge it into current user
            logger.info(
                "Merging accounts: primary_id=%s, secondary_id=%s (tg_id=%s)",
                user["id"], tg_user["id"], tg_id,
            )
            await merge_accounts(primary_id=user["id"], secondary_id=tg_user["id"])
            logger.info("Merge complete for user_id=%s", user["id"])
        elif not tg_user:
            # No TG account — link tg_id directly to current user
            logger.info("Linking tg_id=%s directly to user_id=%s", tg_id, user["id"])
            rowcount = await database.execute(
                users.update().where(users.c.id == user["id"]).values(
                    tg_id=tg_id, linked_tg_id=tg_id
                )
            )
            logger.info("UPDATE tg_id rowcount=%s for user_id=%s", rowcount, user["id"])

            # Verify the update was saved
            updated = await database.fetch_one(users.select().where(users.c.id == user["id"]))
            logger.info(
                "Post-update check: user_id=%s tg_id=%s linked_tg_id=%s",
                user["id"], updated.get("tg_id") if updated else "NOT_FOUND",
                updated.get("linked_tg_id") if updated else "NOT_FOUND",
            )

            # Update name/avatar from Telegram if missing
            if not user.get("avatar") and photo:
                await database.execute(
                    users.update().where(users.c.id == user["id"]).values(avatar=photo)
                )
                logger.info("Updated avatar for user_id=%s", user["id"])
            if not user.get("name") and name:
                await database.execute(
                    users.update().where(users.c.id == user["id"]).values(name=name)
                )
                logger.info("Updated name for user_id=%s", user["id"])
        else:
            # tg_user["id"] == user["id"] → already linked
            logger.info("tg_id=%s already linked to user_id=%s — no action needed", tg_id, user["id"])

        logger.info("Telegram linked successfully: user_id=%s, tg_id=%s", user["id"], tg_id)
        return RedirectResponse("/dashboard?success=linked")

    except Exception as exc:
        logger.exception(
            "Unexpected error in link_telegram_callback for user_id=%s: %s",
            user["id"], exc,
        )
        return RedirectResponse("/dashboard?error=tg_link_failed")


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
