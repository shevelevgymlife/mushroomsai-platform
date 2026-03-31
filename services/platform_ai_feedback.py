"""Пожелания к платформе из ответов в NeuroFungi AI — админка и ответ от имени AI."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import sqlalchemy as sa

from db.database import database
from db.models import direct_messages, platform_ai_feedback, users
from services.legacy_dm_chat_sync import sync_direct_messages_pair
from services.system_support_delivery import resolve_neurofungi_ai_user_id

logger = logging.getLogger(__name__)


async def record_platform_ai_feedback(
    user_id: int,
    user_role: str,
    raw_text: str,
    *,
    source: str = "user_reply",
) -> Optional[int]:
    t = (raw_text or "").strip()
    if len(t) < 8:
        return None
    row = await database.fetch_one_write(
        platform_ai_feedback.insert()
        .values(
            user_id=int(user_id),
            user_role=(user_role or "user")[:20],
            raw_text=t[:12000],
            source=((source or "")[:48] if source else None),
        )
        .returning(platform_ai_feedback.c.id)
    )
    return int(row["id"]) if row and row.get("id") is not None else None


async def list_platform_ai_feedback(limit: int = 200) -> list[dict]:
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT f.id, f.user_id, f.user_role, f.raw_text, f.source, f.admin_reply, f.admin_reply_at,
                   f.created_at, u.name AS user_name, u.email AS user_email
            FROM platform_ai_feedback f
            JOIN users u ON u.id = f.user_id
            ORDER BY f.created_at DESC
            LIMIT :lim
            """
        ),
        {"lim": int(limit)},
    )
    return [dict(r) for r in rows]


async def set_admin_reply(feedback_id: int, reply_text: str) -> bool:
    t = (reply_text or "").strip()
    if not t:
        return False
    await database.execute(
        platform_ai_feedback.update()
        .where(platform_ai_feedback.c.id == int(feedback_id))
        .values(admin_reply=t[:12000], admin_reply_at=datetime.utcnow()),
    )
    return True


async def deliver_admin_reply_as_neurofungi_dm(user_id: int, body_plain: str) -> bool:
    """Отправить ответ пользователю в ЛС от имени NeuroFungi AI."""
    coach = await resolve_neurofungi_ai_user_id()
    if not coach:
        return False
    target = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not target:
        return False
    notify_uid = int(target.get("primary_user_id") or user_id)
    body = "🍄 NeuroFungi AI\n\n" + (body_plain or "").strip()
    try:
        dm_row = await database.fetch_one_write(
            direct_messages.insert()
            .values(
                sender_id=int(coach),
                recipient_id=notify_uid,
                text=body,
                is_read=False,
                is_system=False,
            )
            .returning(direct_messages.c.id)
        )
        mid = int(dm_row["id"]) if dm_row and dm_row.get("id") else None
        if mid:
            await sync_direct_messages_pair(int(coach), int(notify_uid), broadcast_legacy_dm_id=mid)
        return True
    except Exception:
        logger.exception("deliver_admin_reply_as_neurofungi_dm failed uid=%s", user_id)
        return False
