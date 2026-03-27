"""WebSocket комнаты для мессенджера + heartbeat / presence (in-memory)."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from starlette.websockets import WebSocket

# chat_id -> set of connections
_rooms: dict[int, set[WebSocket]] = {}
_locks: dict[int, asyncio.Lock] = {}
# chat_id -> user_id -> last heartbeat unix time
_presence: dict[int, dict[int, float]] = {}


def _lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _locks:
        _locks[chat_id] = asyncio.Lock()
    return _locks[chat_id]


async def room_connect(chat_id: int, websocket: WebSocket) -> None:
    await websocket.accept()
    async with _lock(chat_id):
        _rooms.setdefault(chat_id, set()).add(websocket)


def room_disconnect(chat_id: int, websocket: WebSocket) -> None:
    if chat_id in _rooms:
        _rooms[chat_id].discard(websocket)
        if not _rooms[chat_id]:
            del _rooms[chat_id]


async def room_broadcast(chat_id: int, payload: dict[str, Any]) -> None:
    async with _lock(chat_id):
        targets = list(_rooms.get(chat_id, set()))
    if not targets:
        return
    raw = json.dumps(payload, default=str)
    dead: list[WebSocket] = []
    for ws in targets:
        try:
            await ws.send_text(raw)
        except Exception:
            dead.append(ws)
    for ws in dead:
        room_disconnect(chat_id, ws)


def touch_presence(chat_id: int, user_id: int) -> None:
    now = time.time()
    _presence.setdefault(chat_id, {})[user_id] = now


def online_user_ids(chat_id: int, window_sec: float = 45.0) -> list[int]:
    now = time.time()
    pr = _presence.get(chat_id, {})
    return sorted([uid for uid, ts in pr.items() if now - ts < window_sec])


def clear_presence_user(chat_id: int, user_id: int) -> None:
    if chat_id in _presence and user_id in _presence[chat_id]:
        del _presence[chat_id][user_id]
