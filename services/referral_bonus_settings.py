"""Глобальные и персональные проценты реферального бонуса за подписки: 1-я и 2-я линия."""
from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from db.database import database
from db.models import users

logger = logging.getLogger(__name__)

LINE1_KEY = "referral_bonus_line1_percent"
LINE2_KEY = "referral_bonus_line2_percent"
LEGACY_GLOBAL_KEY = "referral_bonus_percent_global"

DEFAULT_LINE1 = 5.0
DEFAULT_LINE2 = 5.0


def _clamp_percent(raw: Any) -> float:
    try:
        v = float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, v))


def _fmt_setting(v: float) -> str:
    v = _clamp_percent(v)
    return str(int(v)) if abs(v - round(v)) < 1e-6 else f"{v:.2f}".rstrip("0").rstrip(".")


async def _get_setting(key: str, default: float) -> float:
    try:
        row = await database.fetch_one(
            sa.text("SELECT value FROM site_settings WHERE key = :k"),
            {"k": key},
        )
        if row and row.get("value") is not None and str(row["value"]).strip() != "":
            return _clamp_percent(row["value"])
    except Exception:
        logger.debug("get_setting %s failed", key, exc_info=True)
    return default


async def _set_setting(key: str, percent: float) -> None:
    v = _fmt_setting(percent)
    await database.execute(
        sa.text(
            """
            INSERT INTO site_settings (key, value, updated_at)
            VALUES (:k, :v, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """
        ),
        {"k": key, "v": v},
    )


async def get_referral_bonus_line1_global() -> float:
    return await _get_setting(LINE1_KEY, DEFAULT_LINE1)


async def get_referral_bonus_line2_global() -> float:
    return await _get_setting(LINE2_KEY, DEFAULT_LINE2)


async def set_referral_bonus_lines_global(line1_percent: float, line2_percent: float) -> None:
    await _set_setting(LINE1_KEY, line1_percent)
    await _set_setting(LINE2_KEY, line2_percent)


async def get_effective_referrer_bonus_line1_percent(referrer_user_id: int) -> float:
    row = await database.fetch_one(users.select().where(users.c.id == int(referrer_user_id)))
    if row:
        o = row.get("referral_bonus_line1_override")
        if o is not None:
            try:
                return _clamp_percent(float(o))
            except (TypeError, ValueError):
                pass
        # legacy single override → обе линии
        leg = row.get("referral_bonus_percent_override")
        if leg is not None:
            try:
                return _clamp_percent(float(leg))
            except (TypeError, ValueError):
                pass
    return await get_referral_bonus_line1_global()


async def get_effective_referrer_bonus_line2_percent(referrer_user_id: int) -> float:
    row = await database.fetch_one(users.select().where(users.c.id == int(referrer_user_id)))
    if row:
        o = row.get("referral_bonus_line2_override")
        if o is not None:
            try:
                return _clamp_percent(float(o))
            except (TypeError, ValueError):
                pass
        leg = row.get("referral_bonus_percent_override")
        if leg is not None:
            try:
                return _clamp_percent(float(leg))
            except (TypeError, ValueError):
                pass
    return await get_referral_bonus_line2_global()


# Совместимость со старым кодом (одно число = сумма двух линий для подсказок)
async def get_referral_bonus_percent_global() -> float:
    a = await get_referral_bonus_line1_global()
    b = await get_referral_bonus_line2_global()
    return _clamp_percent(a + b)


async def set_referral_bonus_percent_global(percent: float) -> None:
    """Устарело: делит поровну между линиями (для скриптов). Предпочтительно set_referral_bonus_lines_global."""
    half = _clamp_percent(percent) / 2.0
    await set_referral_bonus_lines_global(half, half)


async def get_effective_referrer_bonus_percent(referrer_user_id: int) -> float:
    """Сумма эффективных % по 1-й и 2-й линии (для коротких подсказок)."""
    a = await get_effective_referrer_bonus_line1_percent(int(referrer_user_id))
    b = await get_effective_referrer_bonus_line2_percent(int(referrer_user_id))
    return _clamp_percent(a + b)


async def set_user_referral_bonus_line_overrides(
    user_id: int,
    line1: float | None,
    line2: float | None,
) -> None:
    uid = int(user_id)
    if line1 is None and line2 is None:
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(
                referral_bonus_line1_override=None,
                referral_bonus_line2_override=None,
                referral_bonus_percent_override=None,
            )
        )
        return
    vals: dict = {}
    if line1 is not None:
        vals["referral_bonus_line1_override"] = _clamp_percent(line1)
    if line2 is not None:
        vals["referral_bonus_line2_override"] = _clamp_percent(line2)
    await database.execute(users.update().where(users.c.id == uid).values(**vals))


async def set_user_referral_bonus_percent_override(user_id: int, percent: float | None) -> None:
    """Совместимость: одно число записывается в обе линии + legacy-колонку."""
    uid = int(user_id)
    if percent is None:
        await set_user_referral_bonus_line_overrides(uid, None, None)
        return
    v = _clamp_percent(percent)
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(
            referral_bonus_percent_override=v,
            referral_bonus_line1_override=v,
            referral_bonus_line2_override=v,
        )
    )


async def list_users_with_bonus_override(limit: int = 200) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        users.select()
        .where(
            sa.or_(
                users.c.referral_bonus_line1_override.isnot(None),
                users.c.referral_bonus_line2_override.isnot(None),
                users.c.referral_bonus_percent_override.isnot(None),
            )
        )
        .where(users.c.primary_user_id.is_(None))
        .order_by(users.c.id.asc())
        .limit(int(limit))
    )
    g1 = await get_referral_bonus_line1_global()
    g2 = await get_referral_bonus_line2_global()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        uid = int(d["id"])
        l1o = d.get("referral_bonus_line1_override")
        l2o = d.get("referral_bonus_line2_override")
        lego = d.get("referral_bonus_percent_override")
        eff1 = float(l1o) if l1o is not None else (float(lego) if lego is not None else g1)
        eff2 = float(l2o) if l2o is not None else (float(lego) if lego is not None else g2)
        out.append(
            {
                "id": uid,
                "name": (d.get("name") or "").strip() or f"#{uid}",
                "line1": _clamp_percent(eff1),
                "line2": _clamp_percent(eff2),
                "has_explicit_pair": l1o is not None or l2o is not None or lego is not None,
            }
        )
    return out
