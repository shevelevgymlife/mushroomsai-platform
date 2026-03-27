"""
Импорт личных сообщений из direct_messages в мессенджер (chats / chat_messages).
Идемпотентно: по legacy_direct_message_id без дублей.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa

from db.database import database

logger = logging.getLogger(__name__)

_COL_ENSURED = False


async def _ensure_legacy_column() -> None:
    global _COL_ENSURED
    if _COL_ENSURED:
        return
    try:
        await database.execute(
            sa.text(
                "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS legacy_direct_message_id INTEGER UNIQUE"
            )
        )
        _COL_ENSURED = True
    except Exception as e:
        logger.warning("legacy_direct_message_id column: %s", e)


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


async def _create_personal_chat(uid: int, oid: int) -> int:
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
        {"c": cid, "u": uid, "o": oid},
    )
    return cid


async def _recompute_last_read_for_members(chat_id: int, member_uids: list[int]) -> None:
    for u in member_uids:
        mx = await database.fetch_val(
            sa.text(
                """
                SELECT COALESCE(MAX(m.id), 0) FROM chat_messages m
                LEFT JOIN direct_messages dm ON dm.id = m.legacy_direct_message_id
                WHERE m.chat_id = :cid AND m.is_deleted = false
                AND (
                  m.user_id = :member
                  OR (dm.id IS NOT NULL AND dm.recipient_id = :member AND dm.is_read = true)
                )
                """
            ),
            {"cid": chat_id, "member": u},
        )
        await database.execute(
            sa.text(
                """
                UPDATE chat_members
                SET last_read_message_id = GREATEST(COALESCE(last_read_message_id, 0), :mx)
                WHERE chat_id = :cid AND user_id = :member
                """
            ),
            {"cid": chat_id, "member": u, "mx": int(mx or 0)},
        )


async def sync_direct_messages_pair(
    uid: int,
    other_id: int,
    *,
    broadcast_legacy_dm_id: int | None = None,
) -> int | None:
    """
    Подтягивает все direct_messages между uid и other_id в chat_messages.
    Возвращает chat_id или None если пара невалидна.
    """
    await _ensure_legacy_column()
    if other_id <= 0 or uid == other_id:
        return None

    cid = await _find_personal_chat(uid, other_id)
    if not cid:
        cid = await _create_personal_chat(uid, other_id)

    await database.execute(
        sa.text(
            """
            INSERT INTO chat_messages (
              chat_id, user_id, text, media_url, reply_to_id,
              is_edited, is_deleted, created_at, legacy_direct_message_id
            )
            SELECT
              :cid,
              dm.sender_id,
              dm.text,
              NULL,
              NULL,
              false,
              false,
              dm.created_at,
              dm.id
            FROM direct_messages dm
            WHERE dm.is_system = false
              AND (
                (dm.sender_id = :uid AND dm.recipient_id = :oid)
                OR (dm.sender_id = :oid AND dm.recipient_id = :uid)
              )
              AND NOT EXISTS (
                SELECT 1 FROM chat_messages m
                WHERE m.legacy_direct_message_id = dm.id
              )
            """
        ),
        {"cid": cid, "uid": uid, "oid": other_id},
    )

    await _recompute_last_read_for_members(cid, [uid, other_id])

    if broadcast_legacy_dm_id:
        try:
            from services.chat_ws_manager import room_broadcast

            srow = await database.fetch_one(
                sa.text(
                    """
                    SELECT m.id, m.chat_id, m.user_id, m.text, m.media_url, m.reply_to_id,
                           m.is_edited, m.is_deleted, m.created_at,
                           u.name AS sender_name, u.avatar AS sender_avatar
                    FROM chat_messages m
                    JOIN users u ON u.id = m.user_id
                    WHERE m.legacy_direct_message_id = :lid AND m.chat_id = :cid
                    """
                ),
                {"lid": broadcast_legacy_dm_id, "cid": cid},
            )
            if srow:
                mid = int(srow["id"])
                payload = {
                    "id": mid,
                    "chat_id": int(srow["chat_id"]),
                    "user_id": int(srow["user_id"]),
                    "text": srow.get("text"),
                    "media_url": srow.get("media_url"),
                    "reply_to_id": int(srow["reply_to_id"]) if srow.get("reply_to_id") else None,
                    "reply_preview": None,
                    "is_edited": bool(srow.get("is_edited")),
                    "is_deleted": bool(srow.get("is_deleted")),
                    "created_at": srow["created_at"].isoformat() if srow.get("created_at") else None,
                    "sender_name": srow.get("sender_name"),
                    "sender_avatar": srow.get("sender_avatar"),
                    "reactions": {},
                    "my_reactions": [],
                }
                await room_broadcast(cid, {"type": "message", "payload": payload})
        except Exception as e:
            logger.debug("legacy dm ws broadcast: %s", e)

    return cid


async def sync_all_partners_for_user(uid: int) -> None:
    """Все собеседники из direct_messages (не системные) — в списке чатов."""
    await _ensure_legacy_column()
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT DISTINCT CASE
              WHEN dm.sender_id = :uid THEN dm.recipient_id
              ELSE dm.sender_id
            END AS other_id
            FROM direct_messages dm
            WHERE dm.is_system = false
              AND (
                (dm.sender_id = :uid AND dm.recipient_id IS NOT NULL AND dm.recipient_id > 0)
                OR (dm.recipient_id = :uid AND dm.sender_id IS NOT NULL AND dm.sender_id > 0)
              )
            """
        ),
        {"uid": uid},
    )
    for r in rows:
        oid = int(r["other_id"] or 0)
        if oid <= 0 or oid == uid:
            continue
        try:
            await sync_direct_messages_pair(uid, oid)
        except Exception as e:
            logger.warning("sync DM pair %s <-> %s: %s", uid, oid, e)
