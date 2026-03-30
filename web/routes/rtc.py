"""P2P видеозвонки: API старта, страница /call, регистрация комнаты для Socket.IO."""
from __future__ import annotations

import re
import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.responses import Response

from auth.session import get_user_from_request
from db.database import database
from db.models import users
from services.chat_ws_manager import room_broadcast
from services.dm_blocks import is_dm_blocked
from services.rtc_socketio import get_call_room_meta, register_call_room
from web.routes.chats import _find_personal_chat, _reactions_for_messages
from web.templates_utils import Jinja2Templates

router = APIRouter(tags=["rtc"])
templates = Jinja2Templates(directory="web/templates")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


def _eff_uid(user: dict) -> int:
    return int(user.get("primary_user_id") or user["id"])


class StartCallBody(BaseModel):
    target_user_id: int = Field(..., ge=1)


async def _create_personal_chat_if_needed(uid: int, other_id: int) -> int:
    existing = await _find_personal_chat(uid, other_id)
    if existing:
        return int(existing)
    row = await database.fetch_one_write(
        sa.text(
            """
            INSERT INTO chats (type, name, avatar_url, created_by)
            VALUES ('personal', NULL, NULL, :cb) RETURNING id
            """
        ),
        {"cb": uid},
    )
    cid = int(row["id"])
    await database.execute(
        sa.text(
            """
            INSERT INTO chat_members (chat_id, user_id, role) VALUES
            (:c, :u, 'owner'),
            (:c, :o, 'member')
            """
        ),
        {"c": cid, "u": uid, "o": other_id},
    )
    return cid


@router.post("/api/rtc/start-call")
async def api_rtc_start_call(request: Request, body: StartCallBody):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    tid = int(body.target_user_id)
    if tid == uid:
        return JSONResponse({"error": "self"}, status_code=400)
    other = await database.fetch_one(users.select().where(users.c.id == tid))
    if not other:
        return JSONResponse({"error": "user_not_found"}, status_code=404)
    if await is_dm_blocked(blocker_id=tid, blocked_id=uid) or await is_dm_blocked(
        blocker_id=uid, blocked_id=tid
    ):
        return JSONResponse({"error": "blocked"}, status_code=403)

    room_id = str(uuid.uuid4())
    register_call_room(room_id, caller_id=uid, callee_id=tid)

    chat_id = await _create_personal_chat_if_needed(uid, tid)
    caller_row = await database.fetch_one(users.select().where(users.c.id == uid))
    caller_name = (caller_row.get("name") if caller_row else None) or "Участник"
    base = str(request.base_url).rstrip("/")
    call_url = f"{base}/call/{room_id}"
    text = f"📹 Вам звонит {caller_name}.\n\nПринять звонок: {call_url}"

    row = await database.fetch_one_write(
        sa.text(
            """
            INSERT INTO chat_messages (chat_id, user_id, text, media_url, reply_to_id)
            VALUES (:cid, :uid, :t, NULL, NULL)
            RETURNING id, created_at
            """
        ),
        {"cid": chat_id, "uid": uid, "t": text},
    )
    mid = int(row["id"])
    srow = await database.fetch_one(
        sa.text(
            """
            SELECT m.id, m.chat_id, m.user_id, m.text, m.media_url, m.reply_to_id,
                   m.is_edited, m.is_deleted, m.created_at,
                   u.name AS sender_name, u.avatar AS sender_avatar
            FROM chat_messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.id = :id
            """
        ),
        {"id": mid},
    )
    if not srow:
        return JSONResponse({"error": "message_failed"}, status_code=500)
    rc, rm = await _reactions_for_messages([mid], uid)
    payload: dict[str, Any] = {
        "id": mid,
        "chat_id": int(srow["chat_id"]),
        "user_id": int(srow["user_id"]),
        "text": srow.get("text"),
        "media_url": srow.get("media_url"),
        "reply_to_id": None,
        "reply_preview": None,
        "is_edited": bool(srow.get("is_edited")),
        "is_deleted": bool(srow.get("is_deleted")),
        "created_at": srow["created_at"].isoformat() if srow.get("created_at") else None,
        "sender_name": srow.get("sender_name"),
        "sender_avatar": srow.get("sender_avatar"),
        "reactions": rc.get(mid, {}),
        "my_reactions": rm.get(mid, []),
    }
    await room_broadcast(chat_id, {"type": "message", "payload": payload})

    return JSONResponse({"ok": True, "room_id": room_id, "redirect": f"/call/{room_id}"})


@router.get("/call/{room_id}", response_class=HTMLResponse)
async def call_room_page(request: Request, room_id: str):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(f"/login?next=/call/{room_id}", status_code=302)
    if not _UUID_RE.match(room_id or ""):
        return Response("Некорректная ссылка", status_code=400)

    meta = get_call_room_meta(room_id)
    if not meta:
        return Response("Звонок не найден или уже недоступен", status_code=404)
    uid = _eff_uid(user)
    if uid not in (meta["caller_id"], meta["callee_id"]):
        return Response("Доступ запрещён", status_code=403)

    is_initiator = uid == meta["caller_id"]
    return templates.TemplateResponse(
        "call_room.html",
        {
            "request": request,
            "user": user,
            "room_id": room_id,
            "is_initiator": is_initiator,
        },
    )
