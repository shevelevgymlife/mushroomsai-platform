"""Блокировки в личных сообщениях (односторонние)."""
from __future__ import annotations

import sqlalchemy as sa

from db.database import database


async def is_dm_blocked(*, blocker_id: int, blocked_id: int) -> bool:
    if blocker_id <= 0 or blocked_id <= 0 or blocker_id == blocked_id:
        return False
    row = await database.fetch_one(
        sa.text(
            """
            SELECT 1 FROM dm_user_blocks
            WHERE blocker_id = :b AND blocked_id = :v
            LIMIT 1
            """
        ),
        {"b": blocker_id, "v": blocked_id},
    )
    return row is not None


async def dm_block_user(blocker_id: int, blocked_id: int) -> None:
    if blocker_id <= 0 or blocked_id <= 0 or blocker_id == blocked_id:
        return
    await database.execute(
        sa.text(
            """
            INSERT INTO dm_user_blocks (blocker_id, blocked_id)
            VALUES (:b, :v)
            ON CONFLICT (blocker_id, blocked_id) DO NOTHING
            """
        ),
        {"b": blocker_id, "v": blocked_id},
    )


async def dm_unblock_user(blocker_id: int, blocked_id: int) -> None:
    await database.execute(
        sa.text("DELETE FROM dm_user_blocks WHERE blocker_id = :b AND blocked_id = :v"),
        {"b": blocker_id, "v": blocked_id},
    )
