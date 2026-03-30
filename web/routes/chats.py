"""
Мессенджер: личные и групповые чаты, WebSocket, реакции.
Таблицы: migrate_v14 / heavy_startup.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.responses import HTMLResponse

from auth.session import get_current_user, get_user_from_request
from db.database import database
from db.models import community_posts, community_profiles, users
from services.in_app_notifications import (
    create_notification,
    load_prefs_for_user,
    should_send_telegram_for_event,
)
from services.notify_user_stub import notify_user_dm_with_read_button, notify_user_group_chat_button
from services.chat_ws_manager import (
    online_user_ids,
    room_broadcast,
    room_connect,
    room_disconnect,
    touch_presence,
)
from services.legal import legal_acceptance_redirect
from services.legacy_dm_chat_sync import sync_all_partners_for_user
from services.dm_blocks import dm_block_user, dm_unblock_user, is_dm_blocked
from services.messenger_unread import count_chat_unread, mark_chat_viewed
from web.templates_utils import Jinja2Templates
from web.routes.public import apply_token_privacy_for_viewer, get_public_user_data

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


async def _personal_partner_user_id(chat_id: int, viewer_id: int) -> int | None:
    crow = await database.fetch_one(sa.text("SELECT type FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or (crow.get("type") or "") != "personal":
        return None
    row = await database.fetch_one(
        sa.text(
            "SELECT user_id FROM chat_members WHERE chat_id = :cid AND user_id <> :uid LIMIT 1"
        ),
        {"cid": chat_id, "uid": viewer_id},
    )
    return int(row["user_id"]) if row and row.get("user_id") is not None else None


async def _viewer_message_cutoff_datetime(chat_id: int, viewer_id: int) -> datetime | None:
    try:
        row = await database.fetch_one(
            sa.text(
                "SELECT auto_delete_ttl_seconds FROM chat_members WHERE chat_id = :c AND user_id = :u"
            ),
            {"c": chat_id, "u": viewer_id},
        )
    except Exception:
        return None
    if not row or row.get("auto_delete_ttl_seconds") is None:
        return None
    sec = int(row["auto_delete_ttl_seconds"] or 0)
    if sec <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(seconds=sec)


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
    """Публичная — видна всем в списке групп, можно вступить. Приватная — только по приглашению."""
    is_public: bool = True


class SendMessageBody(BaseModel):
    text: str | None = None
    media_url: str | None = None
    reply_to_id: int | None = None


class ReactBody(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=32)


class AddMemberBody(BaseModel):
    user_id: int


class GroupPatchBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)
    avatar_url: str | None = None
    is_public: bool | None = None
    reactions_mode: str | None = None
    appearance: str | None = None
    topics_enabled: bool | None = None
    linked_channel_label: str | None = Field(None, max_length=255)
    permissions: dict[str, bool] | None = None


class MuteBody(BaseModel):
    muted: bool


class MemberMeSettingsBody(BaseModel):
    """Автоудаление с экрана: только для этого участника; NULL/0 = выкл."""

    auto_delete_ttl_seconds: int | None = None


ALLOWED_AUTO_DELETE_TTL = frozenset(
    {
        24 * 3600,
        7 * 24 * 3600,
        30 * 24 * 3600,
    }
)

_URL_FIND = re.compile(
    r"(https?://[^\s<>\]\)\"'\]]+|(?:www\.)[^\s<>\]\)\"'\]]+|t\.me/[^\s<>\]\)\"'\]]+)",
    re.IGNORECASE,
)


def _urls_from_message_text(text: str | None) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for m in _URL_FIND.finditer(text):
        u = m.group(0).strip().rstrip(").,;]")
        if u and u not in out:
            out.append(u)
    return out


class BanBody(BaseModel):
    user_id: int


class SetRoleBody(BaseModel):
    role: str = Field(..., pattern="^(admin|member)$")


PERMISSION_KEYS: tuple[str, ...] = (
    "send_messages",
    "send_media",
    "invite_members",
    "pin_messages",
    "edit_group_info",
    "delete_others_messages",
    "add_admins",
    "ban_members",
    "send_stickers",
    "send_voice",
    "send_links",
    "mention_everyone",
    "slow_mode_bypass",
    "manage_topics",
)


def _default_permissions() -> dict[str, bool]:
    return {k: True for k in PERMISSION_KEYS}


def _default_group_settings() -> dict[str, Any]:
    return {
        "is_public": True,
        "reactions_mode": "all",
        "appearance": "cyan",
        "topics_enabled": False,
        "linked_channel_label": "",
        "permissions": _default_permissions(),
    }


def _parse_group_settings(raw: str | None) -> dict[str, Any]:
    base = _default_group_settings()
    if not raw:
        return base
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return base
        if "is_public" in data:
            base["is_public"] = bool(data["is_public"])
        if data.get("reactions_mode") in ("all", "none"):
            base["reactions_mode"] = data["reactions_mode"]
        if data.get("appearance") in ("cyan", "gold", "violet"):
            base["appearance"] = data["appearance"]
        if "topics_enabled" in data:
            base["topics_enabled"] = bool(data["topics_enabled"])
        if isinstance(data.get("linked_channel_label"), str):
            base["linked_channel_label"] = data["linked_channel_label"][:255]
        perms_in = data.get("permissions")
        if isinstance(perms_in, dict):
            merged = dict(base["permissions"])
            for k in PERMISSION_KEYS:
                if k in perms_in:
                    merged[k] = bool(perms_in[k])
            base["permissions"] = merged
        return base
    except Exception:
        return base


def _settings_json_from_dict(d: dict[str, Any]) -> str:
    return json.dumps(d, ensure_ascii=False)


def _mention_user_ids_from_chat_text(t: str | None) -> set[int]:
    if not t:
        return set()
    out: set[int] = set()
    for m in re.finditer(r"@(\d{1,12})\b", t):
        try:
            out.add(int(m.group(1)))
        except ValueError:
            pass
    return out


async def _notify_group_chat_message(
    *,
    chat_id: int,
    sender_id: int,
    message_id: int,
    text: str | None,
    media_url: str | None,
    reply_to_id: int | None,
    sender_name: str,
) -> None:
    crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or (crow.get("type") or "") != "group":
        return
    chat_title = ((crow.get("name") or "") or "Группа").strip() or "Группа"
    link = f"/chats?open_chat={chat_id}"
    body_prev = (text or "").strip()
    if not body_prev and media_url:
        body_prev = "[медиа]"
    if not body_prev:
        body_prev = " "
    body_prev = body_prev[:400]

    mention_ids = _mention_user_ids_from_chat_text(text)
    reply_to_uid: int | None = None
    if reply_to_id:
        rpr = await database.fetch_one(
            sa.text(
                "SELECT user_id FROM chat_messages WHERE id = :id AND chat_id = :cid AND is_deleted = false"
            ),
            {"id": reply_to_id, "cid": chat_id},
        )
        if rpr and rpr.get("user_id") is not None:
            reply_to_uid = int(rpr["user_id"])

    members = await database.fetch_all(
        sa.text(
            """
            SELECT user_id, COALESCE(mute_notifications, false) AS mute_notifications
            FROM chat_members
            WHERE chat_id = :cid AND user_id <> :sender
            """
        ),
        {"cid": chat_id, "sender": sender_id},
    )
    sn = (sender_name or "").strip() or "Участник"

    for mrow in members:
        rid = int(mrow["user_id"])
        muted = bool(mrow["mute_notifications"])
        mentioned = rid in mention_ids
        is_reply = reply_to_uid is not None and reply_to_uid == rid
        priority = mentioned or is_reply

        if muted and not priority:
            continue

        prefs = await load_prefs_for_user(rid)

        if priority:
            if mentioned and is_reply:
                ntype = "mention"
                title = "Упоминание и ответ"
                body_n = f"{sn} ответил вам и упомянул вас в «{chat_title}»"
            elif mentioned:
                ntype = "mention"
                title = "Упоминание в чате"
                body_n = f"{sn} упомянул вас в «{chat_title}»"
            else:
                ntype = "group_post"
                title = "Ответ в чате"
                body_n = f"{sn} ответил вам в «{chat_title}»"
            await create_notification(
                recipient_id=rid,
                actor_id=sender_id,
                ntype=ntype,
                title=title,
                body=body_n,
                link_url=link,
                source_kind="chat_group_message",
                source_id=message_id,
                skip_prefs=True,
            )
        else:
            await create_notification(
                recipient_id=rid,
                actor_id=sender_id,
                ntype="group_post",
                title="Сообщение в группе",
                body=f"{sn} в «{chat_title}»: {body_prev[:280]}",
                link_url=link,
                source_kind="chat_group_message",
                source_id=message_id,
                skip_prefs=False,
            )

        if not prefs.get("telegram_bot", True):
            continue
        if not priority:
            continue
        allow_tg = (mentioned and prefs.get("mentions", True)) or (
            is_reply and prefs.get("group_posts", True)
        )
        if not allow_tg:
            continue
        peer_row = await database.fetch_one(users.select().where(users.c.id == rid))
        tg_id = (peer_row.get("tg_id") or peer_row.get("linked_tg_id")) if peer_row else None
        if not tg_id:
            continue
        await notify_user_group_chat_button(
            int(tg_id),
            chat_title=chat_title,
            open_path=link,
            is_mention=mentioned,
            is_reply=is_reply,
        )


async def _audit(chat_id: int, actor_id: int | None, action: str, detail: dict | None = None) -> None:
    try:
        await database.execute(
            sa.text(
                """
                INSERT INTO chat_group_audit (chat_id, actor_id, action, detail)
                VALUES (:cid, :aid, :act, :det)
                """
            ),
            {
                "cid": chat_id,
                "aid": actor_id,
                "act": action[:64],
                "det": json.dumps(detail, ensure_ascii=False) if detail else None,
            },
        )
    except Exception as e:
        logger.debug("chat audit skip: %s", e)


async def _is_banned(chat_id: int, user_id: int) -> bool:
    row = await database.fetch_one(
        sa.text(
            "SELECT 1 FROM chat_group_bans WHERE chat_id = :c AND user_id = :u LIMIT 1"
        ),
        {"c": chat_id, "u": user_id},
    )
    return bool(row)


async def _save_group_settings_row(chat_id: int, settings: dict[str, Any]) -> None:
    await database.execute(
        sa.text("UPDATE chats SET group_settings_json = :js WHERE id = :id"),
        {"js": _settings_json_from_dict(settings), "id": chat_id},
    )


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
        await sync_all_partners_for_user(uid)
        n = await count_chat_unread(uid)
        return JSONResponse({"count": n})
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
    while q.startswith("@"):
        q = q[1:].strip()
    if len(q) < 1:
        return JSONResponse({"users": []})
    q = q[:80]
    like = f"%{q}%"
    pref = f"{q}%"
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT id, name, avatar FROM users
            WHERE id != :uid
              AND (
                LOWER(COALESCE(name, '')) LIKE LOWER(:like)
                OR CAST(id AS TEXT) LIKE :pref
              )
            ORDER BY
              CASE WHEN LOWER(COALESCE(name, '')) = LOWER(:exact) THEN 0 ELSE 1 END,
              CASE WHEN LOWER(COALESCE(name, '')) LIKE LOWER(:startpref) || '%' THEN 0 ELSE 1 END,
              LENGTH(COALESCE(name, '')),
              name NULLS LAST
            LIMIT 30
            """
        ),
        {"uid": uid, "like": like, "pref": pref, "exact": q, "startpref": q},
    )
    return JSONResponse({"users": [dict(r) for r in rows]})


@router.get("/api/chats")
async def api_list_chats(request: Request):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    await sync_all_partners_for_user(uid)
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
                "needs_join": False,
            }
        )
    member_chat_ids = {int(x["id"]) for x in out}
    try:
        discover = await database.fetch_all(
            sa.text(
                """
                SELECT c.id, c.type, c.name, c.avatar_url, c.created_at,
                       lm.id AS last_msg_id, lm.text AS last_text, lm.created_at AS last_at
                FROM chats c
                LEFT JOIN LATERAL (
                  SELECT m.id, m.text, m.created_at
                  FROM chat_messages m
                  WHERE m.chat_id = c.id AND m.is_deleted = false
                  ORDER BY m.id DESC
                  LIMIT 1
                ) lm ON true
                WHERE c.type = 'group'
                  AND NOT EXISTS (
                    SELECT 1 FROM chat_members cm WHERE cm.chat_id = c.id AND cm.user_id = :uid
                  )
                  AND (
                    c.group_settings_json IS NULL OR trim(c.group_settings_json) = ''
                    OR COALESCE((c.group_settings_json::jsonb ->> 'is_public')::boolean, true) = true
                  )
                ORDER BY lm.created_at DESC NULLS LAST, c.id DESC
                LIMIT 150
                """
            ),
            {"uid": uid},
        )
    except Exception:
        discover = []
    for r in discover or []:
        cid = int(r["id"])
        if cid in member_chat_ids:
            continue
        last_text = (r["last_text"] or "").strip()
        if len(last_text) > 120:
            last_text = last_text[:117] + "…"
        out.append(
            {
                "id": cid,
                "type": "group",
                "name": (r["name"] or "").strip() or "Группа",
                "avatar_url": r["avatar_url"],
                "partner_id": None,
                "last_message": last_text or "Публичная группа — нажмите, чтобы вступить",
                "last_at": r["last_at"].isoformat() if r.get("last_at") else None,
                "unread": 0,
                "needs_join": True,
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
    mem = await _member_row(chat_id, uid)
    my_role = (mem.get("role") or "member") if mem else "member"
    mute = bool(mem.get("mute_notifications")) if mem else False
    adel = None
    if mem and mem.get("auto_delete_ttl_seconds") is not None:
        try:
            adel = int(mem["auto_delete_ttl_seconds"])
        except (TypeError, ValueError):
            adel = None
    payload: dict[str, Any] = {
        "id": chat_id,
        "type": ctype,
        "name": title or "Чат",
        "avatar_url": avatar,
        "member_count": int(member_count or 0),
        "partner": partner,
        "online_user_ids": online,
        "my_role": my_role,
        "mute_notifications": mute,
        "auto_delete_ttl_seconds": adel,
    }
    if ctype == "personal" and partner:
        pid = int(partner["id"])
        payload["dm_blocked_by_partner"] = await is_dm_blocked(blocker_id=pid, blocked_id=uid)
        payload["dm_i_blocked_partner"] = await is_dm_blocked(blocker_id=uid, blocked_id=pid)
    if ctype == "group":
        st = _parse_group_settings(crow.get("group_settings_json") if crow else None)
        admin_n = await database.fetch_val(
            sa.text(
                "SELECT COUNT(*) FROM chat_members WHERE chat_id = :c AND role IN ('owner','admin')"
            ),
            {"c": chat_id},
        )
        ban_n = await database.fetch_val(
            sa.text("SELECT COUNT(*) FROM chat_group_bans WHERE chat_id = :c"),
            {"c": chat_id},
        )
        perms = st.get("permissions") or {}
        perm_on = sum(1 for k in PERMISSION_KEYS if perms.get(k, True))
        payload.update(
            {
                "description": ((crow.get("description") or "") if crow else "").strip(),
                "group_settings": st,
                "created_by": int(crow["created_by"] or 0) if crow else 0,
                "admin_count": int(admin_n or 0),
                "ban_count": int(ban_n or 0),
                "permissions_score": f"{perm_on}/{len(PERMISSION_KEYS)}",
                "can_manage_members": my_role in ("owner", "admin"),
            }
        )
    return JSONResponse(payload)


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
    around_message_id: int | None = Query(None),
):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    cutoff = await _viewer_message_cutoff_datetime(chat_id, uid)
    ac = ""
    apx: dict[str, Any] = {}
    if cutoff is not None:
        ac = " AND m.created_at >= :auto_cutoff "
        apx["auto_cutoff"] = cutoff

    half = max(1, min(limit // 2, 50))
    rows: list[Any] = []
    before_rows: list[Any] = []
    after_rows: list[Any] = []
    if around_message_id is not None:
        anchor = await database.fetch_one(
            sa.text(
                f"""
                SELECT id FROM chat_messages
                WHERE id = :mid AND chat_id = :cid AND is_deleted = false
                {ac}
                """
            ),
            {"mid": around_message_id, "cid": chat_id, **apx},
        )
        if not anchor:
            return JSONResponse({"error": "not_found"}, status_code=404)
        aid = int(anchor["id"])
        before_rows = await database.fetch_all(
            sa.text(
                f"""
                SELECT m.id, m.chat_id, m.user_id, m.text, m.media_url, m.reply_to_id,
                       m.is_edited, m.is_deleted, m.created_at,
                       u.name AS sender_name, u.avatar AS sender_avatar
                FROM chat_messages m
                JOIN users u ON u.id = m.user_id
                WHERE m.chat_id = :cid AND m.is_deleted = false AND m.id < :aid
                {ac}
                ORDER BY m.id DESC
                LIMIT :lim
                """
            ),
            {"cid": chat_id, "aid": aid, "lim": half, **apx},
        )
        after_rows = await database.fetch_all(
            sa.text(
                f"""
                SELECT m.id, m.chat_id, m.user_id, m.text, m.media_url, m.reply_to_id,
                       m.is_edited, m.is_deleted, m.created_at,
                       u.name AS sender_name, u.avatar AS sender_avatar
                FROM chat_messages m
                JOIN users u ON u.id = m.user_id
                WHERE m.chat_id = :cid AND m.is_deleted = false AND m.id > :aid
                {ac}
                ORDER BY m.id ASC
                LIMIT :lim2
                """
            ),
            {"cid": chat_id, "aid": aid, "lim2": half, **apx},
        )
        center = await database.fetch_one(
            sa.text(
                f"""
                SELECT m.id, m.chat_id, m.user_id, m.text, m.media_url, m.reply_to_id,
                       m.is_edited, m.is_deleted, m.created_at,
                       u.name AS sender_name, u.avatar AS sender_avatar
                FROM chat_messages m
                JOIN users u ON u.id = m.user_id
                WHERE m.id = :aid AND m.chat_id = :cid AND m.is_deleted = false
                {ac}
                """
            ),
            {"aid": aid, "cid": chat_id, **apx},
        )
        rows = list(reversed(before_rows))
        if center:
            rows.append(center)
        rows.extend(after_rows)
    else:
        params: dict[str, Any] = {"cid": chat_id, "lim": limit, **apx}
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
                {ac}
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
            await mark_chat_viewed(uid, chat_id, max_mid)

    if around_message_id is not None:
        has_more = len(before_rows) >= half or len(after_rows) >= half
    else:
        has_more = len(rows) >= limit

    return JSONResponse({"messages": messages, "has_more": has_more})


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
    if await is_dm_blocked(blocker_id=other_id, blocked_id=uid):
        return JSONResponse({"error": "blocked_by_peer"}, status_code=403)
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
    st = _default_group_settings()
    st["is_public"] = bool(body.is_public)
    await _save_group_settings_row(cid, st)
    return JSONResponse({"chat_id": cid})


@router.post("/api/chats/{chat_id}/join")
async def api_join_public_group(request: Request, chat_id: int):
    """Вступление в публичную группу (без приглашения). Приватные — только через добавление участника."""
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if await _member_row(chat_id, uid):
        return JSONResponse({"ok": True, "already_member": True})
    crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or (crow.get("type") or "") != "group":
        return JSONResponse({"error": "not_group"}, status_code=400)
    st = _parse_group_settings(crow.get("group_settings_json"))
    if not st.get("is_public", True):
        return JSONResponse({"error": "private_group"}, status_code=403)
    ban = await database.fetch_one(
        sa.text("SELECT 1 FROM chat_group_bans WHERE chat_id = :c AND user_id = :u"),
        {"c": chat_id, "u": uid},
    )
    if ban:
        return JSONResponse({"error": "banned"}, status_code=403)
    try:
        await database.execute(
            sa.text(
                "INSERT INTO chat_members (chat_id, user_id, role) VALUES (:c,:u,'member')"
            ),
            {"c": chat_id, "u": uid},
        )
    except Exception:
        return JSONResponse({"error": "join_failed"}, status_code=400)
    await _audit(chat_id, uid, "join_public", {})
    await room_broadcast(
        chat_id,
        {"type": "members_changed", "payload": {"action": "join", "user_id": uid}},
    )
    return JSONResponse({"ok": True})


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
    mem = await _member_row(chat_id, uid)
    if not mem:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crow_probe = await database.fetch_one(sa.text("SELECT type FROM chats WHERE id = :id"), {"id": chat_id})
    if crow_probe and (crow_probe.get("type") or "") == "personal":
        pr = await database.fetch_one(
            sa.text(
                "SELECT user_id FROM chat_members WHERE chat_id = :c AND user_id <> :u LIMIT 1"
            ),
            {"c": chat_id, "u": uid},
        )
        if pr and await is_dm_blocked(blocker_id=int(pr["user_id"]), blocked_id=uid):
            return JSONResponse({"error": "blocked_by_peer"}, status_code=403)
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

    crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    if crow and (crow.get("type") or "") == "group":
        role = mem.get("role") or "member"
        if role not in ("owner", "admin"):
            st = _parse_group_settings(crow.get("group_settings_json"))
            perms = st.get("permissions") or {}
            if not perms.get("send_messages", True):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            if media and not perms.get("send_media", True):
                return JSONResponse({"error": "forbidden"}, status_code=403)

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
    try:
        crow = await database.fetch_one(
            sa.text("SELECT type FROM chats WHERE id = :id"), {"id": chat_id}
        )
        if crow and (crow.get("type") or "") == "personal":
            peers = await database.fetch_all(
                sa.text(
                    "SELECT user_id FROM chat_members WHERE chat_id = :cid AND user_id <> :uid"
                ),
                {"cid": chat_id, "uid": uid},
            )
            actor_name = (srow.get("sender_name") or "").strip() or "Участник"
            prev = (text or "").strip()
            if not prev and media:
                prev = "[медиа]"
            if not prev:
                prev = " "
            link = f"/chats?open_chat={chat_id}"
            for pr in peers:
                peer_id = int(pr["user_id"] or 0)
                if peer_id <= 0:
                    continue
                peer_mem = await _member_row(chat_id, peer_id)
                if peer_mem and bool(peer_mem.get("mute_notifications")):
                    continue
                await create_notification(
                    recipient_id=peer_id,
                    actor_id=uid,
                    ntype="message",
                    title="Личное сообщение",
                    body=f"{actor_name}: {prev[:400]}",
                    link_url=link,
                    source_kind="chat_message",
                    source_id=mid,
                )
                peer_row = await database.fetch_one(users.select().where(users.c.id == peer_id))
                if peer_row and await should_send_telegram_for_event(peer_id, "message"):
                    tg_id = peer_row.get("tg_id") or peer_row.get("linked_tg_id")
                    if tg_id:
                        await notify_user_dm_with_read_button(
                            int(tg_id), actor_name, prev, link
                        )
        elif crow and (crow.get("type") or "") == "group":
            await _notify_group_chat_message(
                chat_id=chat_id,
                sender_id=uid,
                message_id=mid,
                text=text,
                media_url=media,
                reply_to_id=body.reply_to_id,
                sender_name=(srow.get("sender_name") or "").strip() or "Участник",
            )
    except Exception as e:
        logger.warning("chat notify: %s", e)

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
    crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    if crow and (crow.get("type") or "") == "group":
        st = _parse_group_settings(crow.get("group_settings_json"))
        if st.get("reactions_mode") == "none":
            return JSONResponse({"error": "reactions_disabled"}, status_code=403)
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
    mem = await _member_row(chat_id, uid)
    if not mem:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    row = await database.fetch_one(
        sa.text(
            "SELECT user_id FROM chat_messages WHERE id = :id AND chat_id = :cid AND is_deleted = false"
        ),
        {"id": msg_id, "cid": chat_id},
    )
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    author_id = int(row["user_id"])
    if author_id != uid:
        role = mem.get("role") or "member"
        if role not in ("owner", "admin"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
        if not crow or (crow.get("type") or "") != "group":
            return JSONResponse({"error": "forbidden"}, status_code=403)
        st = _parse_group_settings(crow.get("group_settings_json"))
        perms = st.get("permissions") or {}
        if role == "admin" and not perms.get("delete_others_messages", False):
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
    crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or crow["type"] != "group":
        return JSONResponse({"error": "not_group"}, status_code=400)
    oid = int(body.user_id)
    if oid == uid:
        return JSONResponse({"error": "self"}, status_code=400)
    if await _is_banned(chat_id, oid):
        return JSONResponse({"error": "user_banned"}, status_code=403)
    st = _parse_group_settings(crow.get("group_settings_json"))
    if mem["role"] == "admin" and not st.get("permissions", {}).get("invite_members", True):
        return JSONResponse({"error": "forbidden"}, status_code=403)
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
    await _audit(chat_id, uid, "member_add", {"user_id": oid})
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
    await _audit(chat_id, uid, "member_leave", {"user_id": uid})
    await room_broadcast(
        chat_id, {"type": "members_changed", "payload": {"action": "leave", "user_id": uid}}
    )
    return JSONResponse({"ok": True})


@router.get("/api/chats/{chat_id}/media")
async def api_chat_media(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    cutoff = await _viewer_message_cutoff_datetime(chat_id, uid)
    extra_where = ""
    params: dict[str, Any] = {"cid": chat_id}
    if cutoff is not None:
        extra_where = " AND m.created_at >= :auto_cutoff "
        params["auto_cutoff"] = cutoff
    rows = await database.fetch_all(
        sa.text(
            f"""
            SELECT m.id AS message_id, m.media_url, m.created_at, m.user_id, u.name AS sender_name
            FROM chat_messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.chat_id = :cid AND m.is_deleted = false
              AND m.media_url IS NOT NULL AND TRIM(m.media_url) <> ''
              {extra_where}
            ORDER BY m.id DESC
            LIMIT 500
            """
        ),
        params,
    )
    items = [
        {
            "message_id": int(r["message_id"]),
            "media_url": r["media_url"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "user_id": int(r["user_id"]),
            "sender_name": r.get("sender_name"),
        }
        for r in rows
    ]
    return JSONResponse({"items": items})


@router.get("/api/chats/{chat_id}/messages/search")
async def api_messages_search(request: Request, chat_id: int, q: str = ""):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    qn = (q or "").strip()[:200]
    if len(qn) < 1:
        return JSONResponse({"results": []})
    like = f"%{qn}%"
    cutoff = await _viewer_message_cutoff_datetime(chat_id, uid)
    extra_where = ""
    sparams: dict[str, Any] = {"cid": chat_id, "like": like, "pref": f"{qn}%"}
    if cutoff is not None:
        extra_where = " AND m.created_at >= :auto_cutoff "
        sparams["auto_cutoff"] = cutoff
    rows = await database.fetch_all(
        sa.text(
            f"""
            SELECT m.id, m.text, m.created_at, u.name AS sender_name
            FROM chat_messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.chat_id = :cid AND m.is_deleted = false
              AND (
                LOWER(COALESCE(m.text, '')) LIKE LOWER(:like)
                OR CAST(m.id AS TEXT) LIKE :pref
              )
              {extra_where}
            ORDER BY m.id DESC
            LIMIT 50
            """
        ),
        sparams,
    )
    return JSONResponse(
        {
            "results": [
                {
                    "id": int(r["id"]),
                    "text": (r.get("text") or "")[:240],
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                    "sender_name": r.get("sender_name"),
                }
                for r in rows
            ]
        }
    )


@router.get("/api/chats/{chat_id}/partner-profile")
async def api_chat_partner_profile(request: Request, chat_id: int):
    """Карточка собеседника для личного чата (шапка переписки)."""
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    partner_id = await _personal_partner_user_id(chat_id, uid)
    if partner_id is None:
        return JSONResponse({"error": "not_personal"}, status_code=400)
    raw = await database.fetch_one(users.select().where(users.c.id == partner_id))
    if not raw:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if raw.get("primary_user_id"):
        primary = await database.fetch_one(
            users.select().where(users.c.id == int(raw["primary_user_id"]))
        )
        if primary:
            raw = primary
            partner_id = int(primary["id"])
    cpro = await database.fetch_one(
        sa.select(community_profiles.c.display_name, community_profiles.c.bio).where(
            community_profiles.c.user_id == partner_id
        )
    )
    profile = get_public_user_data(dict(raw))
    apply_token_privacy_for_viewer(profile, uid, partner_id)
    display_name = profile.get("name") or f"User {partner_id}"
    if cpro and (cpro.get("display_name") or "").strip():
        display_name = (cpro.get("display_name") or "").strip()
    if cpro and cpro.get("bio") and not (profile.get("bio") or "").strip():
        profile["bio"] = cpro.get("bio")
    post_count = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(community_posts)
        .where(community_posts.c.user_id == partner_id)
        .where(community_posts.c.approved == True)
    ) or 0
    post_rows = await database.fetch_all(
        sa.select(
            community_posts.c.id,
            community_posts.c.title,
            community_posts.c.content,
            community_posts.c.image_url,
            community_posts.c.created_at,
        )
        .where(community_posts.c.user_id == partner_id)
        .where(community_posts.c.approved == True)
        .order_by(community_posts.c.created_at.desc())
        .limit(20)
    )
    recent_posts = []
    for pr in post_rows:
        body = (pr.get("content") or "")[:160]
        recent_posts.append(
            {
                "id": int(pr["id"]),
                "title": (pr.get("title") or "")[:200],
                "snippet": body,
                "image_url": pr.get("image_url"),
                "created_at": pr["created_at"].isoformat() if pr.get("created_at") else None,
            }
        )
    ls_raw = await database.fetch_one(
        sa.select(users.c.last_seen_at).where(users.c.id == partner_id)
    )
    last_seen_at = None
    if ls_raw and ls_raw.get("last_seen_at") is not None:
        v = ls_raw["last_seen_at"]
        last_seen_at = v.isoformat() if hasattr(v, "isoformat") else None
    return JSONResponse(
        {
            "partner_id": partner_id,
            "display_name": display_name,
            "profile": profile,
            "post_count": int(post_count),
            "recent_posts": recent_posts,
            "profile_url": f"/community/profile/{partner_id}",
            "last_seen_at": last_seen_at,
        }
    )


@router.get("/api/chats/{chat_id}/messages/links")
async def api_chat_messages_links(request: Request, chat_id: int):
    """Сообщения чата, в тексте которых есть ссылка (http, www., t.me/…)."""
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    cutoff = await _viewer_message_cutoff_datetime(chat_id, uid)
    extra_where = ""
    lparams: dict[str, Any] = {"cid": chat_id}
    if cutoff is not None:
        extra_where = " AND m.created_at >= :auto_cutoff "
        lparams["auto_cutoff"] = cutoff
    rows = await database.fetch_all(
        sa.text(
            f"""
            SELECT m.id, m.text, m.created_at, m.user_id, u.name AS sender_name
            FROM chat_messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.chat_id = :cid AND m.is_deleted = false
              AND m.text IS NOT NULL AND TRIM(m.text) <> ''
              AND (
                COALESCE(m.text, '') ~* 'https?://'
                OR COALESCE(m.text, '') ~* 'www\\.'
                OR COALESCE(m.text, '') ~* 't\\.me/'
              )
              {extra_where}
            ORDER BY m.id DESC
            LIMIT 200
            """
        ),
        lparams,
    )
    return JSONResponse(
        {
            "results": [
                {
                    "id": int(r["id"]),
                    "text": (r.get("text") or "")[:400],
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                    "sender_name": r.get("sender_name"),
                    "user_id": int(r["user_id"]),
                    "urls": _urls_from_message_text(r.get("text")),
                }
                for r in rows
            ]
        }
    )


@router.patch("/api/chats/{chat_id}/members/me/mute")
async def api_chat_mute_me(request: Request, chat_id: int, body: MuteBody):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        sa.text(
            """
            UPDATE chat_members SET mute_notifications = :m
            WHERE chat_id = :c AND user_id = :u
            """
        ),
        {"m": bool(body.muted), "c": chat_id, "u": uid},
    )
    await _audit(chat_id, uid, "mute_toggle", {"muted": bool(body.muted)})
    return JSONResponse({"ok": True, "mute_notifications": bool(body.muted)})


@router.patch("/api/chats/{chat_id}/members/me/settings")
async def api_chat_member_me_settings(
    request: Request, chat_id: int, body: MemberMeSettingsBody
):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    v = body.auto_delete_ttl_seconds
    if v is not None and v != 0 and int(v) not in ALLOWED_AUTO_DELETE_TTL:
        return JSONResponse({"error": "invalid_ttl"}, status_code=400)
    if v is None or int(v or 0) == 0:
        await database.execute(
            sa.text(
                """
                UPDATE chat_members SET auto_delete_ttl_seconds = NULL
                WHERE chat_id = :c AND user_id = :u
                """
            ),
            {"c": chat_id, "u": uid},
        )
        saved = None
    else:
        sec = int(v)
        await database.execute(
            sa.text(
                """
                UPDATE chat_members SET auto_delete_ttl_seconds = :sec
                WHERE chat_id = :c AND user_id = :u
                """
            ),
            {"sec": sec, "c": chat_id, "u": uid},
        )
        saved = sec
    await _audit(chat_id, uid, "auto_delete_set", {"ttl_seconds": saved})
    return JSONResponse({"ok": True, "auto_delete_ttl_seconds": saved})


@router.post("/api/chats/{chat_id}/clear-history")
async def api_chat_clear_history(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crow = await database.fetch_one(sa.text("SELECT type FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or (crow.get("type") or "") != "personal":
        return JSONResponse({"error": "not_personal"}, status_code=400)
    await database.execute(
        sa.text("DELETE FROM chat_messages WHERE chat_id = :c"),
        {"c": chat_id},
    )
    await _audit(chat_id, uid, "clear_history", {})
    await room_broadcast(chat_id, {"type": "history_cleared", "payload": {}})
    return JSONResponse({"ok": True})


@router.delete("/api/chats/{chat_id}/personal-dialog")
async def api_delete_personal_dialog(request: Request, chat_id: int):
    """Удалить личный диалог для обоих участников: чат и все сообщения удаляются из БД."""
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crow = await database.fetch_one(sa.text("SELECT type FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or (crow.get("type") or "") != "personal":
        return JSONResponse({"error": "not_personal"}, status_code=400)
    await room_broadcast(
        chat_id,
        {"type": "chat_deleted", "payload": {"chat_id": chat_id}},
    )
    await database.execute(sa.text("DELETE FROM chats WHERE id = :id"), {"id": chat_id})
    return JSONResponse({"ok": True})


@router.post("/api/chats/{chat_id}/dm-block")
async def api_dm_block_chat(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    partner_id = await _personal_partner_user_id(chat_id, uid)
    if partner_id is None:
        return JSONResponse({"error": "not_personal"}, status_code=400)
    await dm_block_user(blocker_id=uid, blocked_id=partner_id)
    await _audit(chat_id, uid, "dm_block", {"blocked_user_id": partner_id})
    return JSONResponse({"ok": True})


@router.delete("/api/chats/{chat_id}/dm-block")
async def api_dm_unblock_chat(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if not await _member_row(chat_id, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    partner_id = await _personal_partner_user_id(chat_id, uid)
    if partner_id is None:
        return JSONResponse({"error": "not_personal"}, status_code=400)
    await dm_unblock_user(blocker_id=uid, blocked_id=partner_id)
    await _audit(chat_id, uid, "dm_unblock", {"unblocked_user_id": partner_id})
    return JSONResponse({"ok": True})


@router.patch("/api/chats/{chat_id}/group")
async def api_patch_group(request: Request, chat_id: int, body: GroupPatchBody):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    mem = await _member_row(chat_id, uid)
    if not mem:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    role = mem.get("role") or "member"
    if role not in ("owner", "admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or (crow.get("type") or "") != "group":
        return JSONResponse({"error": "not_group"}, status_code=400)
    st = _parse_group_settings(crow.get("group_settings_json"))
    if role == "admin" and not st.get("permissions", {}).get("edit_group_info", False):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if body.name is not None:
        await database.execute(
            sa.text("UPDATE chats SET name = :n WHERE id = :id"),
            {"n": body.name.strip(), "id": chat_id},
        )
    if body.description is not None:
        await database.execute(
            sa.text("UPDATE chats SET description = :d WHERE id = :id"),
            {"d": body.description.strip(), "id": chat_id},
        )
    if body.avatar_url is not None:
        av = (body.avatar_url or "").strip() or None
        await database.execute(
            sa.text("UPDATE chats SET avatar_url = :a WHERE id = :id"),
            {"a": av, "id": chat_id},
        )

    settings_changed = False
    if body.is_public is not None:
        st["is_public"] = bool(body.is_public)
        settings_changed = True
    if body.reactions_mode is not None:
        if body.reactions_mode not in ("all", "none"):
            return JSONResponse({"error": "bad_reactions_mode"}, status_code=400)
        st["reactions_mode"] = body.reactions_mode
        settings_changed = True
    if body.appearance is not None:
        if body.appearance not in ("cyan", "gold", "violet"):
            return JSONResponse({"error": "bad_appearance"}, status_code=400)
        st["appearance"] = body.appearance
        settings_changed = True
    if body.topics_enabled is not None:
        st["topics_enabled"] = bool(body.topics_enabled)
        settings_changed = True
    if body.linked_channel_label is not None:
        st["linked_channel_label"] = body.linked_channel_label.strip()[:255]
        settings_changed = True
    if body.permissions is not None:
        merged = dict(st.get("permissions") or _default_permissions())
        for k, v in body.permissions.items():
            if k in PERMISSION_KEYS:
                merged[k] = bool(v)
        st["permissions"] = merged
        settings_changed = True
    if settings_changed:
        await _save_group_settings_row(chat_id, st)

    await _audit(chat_id, uid, "group_patch", {"fields": [k for k, v in body.model_dump().items() if v is not None]})
    crow2 = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    return JSONResponse(
        {
            "ok": True,
            "name": crow2.get("name") if crow2 else None,
            "description": (crow2.get("description") or "").strip() if crow2 else "",
            "avatar_url": crow2.get("avatar_url") if crow2 else None,
            "group_settings": _parse_group_settings(crow2.get("group_settings_json") if crow2 else None),
        }
    )


@router.delete("/api/chats/{chat_id}/members/{target_user_id}")
async def api_kick_member(request: Request, chat_id: int, target_user_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    if target_user_id == uid:
        return JSONResponse({"error": "use_leave"}, status_code=400)
    req_mem = await _member_row(chat_id, uid)
    if not req_mem or req_mem.get("role") not in ("owner", "admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    tgt = await _member_row(chat_id, target_user_id)
    if not tgt:
        return JSONResponse({"error": "not_member"}, status_code=404)
    if (tgt.get("role") or "") == "owner":
        return JSONResponse({"error": "cannot_kick_owner"}, status_code=403)
    if req_mem.get("role") == "admin" and (tgt.get("role") or "") == "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        sa.text("DELETE FROM chat_members WHERE chat_id = :c AND user_id = :u"),
        {"c": chat_id, "u": target_user_id},
    )
    await _audit(chat_id, uid, "member_kick", {"user_id": target_user_id})
    await room_broadcast(
        chat_id,
        {"type": "members_changed", "payload": {"action": "kick", "user_id": target_user_id}},
    )
    return JSONResponse({"ok": True})


@router.post("/api/chats/{chat_id}/members/{target_user_id}/role")
async def api_set_member_role(request: Request, chat_id: int, target_user_id: int, body: SetRoleBody):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    req_mem = await _member_row(chat_id, uid)
    if not req_mem or req_mem.get("role") != "owner":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or (crow.get("type") or "") != "group":
        return JSONResponse({"error": "not_group"}, status_code=400)
    tgt = await _member_row(chat_id, target_user_id)
    if not tgt:
        return JSONResponse({"error": "not_member"}, status_code=404)
    if (tgt.get("role") or "") == "owner":
        return JSONResponse({"error": "bad_target"}, status_code=400)
    new_role = "admin" if body.role == "admin" else "member"
    await database.execute(
        sa.text(
            "UPDATE chat_members SET role = :r WHERE chat_id = :c AND user_id = :u"
        ),
        {"r": new_role, "c": chat_id, "u": target_user_id},
    )
    await _audit(chat_id, uid, "role_change", {"user_id": target_user_id, "role": new_role})
    await room_broadcast(chat_id, {"type": "members_changed", "payload": {"action": "role", "user_id": target_user_id}})
    return JSONResponse({"ok": True, "role": new_role})


@router.get("/api/chats/{chat_id}/bans")
async def api_list_bans(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    mem = await _member_row(chat_id, uid)
    if not mem or mem.get("role") not in ("owner", "admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT b.user_id, b.created_at, u.name, u.avatar
            FROM chat_group_bans b
            JOIN users u ON u.id = b.user_id
            WHERE b.chat_id = :c
            ORDER BY b.created_at DESC
            """
        ),
        {"c": chat_id},
    )
    return JSONResponse(
        {
            "bans": [
                {
                    "user_id": int(r["user_id"]),
                    "name": r.get("name"),
                    "avatar": r.get("avatar"),
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                }
                for r in rows
            ]
        }
    )


@router.post("/api/chats/{chat_id}/bans")
async def api_ban_user(request: Request, chat_id: int, body: BanBody):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    mem = await _member_row(chat_id, uid)
    if not mem or mem.get("role") not in ("owner", "admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crow = await database.fetch_one(sa.text("SELECT * FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or (crow.get("type") or "") != "group":
        return JSONResponse({"error": "not_group"}, status_code=400)
    st = _parse_group_settings(crow.get("group_settings_json"))
    if mem.get("role") == "admin" and not st.get("permissions", {}).get("ban_members", True):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    tid = int(body.user_id)
    if tid == uid:
        return JSONResponse({"error": "self"}, status_code=400)
    tgt = await _member_row(chat_id, tid)
    if tgt and (tgt.get("role") or "") == "owner":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if mem.get("role") == "admin" and tgt and (tgt.get("role") or "") == "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        sa.text(
            """
            INSERT INTO chat_group_bans (chat_id, user_id, banned_by)
            VALUES (:c, :u, :by)
            ON CONFLICT (chat_id, user_id) DO NOTHING
            """
        ),
        {"c": chat_id, "u": tid, "by": uid},
    )
    await database.execute(
        sa.text("DELETE FROM chat_members WHERE chat_id = :c AND user_id = :u"),
        {"c": chat_id, "u": tid},
    )
    await _audit(chat_id, uid, "ban_add", {"user_id": tid})
    await room_broadcast(chat_id, {"type": "members_changed", "payload": {"action": "ban", "user_id": tid}})
    return JSONResponse({"ok": True})


@router.delete("/api/chats/{chat_id}/bans/{ban_user_id}")
async def api_unban_user(request: Request, chat_id: int, ban_user_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    mem = await _member_row(chat_id, uid)
    if not mem or mem.get("role") not in ("owner", "admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        sa.text("DELETE FROM chat_group_bans WHERE chat_id = :c AND user_id = :u"),
        {"c": chat_id, "u": ban_user_id},
    )
    await _audit(chat_id, uid, "ban_remove", {"user_id": ban_user_id})
    return JSONResponse({"ok": True})


@router.get("/api/chats/{chat_id}/audit")
async def api_chat_audit(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    mem = await _member_row(chat_id, uid)
    if not mem or mem.get("role") not in ("owner", "admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT a.id, a.actor_id, a.action, a.detail, a.created_at, u.name AS actor_name
            FROM chat_group_audit a
            LEFT JOIN users u ON u.id = a.actor_id
            WHERE a.chat_id = :c
            ORDER BY a.id DESC
            LIMIT 120
            """
        ),
        {"c": chat_id},
    )
    return JSONResponse(
        {
            "events": [
                {
                    "id": int(r["id"]),
                    "actor_id": int(r["actor_id"]) if r.get("actor_id") is not None else None,
                    "actor_name": r.get("actor_name"),
                    "action": r.get("action"),
                    "detail": r.get("detail"),
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                }
                for r in rows
            ]
        }
    )


@router.delete("/api/chats/{chat_id}")
async def api_delete_group_chat(request: Request, chat_id: int):
    user = await _require_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _eff_uid(user)
    mem = await _member_row(chat_id, uid)
    if not mem or mem.get("role") != "owner":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crow = await database.fetch_one(sa.text("SELECT type FROM chats WHERE id = :id"), {"id": chat_id})
    if not crow or (crow.get("type") or "") != "group":
        return JSONResponse({"error": "not_group"}, status_code=400)
    await database.execute(sa.text("DELETE FROM chats WHERE id = :id"), {"id": chat_id})
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
    crow_ws = await database.fetch_one(sa.text("SELECT type FROM chats WHERE id = :id"), {"id": chat_id})
    if crow_ws and (crow_ws.get("type") or "") == "personal":
        pid_ws = await _personal_partner_user_id(chat_id, uid)
        if pid_ws is not None and await is_dm_blocked(blocker_id=pid_ws, blocked_id=uid):
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
