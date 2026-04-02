"""Явные дневные состояния (вектор признаков + дискретная метка) поверх снимков."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from db.database import database
from db.models import wellness_daily_snapshots, wellness_user_state_daily
from services.mushroom_therapy_kb import infer_heuristic_cluster, normalize_metrics_from_m

logger = logging.getLogger(__name__)


def _build_state_payload(merged_metrics: dict[str, Any]) -> tuple[dict[str, Any], str]:
    norm = normalize_metrics_from_m(merged_metrics)
    vec = {k: v for k, v in norm.items() if v is not None}
    label = infer_heuristic_cluster(norm)
    return vec, label


async def upsert_wellness_user_state_daily(
    user_id: int,
    state_date: date,
    merged_metrics: dict[str, Any],
    *,
    source: str = "snapshot",
    kmeans_cluster_id: Optional[int] = None,
) -> None:
    vec, label = _build_state_payload(merged_metrics)
    uid = int(user_id)
    payload = json.dumps(vec, ensure_ascii=False)
    ex = await database.fetch_one(
        wellness_user_state_daily.select()
        .where(wellness_user_state_daily.c.user_id == uid)
        .where(wellness_user_state_daily.c.state_date == state_date)
    )
    now = datetime.utcnow()
    vals = {
        "feature_vector_json": payload,
        "discrete_state_label": label[:160],
        "source": (source or "snapshot")[:32],
        "created_at": now,
    }
    if kmeans_cluster_id is not None:
        vals["kmeans_cluster_id"] = int(kmeans_cluster_id)
    if ex:
        upd = {**vals}
        if kmeans_cluster_id is None:
            upd.pop("kmeans_cluster_id", None)
        await database.execute(
            wellness_user_state_daily.update()
            .where(wellness_user_state_daily.c.id == ex["id"])
            .values(**{k: v for k, v in upd.items() if k != "created_at"})
        )
    else:
        await database.execute(
            wellness_user_state_daily.insert().values(
                user_id=uid,
                state_date=state_date,
                kmeans_cluster_id=kmeans_cluster_id,
                **vals,
            )
        )


async def backfill_wellness_user_state_from_snapshots(days: int = 45) -> int:
    """Заполнить wellness_user_state_daily из wellness_daily_snapshots за период (идемпотентно по UPSERT)."""
    from services.wellness_insights_service import _utc_today  # noqa: PLC0415

    since = _utc_today().toordinal() - int(days)
    since_d = date.fromordinal(since)
    rows = await database.fetch_all(
        wellness_daily_snapshots.select()
        .where(wellness_daily_snapshots.c.snapshot_date >= since_d)
        .order_by(wellness_daily_snapshots.c.user_id, wellness_daily_snapshots.c.snapshot_date)
    )
    n = 0
    for r in rows:
        try:
            m = json.loads(r["metrics_json"] or "{}")
        except json.JSONDecodeError:
            m = {}
        if not isinstance(m, dict):
            continue
        try:
            await upsert_wellness_user_state_daily(
                int(r["user_id"]),
                r["snapshot_date"],
                m,
                source="snapshot_backfill",
            )
            n += 1
        except Exception:
            logger.debug("wellness_state backfill row skip", exc_info=True)
    return n


async def fetch_user_mean_feature_vector(user_id: int, days: int = 14) -> Optional[list[float]]:
    """Средний нормализованный вектор (фиксированный порядок координат) для кластеризации."""
    from services.wellness_insights_service import _utc_today  # noqa: PLC0415

    since = _utc_today() - timedelta(days=days)
    rows = await database.fetch_all(
        wellness_user_state_daily.select()
        .where(wellness_user_state_daily.c.user_id == int(user_id))
        .where(wellness_user_state_daily.c.state_date >= since)
        .order_by(wellness_user_state_daily.c.state_date.desc())
        .limit(21)
    )
    if len(rows) < 2:
        return None
    keys = ("anxiety", "energy", "sleep", "focus", "stress", "immunity", "fatigue")
    acc = [0.0] * len(keys)
    cnt = 0
    for r in rows:
        try:
            d = json.loads(r["feature_vector_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        ok = False
        for i, k in enumerate(keys):
            v = d.get(k)
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            acc[i] += fv
            ok = True
        if ok:
            cnt += 1
    if cnt < 2:
        return None
    return [x / cnt for x in acc]
