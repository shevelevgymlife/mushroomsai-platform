"""Notifications router — page + API endpoints."""
import logging
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth.session import get_user_from_request
from db.database import database
from web.templates_utils import Jinja2Templates

_logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


async def _require_user(request: Request):
    user = await get_user_from_request(request)
    return user


# ── HTML page ─────────────────────────────────────────────────────────────────

@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login?next=/notifications", status_code=302)
    return templates.TemplateResponse("notifications.html", {
        "request": request,
        "user": user,
    })


# ── API: list ─────────────────────────────────────────────────────────────────

@router.get("/api/notifications")
async def api_notifications_list(request: Request, offset: int = 0):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    rows = await database.fetch_all(
        sa.text("""
            SELECT n.*, u.name AS from_name, u.avatar AS from_avatar
            FROM notifications n
            LEFT JOIN users u ON u.id = n.from_user_id
            WHERE n.user_id = :uid
            ORDER BY n.created_at DESC
            LIMIT 50 OFFSET :offset
        """),
        {"uid": uid, "offset": offset}
    )
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "type": r["type"],
            "title": r["title"],
            "body": r["body"],
            "link": r["link"] or "",
            "is_read": bool(r["is_read"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "from_name": r["from_name"],
            "from_avatar": r["from_avatar"],
        })
    return JSONResponse({"ok": True, "notifications": items})


# ── API: read all ─────────────────────────────────────────────────────────────

@router.post("/api/notifications/read-all")
async def api_notifications_read_all(request: Request):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    await database.execute(
        sa.text("UPDATE notifications SET is_read=true WHERE user_id=:uid AND is_read=false"),
        {"uid": uid}
    )
    return JSONResponse({"ok": True})


# ── API: read one ─────────────────────────────────────────────────────────────

@router.post("/api/notifications/{notif_id}/read")
async def api_notification_read(request: Request, notif_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    await database.execute(
        sa.text("UPDATE notifications SET is_read=true WHERE id=:nid AND user_id=:uid"),
        {"nid": notif_id, "uid": uid}
    )
    return JSONResponse({"ok": True})


# ── API: count unread ─────────────────────────────────────────────────────────

@router.get("/api/notifications/count")
async def api_notifications_count(request: Request):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"count": 0})
    uid = user.get("primary_user_id") or user["id"]
    count = await database.fetch_val(
        sa.text("SELECT COUNT(*) FROM notifications WHERE user_id=:uid AND is_read=false"),
        {"uid": uid}
    ) or 0
    return JSONResponse({"count": int(count)})


# ── API: get settings ─────────────────────────────────────────────────────────

@router.get("/api/notifications/settings")
async def api_notifications_settings_get(request: Request):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    row = await database.fetch_one(
        sa.text("SELECT * FROM notification_settings WHERE user_id=:uid"),
        {"uid": uid}
    )
    if not row:
        # Return defaults
        return JSONResponse({
            "ok": True,
            "settings": {
                "new_follower": True,
                "new_like": True,
                "new_comment": True,
                "new_message": True,
                "new_reply": True,
                "post_in_group": True,
                "mention": True,
                "new_post_from_following": False,
                "send_to_telegram": True,
            }
        })
    return JSONResponse({
        "ok": True,
        "settings": {
            "new_follower": bool(row["new_follower"]),
            "new_like": bool(row["new_like"]),
            "new_comment": bool(row["new_comment"]),
            "new_message": bool(row["new_message"]),
            "new_reply": bool(row["new_reply"]),
            "post_in_group": bool(row["post_in_group"]),
            "mention": bool(row["mention"]),
            "new_post_from_following": bool(row["new_post_from_following"]),
            "send_to_telegram": bool(row["send_to_telegram"]),
        }
    })


# ── API: save settings ────────────────────────────────────────────────────────

@router.post("/api/notifications/settings")
async def api_notifications_settings_post(request: Request):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    allowed = {
        "new_follower", "new_like", "new_comment", "new_message",
        "new_reply", "post_in_group", "mention", "new_post_from_following",
        "send_to_telegram",
    }
    vals = {k: bool(v) for k, v in body.items() if k in allowed}
    if not vals:
        return JSONResponse({"error": "no valid fields"}, status_code=400)

    existing = await database.fetch_one(
        sa.text("SELECT id FROM notification_settings WHERE user_id=:uid"),
        {"uid": uid}
    )
    if existing:
        set_clause = ", ".join(f"{k}=:{k}" for k in vals)
        await database.execute(
            sa.text(f"UPDATE notification_settings SET {set_clause} WHERE user_id=:uid"),
            {**vals, "uid": uid}
        )
    else:
        cols = "user_id, " + ", ".join(vals.keys())
        placeholders = ":uid, " + ", ".join(f":{k}" for k in vals)
        await database.execute(
            sa.text(f"INSERT INTO notification_settings ({cols}) VALUES ({placeholders})"),
            {**vals, "uid": uid}
        )
    return JSONResponse({"ok": True})
