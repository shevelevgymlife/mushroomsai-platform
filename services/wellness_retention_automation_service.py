"""Retention-risk, early warning, авто-смена активной связки (флаг в БД)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from db.database import database
from db.models import users, wellness_daily_snapshots, wellness_user_automation
from services.subscription_service import check_subscription
from services.wellness_insights_service import _utc_today

logger = logging.getLogger(__name__)

_AUTO_SCHEME_KEY = "anti_stress"
_STALE_DAYS = 7
_ANX_TH = 7.5
_ENERGY_TH = 4.0


async def _last_snapshot_date(user_id: int) -> Optional[Any]:
    row = await database.fetch_one(
        sa.select(sa.func.max(wellness_daily_snapshots.c.snapshot_date).label("mx"))
        .where(wellness_daily_snapshots.c.user_id == int(user_id))
    )
    if not row or row["mx"] is None:
        return None
    return row["mx"]


async def _recent_anxiety_energy(user_id: int) -> tuple[Optional[float], Optional[float]]:
    import json as _json

    since = _utc_today() - timedelta(days=5)
    rows = await database.fetch_all(
        wellness_daily_snapshots.select()
        .where(wellness_daily_snapshots.c.user_id == int(user_id))
        .where(wellness_daily_snapshots.c.snapshot_date >= since)
        .order_by(wellness_daily_snapshots.c.snapshot_date.desc())
        .limit(5)
    )
    if not rows:
        return None, None
    anx, en = [], []
    for r in rows:
        try:
            m = _json.loads(r["metrics_json"] or "{}")
        except _json.JSONDecodeError:
            continue
        try:
            a = m.get("anxiety_0_10")
            e = m.get("energy_0_10")
            if a is not None:
                anx.append(float(a))
            if e is not None:
                en.append(float(e))
        except (TypeError, ValueError):
            continue
    return (
        sum(anx) / len(anx) if anx else None,
        sum(en) / len(en) if en else None,
    )


async def run_retention_early_warning_and_auto_scheme_job() -> dict[str, Any]:
    since_uid = _utc_today() - timedelta(days=120)
    urows = await database.fetch_all(
        sa.select(wellness_daily_snapshots.c.user_id)
        .where(wellness_daily_snapshots.c.snapshot_date >= since_uid)
        .distinct()
    )
    auto_rows = await database.fetch_all(sa.select(wellness_user_automation.c.user_id))
    uids = {int(r["user_id"]) for r in urows} | {int(r["user_id"]) for r in auto_rows}
    n_warn = 0
    n_ret = 0
    n_auto = 0
    now = datetime.utcnow()
    for uid in uids:
        row = await database.fetch_one(
            users.select().where(users.c.id == uid).where(users.c.primary_user_id.is_(None))
        )
        if not row:
            continue
        plan = await check_subscription(uid)
        if plan == "free" and (row.get("role") or "").strip().lower() != "admin":
            continue
        last_d = await _last_snapshot_date(uid)
        signals: dict[str, Any] = {}
        ret = "low"
        warn_lv = 0
        if last_d is None or (_utc_today() - last_d).days >= _STALE_DAYS:
            ret = "high"
            signals["stale_snapshots"] = True
            n_ret += 1
        am_anx, am_en = await _recent_anxiety_energy(uid)
        if am_anx is not None and am_anx >= _ANX_TH and am_en is not None and am_en <= _ENERGY_TH:
            warn_lv = max(warn_lv, 2)
            signals["high_anxiety_low_energy"] = {"anxiety_avg": am_anx, "energy_avg": am_en}
            n_warn += 1
        elif am_anx is not None and am_anx >= _ANX_TH:
            warn_lv = max(warn_lv, 1)
            signals["elevated_anxiety"] = am_anx

        prev = await database.fetch_one(
            wellness_user_automation.select().where(wellness_user_automation.c.user_id == uid)
        )
        prev_scheme = (prev.get("active_scheme_key") if prev else None) or ""

        auto_switch = bool(warn_lv >= 2 and prev_scheme != _AUTO_SCHEME_KEY)
        if auto_switch:
            n_auto += 1

        base = {
            "early_warning_level": warn_lv,
            "retention_risk": ret[:24],
            "early_warning_signals_json": json.dumps(signals, ensure_ascii=False),
            "updated_at": now,
        }
        if auto_switch:
            base["active_scheme_key"] = _AUTO_SCHEME_KEY
            base["auto_switched_at"] = now
        if prev:
            await database.execute(
                wellness_user_automation.update()
                .where(wellness_user_automation.c.user_id == uid)
                .values(**base)
            )
        else:
            await database.execute(
                wellness_user_automation.insert().values(
                    user_id=uid,
                    active_scheme_key=base.get("active_scheme_key"),
                    auto_switched_at=base.get("auto_switched_at"),
                    early_warning_level=warn_lv,
                    early_warning_signals_json=base["early_warning_signals_json"],
                    retention_risk=base["retention_risk"],
                    updated_at=now,
                )
            )
    return {"users_scanned": len(uids), "retention_high": n_ret, "early_warn": n_warn, "auto_switches": n_auto}


async def get_user_automation(user_id: int) -> Optional[dict[str, Any]]:
    row = await database.fetch_one(
        wellness_user_automation.select().where(wellness_user_automation.c.user_id == int(user_id))
    )
    return dict(row) if row else None
