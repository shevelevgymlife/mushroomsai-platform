import logging
import urllib.parse

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from web.templates_utils import Jinja2Templates
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
        print("TG CALLBACK: no session user, redirecting to login")
        logger.warning("link-telegram-callback: no session user, redirecting to login")
        return RedirectResponse("/login")

    current_user_id = user["id"]
    print(f"TG CALLBACK START: CURRENT USER ID: {current_user_id}, email={user.get('email')}, tg_id_before={user.get('tg_id')}")
    logger.info(
        "link-telegram-callback: user_id=%s email=%s tg_id_before=%s params=%s",
        current_user_id, user.get("email"), user.get("tg_id"), dict(request.query_params),
    )

    try:
        data = dict(request.query_params)
        print(f"TG DATA: {data}")

        # verify_telegram_auth mutates data (pops 'hash'), so pass a copy
        verified = verify_telegram_auth(data.copy())
        print(f"HASH CHECK: {verified}")
        logger.info("Telegram auth verification result: %s for user_id=%s", verified, current_user_id)
        if not verified:
            logger.warning("Telegram auth verification failed for user_id=%s data=%s", current_user_id, data)
            return RedirectResponse("/dashboard?error=tg_auth_failed")

        raw_id = data.get("id")
        if not raw_id:
            print(f"TG CALLBACK: missing id in params: {data}")
            logger.error("Missing 'id' in Telegram callback params: %s", data)
            return RedirectResponse("/dashboard?error=tg_auth_failed")

        tg_id = int(raw_id)
        name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        photo = data.get("photo_url", "")
        print(f"TG USER ID: {tg_id}, name={name!r}")

        # Check if this tg_id already belongs to another account
        tg_user = await database.fetch_one(users.select().where(users.c.tg_id == tg_id))
        print(f"EXISTING TG USER: {{'id': {tg_user['id']}, 'email': {tg_user.get('email')}}} " if tg_user else "EXISTING TG USER: None")
        logger.info(
            "Existing tg_user for tg_id=%s: %s",
            tg_id, {"id": tg_user["id"], "email": tg_user.get("email")} if tg_user else None,
        )

        if tg_user and tg_user["id"] != current_user_id:
            # TG account exists separately — soft-link without touching tg_id (UNIQUE field)
            # Google account stays primary, TG account becomes secondary
            google_id = user.get("google_id")
            print(f"SOFT LINK: Google user_id={current_user_id} (google_id={google_id}) <-> TG user_id={tg_user['id']} (tg_id={tg_id})")
            logger.info("Soft-linking: primary(google) user_id=%s, secondary(tg) user_id=%s, tg_id=%s", current_user_id, tg_user["id"], tg_id)

            # 1. Record tg_id reference on Google account (no tg_id write — would violate UNIQUE)
            r1 = await database.execute(
                users.update().where(users.c.id == current_user_id).values(linked_tg_id=tg_id)
            )
            print(f"UPDATE linked_tg_id on Google account: rowcount={r1}")

            # 2. Record google_id reference on TG account and mark it as secondary
            tg_updates = {"primary_user_id": current_user_id}
            if google_id:
                tg_updates["linked_google_id"] = google_id
            r2 = await database.execute(
                users.update().where(users.c.id == tg_user["id"]).values(**tg_updates)
            )
            print(f"UPDATE TG account (primary_user_id + linked_google_id): rowcount={r2}")

            # 3. Transfer chat messages from TG account to Google account
            r3 = await database.execute(
                messages.update().where(messages.c.user_id == tg_user["id"]).values(user_id=current_user_id)
            )
            print(f"TRANSFER messages from TG to Google account: rowcount={r3}")

            # Verify
            updated = await database.fetch_one(users.select().where(users.c.id == current_user_id))
            print(f"POST-UPDATE CHECK: user_id={current_user_id} linked_tg_id={updated.get('linked_tg_id') if updated else 'NOT_FOUND'}")

        elif not tg_user:
            # No TG account row — just store linked_tg_id reference (do NOT write tg_id, it belongs to TG account)
            print(f"NO TG USER FOUND — storing linked_tg_id={tg_id} on user_id={current_user_id}")
            rowcount = await database.execute(
                users.update().where(users.c.id == current_user_id).values(linked_tg_id=tg_id)
            )
            print(f"UPDATE RESULT: {rowcount}")
            logger.info("UPDATE linked_tg_id rowcount=%s for user_id=%s", rowcount, current_user_id)

            # Verify the update was saved
            updated = await database.fetch_one(users.select().where(users.c.id == current_user_id))
            print(f"POST-UPDATE CHECK: user_id={current_user_id} linked_tg_id={updated.get('linked_tg_id') if updated else 'NOT_FOUND'}")

            # Update name/avatar from Telegram if missing
            if not user.get("avatar") and photo:
                await database.execute(users.update().where(users.c.id == current_user_id).values(avatar=photo))
            if not user.get("name") and name:
                await database.execute(users.update().where(users.c.id == current_user_id).values(name=name))
        else:
            # tg_user["id"] == current_user_id → already linked
            print(f"ALREADY LINKED: tg_id={tg_id} already linked to user_id={current_user_id}")
            logger.info("tg_id=%s already linked to user_id=%s — no action needed", tg_id, current_user_id)

        print(f"TG CALLBACK SUCCESS: user_id={current_user_id}, tg_id={tg_id}")
        logger.info("Telegram linked successfully: user_id=%s, tg_id=%s", current_user_id, tg_id)
        return RedirectResponse("/dashboard?success=linked")

    except Exception as exc:
        print(f"TG CALLBACK ERROR: user_id={user['id']}, exc={exc}")
        logger.exception("Unexpected error in link_telegram_callback for user_id=%s: %s", user["id"], exc)
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


@router.post("/sync-history")
async def sync_history(request: Request):
    """Transfer messages from all secondary (linked) accounts to the current primary account."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    primary_id = user["id"]

    # Find all secondary accounts that point to this user as primary
    secondary_accounts = await database.fetch_all(
        users.select().where(users.c.primary_user_id == primary_id)
    )

    if not secondary_accounts:
        print(f"SYNC HISTORY: no secondary accounts for user_id={primary_id}")
        return JSONResponse({"ok": True, "transferred": 0, "secondaries": 0})

    total_transferred = 0
    for secondary in secondary_accounts:
        secondary_id = secondary["id"]
        rowcount = await database.execute(
            messages.update()
            .where(messages.c.user_id == secondary_id)
            .values(user_id=primary_id)
        )
        print(f"SYNC HISTORY: transferred {rowcount} messages from secondary_id={secondary_id} to primary_id={primary_id}")
        total_transferred += (rowcount or 0)

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
