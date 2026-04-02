"""Профиль пользователя для AI: эвристики грибов/связок, JSON в users.wellness_ai_profile_json."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import sqlalchemy as sa

from db.database import database
from db.models import users
from services.mushroom_therapy_kb import build_stored_profile_json, therapy_panel_from_stored
from services.wellness_insights_service import compute_segment_for_user

logger = logging.getLogger(__name__)


async def refresh_wellness_ai_profile(user_id: int, merged_metrics: dict[str, Any]) -> None:
    """Обновить JSON-профиль после снимка (метрики уже слитые за день)."""
    uid = int(user_id)
    try:
        seg = await compute_segment_for_user(uid)
        blob = build_stored_profile_json(merged_metrics)
        blob["wellness_segment_snapshot"] = seg
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(wellness_ai_profile_json=json.dumps(blob, ensure_ascii=False))
        )
    except Exception:
        logger.exception("wellness_ai_profile: refresh failed uid=%s", uid)


async def load_wellness_ai_profile_dict(user_id: int) -> Optional[dict[str, Any]]:
    row = await database.fetch_one(
        sa.select(users.c.wellness_ai_profile_json).where(users.c.id == int(user_id))
    )
    if not row:
        return None
    raw = row.get("wellness_ai_profile_json")
    if not raw or not str(raw).strip():
        return None
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


async def therapy_dashboard_panel(user_id: int) -> dict[str, Any]:
    stored = await load_wellness_ai_profile_dict(user_id)
    if not stored:
        return {"show": False}
    return therapy_panel_from_stored(stored)
