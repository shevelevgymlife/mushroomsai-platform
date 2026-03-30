"""Socket.IO signaling для P2P видеозвонков (клиент: React + socket.io-client)."""
from __future__ import annotations

import logging
import os

import socketio
from auth.session import get_current_user

logger = logging.getLogger(__name__)

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)

_sid_uid: dict[str, int] = {}
_call_registry: dict[str, dict[str, int]] = {}
_room_sids: dict[str, set[str]] = {}


def build_ice_servers() -> list[dict]:
    """Несколько STUN + опциональный TURN из env (симметричный NAT без TURN часто даёт failed)."""
    servers: list[dict] = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
        {"urls": "stun:stun2.l.google.com:19302"},
    ]
    turn_url = (os.environ.get("RTC_TURN_URL") or "").strip()
    turn_user = (os.environ.get("RTC_TURN_USERNAME") or "").strip()
    turn_cred = (os.environ.get("RTC_TURN_CREDENTIAL") or "").strip()
    if turn_url and turn_user and turn_cred:
        servers.append(
            {
                "urls": turn_url,
                "username": turn_user,
                "credential": turn_cred,
            }
        )
    return servers


ICE_SERVERS = build_ice_servers()


def register_call_room(room_id: str, caller_id: int, callee_id: int) -> None:
    _call_registry[room_id] = {"caller_id": caller_id, "callee_id": callee_id}


def get_call_room_meta(room_id: str) -> dict[str, int] | None:
    return _call_registry.get(room_id)


def _parse_cookie_access_token(environ: dict) -> str | None:
    cookie = environ.get("HTTP_COOKIE", "") or ""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("access_token="):
            return part.split("=", 1)[1].strip()
    return None


@sio.event
async def connect(sid, environ):
    token = _parse_cookie_access_token(environ)
    if not token:
        return False
    user = await get_current_user(token)
    if not user:
        return False
    uid = int(user.get("primary_user_id") or user["id"])
    _sid_uid[sid] = uid
    return True


@sio.event
async def disconnect(sid):
    _sid_uid.pop(sid, None)
    for rid, sids in list(_room_sids.items()):
        if sid in sids:
            sids.discard(sid)
            if not sids:
                del _room_sids[rid]


@sio.on("join_room")
async def on_join_room(sid, data):
    data = data or {}
    room_id = data.get("roomId") or data.get("room_id")
    if not room_id or not isinstance(room_id, str):
        await sio.emit("call_error", {"message": "bad_room"}, to=sid)
        return
    meta = _call_registry.get(room_id)
    if not meta:
        await sio.emit("call_error", {"message": "unknown_room"}, to=sid)
        return
    uid = _sid_uid.get(sid)
    if uid is None:
        return
    if uid not in (meta["caller_id"], meta["callee_id"]):
        await sio.emit("call_error", {"message": "forbidden"}, to=sid)
        return
    await sio.enter_room(sid, room_id)
    _room_sids.setdefault(room_id, set()).add(sid)
    n = len(_room_sids[room_id])
    await sio.emit(
        "joined",
        {"peerCount": n, "roomId": room_id, "iceServers": ICE_SERVERS},
        to=sid,
    )
    if n == 2:
        for s in list(_room_sids[room_id]):
            await sio.emit(
                "peer_ready",
                {
                    "roomId": room_id,
                    "isInitiator": _sid_uid.get(s) == meta["caller_id"],
                },
                to=s,
            )


async def _relay(sid, data, event: str):
    data = data or {}
    room_id = data.get("roomId") or data.get("room_id")
    if not room_id:
        return
    uid = _sid_uid.get(sid)
    meta = _call_registry.get(room_id)
    if not meta or uid not in (meta["caller_id"], meta["callee_id"]):
        return
    await sio.emit(event, data, room=room_id, skip_sid=sid)


@sio.on("call-user")
async def on_call_user(sid, data):
    await _relay(sid, data, "call-user")


@sio.on("answer-call")
async def on_answer_call(sid, data):
    await _relay(sid, data, "answer-call")


@sio.on("ice-candidate")
async def on_ice_candidate(sid, data):
    await _relay(sid, data, "ice-candidate")
