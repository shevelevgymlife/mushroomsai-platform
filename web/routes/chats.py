"""
Мессенджер: личные и групповые чаты, WebSocket, реакции.
Таблицы: migrate_v14 / heavy_startup.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.responses import HTMLResponse

from auth.session import get_current_user, get_user_from_request
from db.database import database
from db.models import users
from services.chat_ws_manager import (
    online_user_ids,
    room_broadcast,
    room_connect,
    room_disconnect,
    touch_presence,
)
from services.legal import legal_acceptance_redirect
from web.templates_utils import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chats"])
templates = Jinja2Templates(directory="web/templates")


def _eff_uid(user: dict) -> int:
    return int(user.get("primary_user_id") or user["id"])


def _media_base() -> str:
    return "/data" if os.path.exists("/data") else "./media"


async def _require_user(request: Request) -> dict | None:
    return await get_user_from_request(request)


async def _member_row(chat_id: int, user_id: int) -> dict | None:
    return await database.fetch_one(
        sa.text(
            "SELECT * FROM chat_members WHERE chat_id = :cid AND user_id = :uid"
        ).bindparams(cid=chat_id, uid=user_id)
    )


async def _find_personal_chat(uid: int, oid: int) -> int | None:
    row = await database.fetch_one(
        sa.text(
            """
            SELECT c.id FROM chats c
            WHERE c.type = 'personal'
              AND (SELECT COUNT(*) FROM chat_members m WHERE m.chat_id = c.id) = 2
              AND EXISTS (SELECT 1 FROM chat_members m1 WHERE m1.chat_id = c.id AND m1.user_id = :u1)
              AND EXISTS (SELECT 1 FROM chat_members m2 WHERE m2.chat_id = c.id AND m2.user_id = :u2)
            LIMIT 1
            """
        ),
        {"u1": uid, "u2": oid},
    )
    return int(row["id"]) if row else None


async def _reactions_for_messages(msg_ids: list[int], viewer_id: int) -> tuple[dict[int, dict[str, int]], dict[int, list[str]]]:
    if not msg_ids:
        return {}, {}
    counts: dict[int, dict[str, int]] = {}
    mine: dict[int, list[str]] = {}
    q = sa.text(
        f"""
        SELECT message_id, emoji, user_id FROM chat_reactions
        WHERE message_id IN ({",".join(str(int(x)) for x in msg_ids)})
        """
    )
    rows = await database.fetch_all(q)
    for r in rows:
        mid = int(r["message_id"])
        em = r["emoji"]
        uid = int(r["user_id"])
        counts.setdefault(mid, {})
        counts[mid][em] = counts[mid].get(em, 0) + 1
        if uid == viewer_id:
            mine.setdefault(mid, []).append(em)
    return counts, mine


class GroupCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    avatar_url: str | None = None
    member_ids: list[int] = Field(default_factory=list)


class SendMessageBody(BaseModel):
    text: str | None = None
    media_url: str | None = None
    reply_to_id: int | None = None


class ReactBody(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=32)


class AddMemberBody(BaseModel):
    user_id: int


@router.get("/chats", response_class=HTMLResponse)
async def chats_page(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse("/login?next=/chats")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    return templates.TemplateResponse(
        "chats.html",
        {"request": request, "user": user},
    )


@router.get("/api/chats/unread-count")
async def api_chats_unread_count(request: Request):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"count": 0})
    uid = _eff_uid(user)
    try:
        n = await database.fetch_val(
            sa.text(
                """
                SELECT COUNT(*) FROM chat_messages m
                JOIN chat_members cm ON cm.chat_id = m.chat_id AND cm.user_id = :uid
                WHERE m.is_deleted = false
                  AND m.user_id != :uid
                  AND m.id > COALESCE(cm.last_read_message_id, 0)
                """
            ),
            {"uid": uid},
        )
        return JSONResponse({"count": int(n or 0)})
    except Exception as e:
        logger.warning("chats unread-count: %s", e)
        return JSONResponse({"count": 0})


@router.get("/api/chats/search-users")
async def api_search_users(request: Request, q: str = ""):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    q = (q or "").strip()
    if len(q) < 1:
        return JSONResponse({"users": []})
    like = f"%{q[:80]}%"
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT id, name, avatar FROM users
            WHERE id != :uid AND (LOWER(name) LIKE LOWER(:like) OR CAST(id AS TEXT) LIKE :pref)
            ORDER BY name NULLS LAST
            LIMIT 30
            """
        ),
        {"uid": uid, "like": like, "pref": f"{q[:20]}%"},
    )
    return JSONResponse({"users": [dict(r) for r in rows]})


@router.get("/api/chats")
async def api_list_chats(request: Request):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT c.id, c.type, c.name, c.avatar_url, c.created_at,
                   lm.id AS last_msg_id, lm.text AS last_text, lm.created_at AS last_at,
                   cm.last_read_message_id
            FROM chat_members cm
            JOIN chats c ON c.id = cm.chat_id
            LEFT JOIN LATERAL (
              SELECT m.id, m.text, m.created_at
              FROM chat_messages m
              WHERE m.chat_id = c.id AND m.is_deleted = false
              ORDER BY m.id DESC
              LIMIT 1
            ) lm ON true
            WHERE cm.user_id = :uid
            ORDER BY lm.created_at DESC NULLS LAST, c.id DESC
            """
        ),
        {"uid": uid},
    )
    out = []
    for r in rows:
        cid = int(r["id"])
        ctype = r["type"]
        title = r["name"]
        avatar = r["avatar_url"]
        partner_id = None
        if ctype == "personal":
            prow = await database.fetch_one(
                sa.text(
                    """
                    SELECT u.id, u.name, u.avatar FROM chat_members cm
                    JOIN users u ON u.id = cm.user_id
                    WHERE cm.chat_id = :cid AND cm.user_id != :uid
                    LIMIT 1
                    """
                ),
                {"cid": cid, "uid": uid},
            )
            if prow:
                partner_id = int(prow["id"])
                title = prow["name"] or f"User {partner_id}"
                avatar = prow["avatar"]
        unread = await database.fetch_val(
            sa.text(
                """
                SELECT COUNT(*) FROM chat_messages m
                JOIN chat_members cm ON cm.chat_id = m.chat_id AND cm.user_id = :uid
                WHERE m.chat_id = :cid AND m.is_deleted = false
                  AND m.user_id != :uid
                  AND m.id > COALESCE(cm.last_read_message_id, 0)
                """
            ),
            {"cid": cid, "uid": uid},
        )
        last_text = (r["last_text"] or "").strip()
        if len(last_text) > 120:
            last_text = last_text[:117] + "…"
        out.append(
            {
                "id": cid,
                "type": ctype,
                "name": title or "Чат",
                "avatar_url": avatar,
                "partner_id": partner_id,
                "last_message": last_text,
                "last_at": r["last_at"].isoformat() if r.get("last_at") else None,
                "unread": int(unread or 0),
            }
        )
    return JSONResponse({"chats": out})


@router.get("/api/chats/{chat_id}/meta")
async def api_chat_meta(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow:
        return JSONResponse({"error": "not_found"}, status_code=404)
    ctype = crow["type"]
    title = crow["name"]
    avatar = crow["avatar_url"]
    member_count = await database.fetch_val(
        sa.text("SELECT COUNT(*) FROM chat_members WHERE chat_id = :cid"),
        {"cid": chat_id},
    )
    partner = None
    if ctype == "personal":
        prow = await database.fetch_one(
            sa.text(
                """
                SELECT u.id, u.name, u.avatar FROM chat_members cm
                JOIN users u ON u.id = cm.user_id
                WHERE cm.chat_id = :cid AND cm.user_id != :uid
                LIMIT 1
                """
            ),
            {"cid": chat_id, "uid": uid},
        )
        if prow:
            partner = {"id": int(prow["id"]), "name": prow["name"], "avatar": prow["avatar"]}
            title = partner["name"]
            avatar = partner["avatar"]
    online = online_user_ids(chat_id)
    return JSONResponse(
        {
            "id": chat_id,
            "type": ctype,
            "name": title or "Чат",
            "avatar_url": avatar,
            "member_count": int(member_count or 0),
            "partner": partner,
            "online_user_ids": online,
        }
    )


@router.get("/api/chats/{chat_id}/members")
async def api_chat_members(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT cm.user_id, cm.role, cm.joined_at, u.name, u.avatar
            FROM chat_members cm
            JOIN users u ON u.id = cm.user_id
            WHERE cm.chat_id = :cid
            ORDER BY cm.joined_at ASC
            """
        ),
        {"cid": chat_id},
    )
    return JSONResponse(
        {
            "members": [
                {
                    "user_id": int(r["user_id"]),
                    "role": r["role"],
                    "name": r["name"],
                    "avatar": r["avatar"],
                    "joined_at": r["joined_at"].isoformat() if r.get("joined_at") else None,
                }
                for r in rows
            ]
        }
    )


@router.get("/api/chats/{chat_id}/messages")
async def api_chat_messages(
    request: Request,
    chat_id: int,
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None),
):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    params: dict[str, Any] = {"cid": chat_id, "lim": limit}
    before_sql = ""
    if before_id is not None:
        before_sql = "AND m.id < :before_id"
        params["before_id"] = before_id

    rows = await database.fetch_all(
        sa.text(
            f"""
            SELECT m.id, m.chat_id, m.user_id, m.text, m.media_url, m.reply_to_id,
                   m.is_edited, m.is_deleted, m.created_at,
                   u.name AS sender_name, u.avatar AS sender_avatar
            FROM chat_messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.chat_id = :cid AND m.is_deleted = false
            {before_sql}
            ORDER BY m.id DESC
            LIMIT :lim
            """
        ),
        params,
    )
    rows = list(reversed(rows))
    reply_ids = [int(r["reply_to_id"]) for r in rows if r.get("reply_to_id")]
    reply_map: dict[int, dict] = {}
    if reply_ids:
        rrows = await database.fetch_all(
            sa.text(
                f"""
                SELECT m.id, m.user_id, m.text, u.name AS sender_name
                FROM chat_messages m
                JOIN users u ON u.id = m.user_id
                WHERE m.id IN ({",".join(str(x) for x in reply_ids)})
                """
            )
        )
        reply_map = {int(x["id"]): dict(x) for x in rrows}

    msg_ids = [int(r["id"]) for r in rows]
    rc_all, rm_all = await _reactions_for_messages(msg_ids, uid)

    messages = []
    for r in rows:
        mid = int(r["id"])
        reply_preview = None
        rtid = r.get("reply_to_id")
        if rtid and int(rtid) in reply_map:
            rp = reply_map[int(rtid)]
            reply_preview = {
                "id": int(rtid),
                "text": (rp.get("text") or "")[:200],
                "user_id": int(rp.get("user_id") or 0),
                "sender_name": rp.get("sender_name"),
            }
        messages.append(
            {
                "id": mid,
                "chat_id": int(r["chat_id"]),
                "user_id": int(r["user_id"]),
                "text": r.get("text"),
                "media_url": r.get("media_url"),
                "reply_to_id": int(rtid) if rtid else None,
                "reply_preview": reply_preview,
                "is_edited": bool(r.get("is_edited")),
                "is_deleted": bool(r.get("is_deleted")),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "sender_name": r.get("sender_name"),
                "sender_avatar": r.get("sender_avatar"),
                "reactions": rc_all.get(mid, {}),
                "my_reactions": rm_all.get(mid, []),
            }
        )

    if before_id is None:
        max_id_row = await database.fetch_one(
            sa.text(
                "SELECT MAX(id) AS x FROM chat_messages WHERE chat_id = :cid AND is_deleted = false"
            ),
            {"cid": chat_id},
        )
        max_mid = int(max_id_row["x"] or 0) if max_id_row else 0
        if max_mid:
            await database.execute(
                sa.text(
                    """
                    UPDATE chat_members
                    SET last_read_message_id = GREATEST(COALESCE(last_read_message_id, 0), :mid)
                    WHERE chat_id = :cid AND user_id = :uid
                    """
                ),
                {"mid": max_mid, "cid": chat_id, "uid": uid},
            )

    return JSONResponse({"messages": messages, "has_more": len(rows) >= limit})


@router.post("/api/chats/personal/{other_id}")
async def api_create_personal(request: Request, other_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if other_id == uid:
        return JSONResponse({"error": "self"}, status_code=400)
    other = await database.fetch_one(users.select().where(users.c.id == other_id))
    if not other:
        return JSONResponse({"error": "user_not_found"}, status_code=404)
    existing = await _find_personal_chat(uid, other_id)
    if existing:
        return JSONResponse({"chat_id": existing, "existing": True})
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
    return JSONResponse({"chat_id": cid, "existing": False})


@router.post("/api/chats/group")
async def api_create_group(request: Request, body: GroupCreateBody):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    mids = [int(x) for x in body.member_ids if int(x) != uid]
    mids = list(dict.fromkeys(mids))[:200]
    row = await database.fetch_one_write(
        sa.text(
            """
            INSERT INTO chats (type, name, avatar_url, created_by)
            VALUES ('group', :name, :av, :cb) RETURNING id
            """
        ),
        {"name": body.name.strip(), "av": body.avatar_url, "cb": uid},
    )
    cid = int(row["id"])
    await database.execute(
        sa.text("INSERT INTO chat_members (chat_id, user_id, role) VALUES (:c,:u,'owner')"),
        {"c": cid, "u": uid},
    )
    for mid in mids:
        exists = await database.fetch_one(users.select().where(users.c.id == mid))
        if exists:
            try:
                await database.execute(
                    sa.text(
                        "INSERT INTO chat_members (chat_id, user_id, role) VALUES (:c,:m,'member')"
                    ),
                    {"c": cid, "m": mid},
                )
            except Exception:
                pass
    return JSONResponse({"chat_id": cid})


@router.post("/api/chats/upload")
async def api_chats_upload(request: Request, file: UploadFile = File(...)):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    ct = (file.content_type or "").lower()
    if ct not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        return JSONResponse({"error": "invalid_type"}, status_code=400)
    raw = await file.read()
    if len(raw) > 6 * 1024 * 1024:
        return JSONResponse({"error": "too_large"}, status_code=400)
    ext = ".jpg" if "jpeg" in ct else ".png" if "png" in ct else ".webp" if "webp" in ct else ".gif"
    name = f"chats/{uuid.uuid4().hex}{ext}"
    base = _media_base()
    os.makedirs(os.path.join(base, "chats"), exist_ok=True)
    path = os.path.join(base, name)
    with open(path, "wb") as f:
        f.write(raw)
    return JSONResponse({"url": f"/media/{name}"})


@router.post("/api/chats/{chat_id}/messages")
async def api_send_message(request: Request, chat_id: int, body: SendMessageBody):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    text = (body.text or "").strip()
    media = (body.media_url or "").strip() or None
    if not text and not media:
        return JSONResponse({"error": "empty"}, status_code=400)
    if body.reply_to_id:
        rp = await database.fetch_one(
            sa.text(
                "SELECT id FROM chat_messages WHERE id = :id AND chat_id = :cid AND is_deleted = false"
            ),
            {"id": body.reply_to_id, "cid": chat_id},
        )
        if not rp:
            return JSONResponse({"error": "bad_reply"}, status_code=400)

    row = await database.fetch_one_write(
        sa.text(
            """
            INSERT INTO chat_messages (chat_id, user_id, text, media_url, reply_to_id)
            VALUES (:cid, :uid, :t, :m, :r)
            RETURNING id, created_at
            """
        ),
        {
            "cid": chat_id,
            "uid": uid,
            "t": text or None,
            "m": media,
            "r": body.reply_to_id,
        },
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
    reply_map = {}
    if srow and srow.get("reply_to_id"):
        rtid = int(srow["reply_to_id"])
        rp = await database.fetch_one(
            sa.text(
                """
                SELECT m.id, m.user_id, m.text, u.name AS sender_name
                FROM chat_messages m
                JOIN users u ON u.id = m.user_id
                WHERE m.id = :id
                """
            ),
            {"id": rtid},
        )
        if rp:
            reply_map[rtid] = dict(rp)
    mid = int(srow["id"])
    rc, rm = await _reactions_for_messages([mid], uid)
    rtid = srow.get("reply_to_id")
    reply_preview = None
    if rtid and int(rtid) in reply_map:
        rp = reply_map[int(rtid)]
        reply_preview = {
            "id": int(rtid),
            "text": (rp.get("text") or "")[:200],
            "user_id": int(rp.get("user_id") or 0),
            "sender_name": rp.get("sender_name"),
        }
    payload = {
        "id": mid,
        "chat_id": int(srow["chat_id"]),
        "user_id": int(srow["user_id"]),
        "text": srow.get("text"),
        "media_url": srow.get("media_url"),
        "reply_to_id": int(rtid) if rtid else None,
        "reply_preview": reply_preview,
        "is_edited": bool(srow.get("is_edited")),
        "is_deleted": bool(srow.get("is_deleted")),
        "created_at": srow["created_at"].isoformat() if srow.get("created_at") else None,
        "sender_name": srow.get("sender_name"),
        "sender_avatar": srow.get("sender_avatar"),
        "reactions": rc.get(mid, {}),
        "my_reactions": rm.get(mid, []),
    }
    await room_broadcast(chat_id, {"type": "message", "payload": payload})
    return JSONResponse({"ok": True, "message": payload})


@router.post("/api/chats/{chat_id}/messages/{msg_id}/react")
async def api_react(request: Request, chat_id: int, msg_id: int, body: ReactBody):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    msg = await database.fetch_one(
        sa.text(
            "SELECT id FROM chat_messages WHERE id = :id AND chat_id = :cid AND is_deleted = false"
        ),
        {"id": msg_id, "cid": chat_id},
    )
    if not msg:
        return JSONResponse({"error": "not_found"}, status_code=404)
    emoji = body.emoji.strip()
    if not emoji or len(emoji) > 32:
        return JSONResponse({"error": "bad_emoji"}, status_code=400)

    existing = await database.fetch_one(
        sa.text(
            """
            SELECT id FROM chat_reactions
            WHERE message_id = :mid AND user_id = :uid AND emoji = :em
            """
        ),
        {"mid": msg_id, "uid": uid, "em": emoji},
    )
    if existing:
        await database.execute(
            sa.text(
                "DELETE FROM chat_reactions WHERE message_id = :mid AND user_id = :uid AND emoji = :em"
            ),
            {"mid": msg_id, "uid": uid, "em": emoji},
        )
        toggled = "removed"
    else:
        try:
            await database.execute(
                sa.text(
                    """
                    INSERT INTO chat_reactions (message_id, user_id, emoji)
                    VALUES (:mid, :uid, :em)
                    """
                ),
                {"mid": msg_id, "uid": uid, "em": emoji},
            )
            toggled = "added"
        except Exception:
            toggled = "noop"

    rc, rm = await _reactions_for_messages([msg_id], uid)
    await room_broadcast(
        chat_id,
        {
            "type": "reaction",
            "payload": {
                "message_id": msg_id,
                "counts": rc.get(msg_id, {}),
            },
        },
    )
    return JSONResponse(
        {
            "ok": True,
            "message_id": msg_id,
            "counts": rc.get(msg_id, {}),
            "my_reactions": rm.get(msg_id, []),
            "toggled": toggled,
        }
    )


@router.delete("/api/chats/{chat_id}/messages/{msg_id}")
async def api_delete_message(request: Request, chat_id: int, msg_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    row = await database.fetch_one(
        sa.text(
            "SELECT user_id FROM chat_messages WHERE id = :id AND chat_id = :cid AND is_deleted = false"
        ),
        {"id": msg_id, "cid": chat_id},
    )
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if int(row["user_id"]) != uid:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        sa.text("UPDATE chat_messages SET is_deleted = true WHERE id = :id"),
        {"id": msg_id},
    )
    await room_broadcast(chat_id, {"type": "message_deleted", "payload": {"id": msg_id}})
    return JSONResponse({"ok": True})


@router.post("/api/chats/{chat_id}/members")
async def api_add_member(request: Request, chat_id: int, body: AddMemberBody):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    mem = await _member_row(chat_id, uid)
    if not mem or mem["role"] not in ("owner", "admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crow = await database.fetch_one(sa.text("SELECT type FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or crow["type"] != "group":
        return JSONResponse({"error": "not_group"}, status_code=400)
    oid = int(body.user_id)
    if oid == uid:
        return JSONResponse({"error": "self"}, status_code=400)
    exists = await database.fetch_one(users.select().where(users.c.id == oid))
    if not exists:
        return JSONResponse({"error": "user_not_found"}, status_code=404)
    if await _member_row(chat_id, oid):
        return JSONResponse({"error": "already_member"}, status_code=400)
    try:
        await database.execute(
            sa.text("INSERT INTO chat_members (chat_id, user_id, role) VALUES (:c,:u,'member')"),
            {"c": chat_id, "u": oid},
        )
    except Exception as e:
        logger.warning("add member: %s", e)
        return JSONResponse({"error": "failed"}, status_code=400)
    await room_broadcast(chat_id, {"type": "members_changed", "payload": {"action": "add", "user_id": oid}})
    return JSONResponse({"ok": True})


@router.delete("/api/chats/{chat_id}/members/me")
async def api_leave_group(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    crow = await database.fetch_one(sa.text("SELECT type FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if crow["type"] == "personal":
        return JSONResponse({"error": "cannot_leave_personal"}, status_code=400)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        sa.text("DELETE FROM chat_members WHERE chat_id = :c AND user_id = :u"),
        {"c": chat_id, "u": uid},
    )
    await room_broadcast(
        chat_id, {"type": "members_changed", "payload": {"action": "leave", "user_id": uid}}
    )
    return JSONResponse({"ok": True})


@router.websocket("/ws/chats/{chat_id}")
async def ws_chats(websocket: WebSocket, chat_id: int):
    token = websocket.cookies.get("access_token")
    if not token:
        await websocket.close(code=4401)
        return
    u = await get_current_user(token)
    if not u:
        await websocket.close(code=4401)
        return
    uid = _eff_uid(u)
    if not await _member_row(chat_id, uid):
        await websocket.close(code=4403)
        return
    await room_connect(chat_id, websocket)
    touch_presence(chat_id, uid)
    try:
        await websocket.send_text(json.dumps({"type": "hello", "user_id": uid}))
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = data.get("type")
            if t in ("ping", "heartbeat", "hb"):
                touch_presence(chat_id, uid)
                await websocket.send_text(json.dumps({"type": "pong", "ts": datetime.utcnow().isoformat()}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("ws chats end: %s", e)
    finally:
        room_disconnect(chat_id, websocket)
