"""Страницы /notifications и API событий (in_app_notifications)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth.session import get_user_from_request
from auth.ui_prefs import attach_screen_rim_prefs
from db.database import database
from db.models import in_app_notifications, users
from services.in_app_notifications import (
    count_unread_events,
    load_prefs_for_user,
    mark_events_notifications_read,
    mark_one_read,
    type_display,
)
from web.templates_utils import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

_NTYPE_SECTION: dict[str, str] = {
    "post_like": "likes",
    "profile_like": "likes",
    "comment": "comments",
    "comment_reply": "comment_replies",
    "follower": "subs",
    "subscription_post": "subs_posts",
    "group_post": "groups",
    "mention": "mentions",
}

_SECTION_ORDER: tuple[tuple[str, str, str], ...] = (
    ("likes", "⭐", "Лайки"),
    ("comments", "💬", "Комментарии"),
    ("subs", "👤", "Подписки"),
    ("comment_replies", "↩️", "Ответы на комментарии"),
    ("groups", "👥", "Публикации в группах"),
    ("mentions", "@", "Упоминания"),
    ("subs_posts", "📰", "Новые посты подписок"),
)


def _bucket_notifications_by_section(items: list[dict]) -> list[dict]:
    bucket: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        key = _NTYPE_SECTION.get(it.get("ntype") or "", "other")
        bucket[key].append(it)
    sections: list[dict] = []
    for sec_key, icon, label in _SECTION_ORDER:
        lst = list(bucket.get(sec_key) or [])
        unread = sum(1 for x in lst if not x.get("read"))
        sections.append({"key": sec_key, "icon": icon, "label": label, "entries": lst, "unread_count": unread})
    other = bucket.get("other") or []
    if other:
        sections.append({"key": "other", "icon": "🔔", "label": "Другое", "entries": other,
                         "unread_count": sum(1 for x in other if not x.get("read"))})
    return sections


def _created_at_utc_iso(dt) -> str:
    if not dt or not isinstance(dt, datetime):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


# ── HTML: список событий ───────────────────────────────────────────────────────

@router.get("/notifications", response_class=HTMLResponse)
async def notifications_list_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/notifications", status_code=302)
    attach_screen_rim_prefs(user)
    uid = int(user.get("primary_user_id") or user["id"])
    # Просмотр ленты событий = все непрочитанные (лайки, комментарии, подписки и т.д.) считаются прочитанными
    await mark_events_notifications_read(uid)
    rows = await database.fetch_all(
        sa.select(
            in_app_notifications.c.id,
            in_app_notifications.c.ntype,
            in_app_notifications.c.title,
            in_app_notifications.c.body,
            in_app_notifications.c.link_url,
            in_app_notifications.c.read_at,
            in_app_notifications.c.created_at,
            in_app_notifications.c.actor_id,
            users.c.name.label("actor_name"),
            users.c.avatar.label("actor_avatar"),
        )
        .select_from(in_app_notifications.outerjoin(users, users.c.id == in_app_notifications.c.actor_id))
        .where(in_app_notifications.c.recipient_id == uid)
        .where(in_app_notifications.c.ntype != "message")
        .order_by(in_app_notifications.c.created_at.desc())
        .limit(500)
    )
    items = []
    for r in rows:
        ico, tlabel = type_display(r["ntype"])
        items.append({
            "id": r["id"],
            "ntype": r["ntype"],
            "type_icon": ico,
            "type_label": tlabel,
            "title": r.get("title") or "",
            "body": (r.get("body") or "")[:500],
            "link_url": r.get("link_url"),
            "read": r.get("read_at") is not None,
            "created_at_iso": _created_at_utc_iso(r.get("created_at")),
            "actor_id": r.get("actor_id"),
            "actor_name": r.get("actor_name") or "Участник",
            "actor_avatar": r.get("actor_avatar"),
        })
    sections = _bucket_notifications_by_section(items)
    return templates.TemplateResponse("notifications/list.html", {
        "request": request,
        "user": user,
        "items": items,
        "sections": sections,
    })


# ── HTML: детальная страница события ──────────────────────────────────────────

@router.get("/notifications/{nid:int}", response_class=HTMLResponse)
async def notification_detail_page(request: Request, nid: int):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(f"/login?next=/notifications/{nid}", status_code=302)
    attach_screen_rim_prefs(user)
    uid = int(user.get("primary_user_id") or user["id"])
    row = await database.fetch_one(
        sa.select(
            in_app_notifications.c.id,
            in_app_notifications.c.ntype,
            in_app_notifications.c.title,
            in_app_notifications.c.body,
            in_app_notifications.c.link_url,
            in_app_notifications.c.read_at,
            in_app_notifications.c.created_at,
            in_app_notifications.c.actor_id,
            in_app_notifications.c.meta_json,
            users.c.name.label("actor_name"),
            users.c.avatar.label("actor_avatar"),
        )
        .select_from(in_app_notifications.outerjoin(users, users.c.id == in_app_notifications.c.actor_id))
        .where(in_app_notifications.c.id == nid)
        .where(in_app_notifications.c.recipient_id == uid)
    )
    if not row:
        return RedirectResponse("/notifications", status_code=302)
    await mark_one_read(nid, uid)
    ico, tlabel = type_display(row["ntype"])
    meta = {}
    if row.get("meta_json"):
        try:
            meta = json.loads(row["meta_json"])
        except Exception:
            pass
    return templates.TemplateResponse("notifications/detail.html", {
        "request": request,
        "user": user,
        "n": {
            "id": row["id"],
            "ntype": row["ntype"],
            "type_icon": ico,
            "type_label": tlabel,
            "title": row.get("title") or "",
            "body": row.get("body") or "",
            "link_url": row.get("link_url"),
            "created_at_iso": _created_at_utc_iso(row.get("created_at")),
            "actor_id": row.get("actor_id"),
            "actor_name": row.get("actor_name") or "Участник",
            "actor_avatar": row.get("actor_avatar"),
            "meta": meta,
        },
    })


# ── API: настройки уведомлений ────────────────────────────────────────────────

@router.post("/api/notifications/settings")
async def api_notifications_settings_save(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = int(user.get("primary_user_id") or user["id"])
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    base = await load_prefs_for_user(uid)
    for k in list(base.keys()):
        if k in body:
            base[k] = bool(body[k])
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(notification_prefs_json=json.dumps(base, ensure_ascii=False))
    )
    return JSONResponse({"ok": True, "prefs": base})


@router.get("/api/notifications/settings")
async def api_notifications_settings_get(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = int(user.get("primary_user_id") or user["id"])
    prefs = await load_prefs_for_user(uid)
    return JSONResponse({"ok": True, "prefs": prefs})


@router.get("/api/notifications/count")
async def api_notifications_count(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"count": 0})
    uid = int(user.get("primary_user_id") or user["id"])
    n = await count_unread_events(uid)
    return JSONResponse({"count": int(n)})
