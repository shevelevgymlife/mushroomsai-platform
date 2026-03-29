"""Кто может подтверждать заявки на доступ к боту обучающих постов и смотреть список выданных прав."""
from __future__ import annotations

import sqlalchemy as sa

from auth.owner import SUPER_ADMIN_TG_ID, is_platform_owner
from config import settings
from db.database import database
from db.models import admin_permissions, users


async def training_bot_notifier_chat_ids() -> list[int]:
    """Chat ID для рассылки заявок (подтверждающие)."""
    ids: list[int] = list(static_approver_telegram_ids())
    rows = await database.fetch_all(
        sa.select(users.c.tg_id, users.c.linked_tg_id)
        .select_from(users.join(admin_permissions, admin_permissions.c.user_id == users.c.id))
        .where(users.c.primary_user_id.is_(None))
        .where(admin_permissions.c.can_training_bot.is_(True))
    )
    for r in rows:
        for k in ("tg_id", "linked_tg_id"):
            v = r.get(k)
            if v is not None:
                try:
                    ids.append(int(v))
                except (TypeError, ValueError):
                    pass
    seen: set[int] = set()
    out: list[int] = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _tg_eq(a, b) -> bool:
    if a is None or b is None:
        return False
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def static_approver_telegram_ids() -> list[int]:
    """Жёсткий список + ADMIN_TG_ID + SUPER_ADMIN + опционально TRAINING_BOT_APPROVER_TG_IDS."""
    out: list[int] = []
    raw = (getattr(settings, "TRAINING_BOT_APPROVER_TG_IDS", "") or "").strip()
    if raw:
        for part in raw.split(","):
            p = part.strip()
            if p.lstrip("-").isdigit():
                out.append(int(p))
    aid = int(getattr(settings, "ADMIN_TG_ID", 0) or 0)
    if aid:
        out.append(aid)
    if SUPER_ADMIN_TG_ID and SUPER_ADMIN_TG_ID not in out:
        out.append(int(SUPER_ADMIN_TG_ID))
    seen: set[int] = set()
    uniq: list[int] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


async def is_training_bot_approver_telegram(tg_id: int) -> bool:
    """Может ли этот Telegram подтверждать заявки и отзывать доступ."""
    tid = int(tg_id)
    for x in static_approver_telegram_ids():
        if int(x) == tid:
            return True
    row = await database.fetch_one(
        users.select().where(sa.or_(users.c.tg_id == tid, users.c.linked_tg_id == tid))
    )
    if not row:
        return False
    u = dict(row)
    if u.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == u["primary_user_id"]))
        if primary:
            u = dict(primary)
    if is_platform_owner(u):
        return True
    prow = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == u["id"])
    )
    if prow and prow.get("can_training_bot"):
        return True
    return False
