"""Глобальный и персональный процент реферального бонуса (подписки)."""
from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from db.database import database
from db.models import users

logger = logging.getLogger(__name__)

SETTINGS_KEY = "referral_bonus_percent_global"
DEFAULT_PERCENT = 10.0


def _clamp_percent(raw: Any) -> float:
    try:
        v = float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return DEFAULT_PERCENT
    return max(0.0, min(100.0, v))


async def get_referral_bonus_percent_global() -> float:
    try:
        row = await database.fetch_one(
            sa.text("SELECT value FROM site_settings WHERE key = :k"),
            {"k": SETTINGS_KEY},
        )
        if row and row.get("value") is not None and str(row["value"]).strip() != "":
            return _clamp_percent(row["value"])
    except Exception:
        logger.debug("get_referral_bonus_percent_global failed", exc_info=True)
    return DEFAULT_PERCENT


async def set_referral_bonus_percent_global(percent: float) -> None:
    v = _clamp_percent(percent)
    s = str(int(v)) if abs(v - round(v)) < 1e-6 else f"{v:.2f}".rstrip("0").rstrip(".")
    await database.execute(
        sa.text(
            """
            INSERT INTO site_settings (key, value, updated_at)
            VALUES (:k, :v, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """
        ),
        {"k": SETTINGS_KEY, "v": s},
    )


async def get_effective_referrer_bonus_percent(referrer_user_id: int) -> float:
    """Процент для реферера: override в users или глобальный."""
    row = await database.fetch_one(users.select().where(users.c.id == int(referrer_user_id)))
    if row:
        o = row.get("referral_bonus_percent_override")
        if o is not None:
            try:
                return _clamp_percent(float(o))
            except (TypeError, ValueError):
                pass
    return await get_referral_bonus_percent_global()


async def set_user_referral_bonus_percent_override(user_id: int, percent: float | None) -> None:
    """None — сброс, использовать глобальный процент."""
    uid = int(user_id)
    if percent is None:
        await database.execute(
            users.update().where(users.c.id == uid).values(referral_bonus_percent_override=None)
        )
        return
    v = _clamp_percent(percent)
    await database.execute(
        users.update().where(users.c.id == uid).values(referral_bonus_percent_override=v)
    )


async def list_users_with_bonus_override(limit: int = 200) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        users.select()
        .where(users.c.referral_bonus_percent_override.isnot(None))
        .where(users.c.primary_user_id.is_(None))
        .order_by(users.c.id.asc())
        .limit(int(limit))
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        out.append(
            {
                "id": int(d["id"]),
                "name": (d.get("name") or "").strip() or f"#{d['id']}",
                "percent": float(d.get("referral_bonus_percent_override") or 0),
            }
        )
    return out
