"""
Чтение строки community_groups без ORM (устойчиво, если v12 ещё не накатили).
"""
from __future__ import annotations

from typing import Any, Optional

import sqlalchemy as sa

from db.database import database


async def fetch_community_group_row(group_id: int) -> Optional[dict[str, Any]]:
    try:
        row = await database.fetch_one(
            sa.text(
                "SELECT id, name, description, created_at, created_by, join_mode, message_retention_days, "
                "slow_mode_seconds, show_history_to_new_members "
                "FROM community_groups WHERE id = :gid"
            ).bindparams(gid=group_id)
        )
        if row:
            return dict(row)
    except Exception:
        pass
    try:
        row = await database.fetch_one(
            sa.text(
                "SELECT id, name, description, created_at, created_by, join_mode, message_retention_days "
                "FROM community_groups WHERE id = :gid"
            ).bindparams(gid=group_id)
        )
        if not row:
            return None
        d = dict(row)
        d.setdefault("slow_mode_seconds", None)
        d.setdefault("show_history_to_new_members", True)
        return d
    except Exception:
        return None
