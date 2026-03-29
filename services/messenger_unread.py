"""Единая логика непрочитанных ЛС: новый мессенджер (chat_*) + старые direct_messages без зеркала в чатах."""
from __future__ import annotations

import sqlalchemy as sa

from db.database import database

_CHAT_UNREAD_SQL = """
SELECT COUNT(*) FROM chat_messages m
JOIN chat_members cm ON cm.chat_id = m.chat_id AND cm.user_id = :uid
WHERE m.is_deleted = false
  AND m.user_id != :uid
  AND m.id > COALESCE(cm.last_read_message_id, 0)
"""

_STANDALONE_DM_SQL = """
SELECT COUNT(*) FROM direct_messages dm
WHERE dm.recipient_id = :uid
  AND dm.is_system = false
  AND dm.is_read = false
  AND NOT EXISTS (
    SELECT 1 FROM chat_messages m WHERE m.legacy_direct_message_id = dm.id
  )
"""


async def count_chat_unread(uid: int) -> int:
    return int(await database.fetch_val(sa.text(_CHAT_UNREAD_SQL), {"uid": uid}) or 0)


async def count_standalone_direct_unread(uid: int) -> int:
    """Сообщения только в legacy-таблице (ещё не импортированы в chats)."""
    return int(await database.fetch_val(sa.text(_STANDALONE_DM_SQL), {"uid": uid}) or 0)


async def mark_chat_viewed(uid: int, chat_id: int, max_message_id: int) -> None:
    """После открытия диалога: last_read, legacy direct_messages, уведомления «ЛС» по этим сообщениям."""
    if max_message_id <= 0:
        return
    await database.execute(
        sa.text(
            """
            UPDATE chat_members
            SET last_read_message_id = GREATEST(COALESCE(last_read_message_id, 0), :mid)
            WHERE chat_id = :cid AND user_id = :uid
            """
        ),
        {"mid": max_message_id, "cid": chat_id, "uid": uid},
    )
    await database.execute(
        sa.text(
            """
            UPDATE direct_messages
            SET is_read = true
            WHERE recipient_id = :uid
              AND is_system = false
              AND id IN (
                SELECT m.legacy_direct_message_id
                FROM chat_messages m
                WHERE m.chat_id = :cid
                  AND m.is_deleted = false
                  AND m.legacy_direct_message_id IS NOT NULL
                  AND m.id <= :mid
              )
            """
        ),
        {"uid": uid, "cid": chat_id, "mid": max_message_id},
    )
    await database.execute(
        sa.text(
            """
            UPDATE in_app_notifications
            SET read_at = NOW()
            WHERE recipient_id = :uid
              AND read_at IS NULL
              AND source_kind = 'chat_message'
              AND source_id IN (
                SELECT m.id FROM chat_messages m
                WHERE m.chat_id = :cid AND m.is_deleted = false AND m.id <= :mid
              )
            """
        ),
        {"uid": uid, "cid": chat_id, "mid": max_message_id},
    )
