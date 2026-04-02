"""RL-lite: статистика по связкам (руки бандита) из прогресса снимков + явные оценки в ЛС."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from db.database import database
from db.models import wellness_bundle_feedback, wellness_daily_snapshots, wellness_rec_arm_stats
from services.mushroom_therapy_kb import normalize_metrics_from_m, suggest_therapy_payload
from services.wellness_insights_service import _utc_today

logger = logging.getLogger(__name__)


async def increment_rec_arm_stats(
    bundle_key: str,
    segment: str,
    *,
    successes_delta: int,
    trials_delta: int,
) -> None:
    """Точечное обновление (админ/скрипты). Основной пересчёт — refresh_wellness_rec_arm_stats_full."""
    if trials_delta <= 0:
        return
    bk = (bundle_key or "")[:64]
    seg = (segment or "")[:80]
    await database.execute(
        sa.text(
            """
            INSERT INTO wellness_rec_arm_stats (bundle_key, segment, successes, trials, updated_at)
            VALUES (:bk, :seg, :succ, :tri, NOW())
            ON CONFLICT (bundle_key, segment) DO UPDATE SET
              successes = wellness_rec_arm_stats.successes + EXCLUDED.successes,
              trials = wellness_rec_arm_stats.trials + EXCLUDED.trials,
              updated_at = NOW()
            """
        ),
        {"bk": bk, "seg": seg, "succ": int(successes_delta), "tri": int(trials_delta)},
    )


async def refresh_wellness_rec_arm_stats_full(since_days: int = 60) -> int:
    """Полный пересчёт: снимки (дельты) + все строки feedback; как refresh_scheme_effect_stats."""
    since = _utc_today() - timedelta(days=int(since_days))
    agg: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)

    rows = await database.fetch_all(
        wellness_daily_snapshots.select()
        .where(wellness_daily_snapshots.c.snapshot_date >= since)
        .order_by(wellness_daily_snapshots.c.user_id, wellness_daily_snapshots.c.snapshot_date)
    )
    by_uid: dict[int, list[Any]] = defaultdict(list)
    for r in rows:
        by_uid[int(r["user_id"])].append(r)
    for _uid, lst in by_uid.items():
        for i in range(1, len(lst)):
            prev, cur = lst[i - 1], lst[i]
            try:
                p = json.loads(prev["metrics_json"] or "{}")
                c = json.loads(cur["metrics_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(p, dict) or not isinstance(c, dict):
                continue
            try:
                m0 = float(c.get("mood_0_10")) if c.get("mood_0_10") is not None else None
                m1 = float(p.get("mood_0_10")) if p.get("mood_0_10") is not None else None
                e0 = float(c.get("energy_0_10")) if c.get("energy_0_10") is not None else None
                e1 = float(p.get("energy_0_10")) if p.get("energy_0_10") is not None else None
            except (TypeError, ValueError):
                continue
            score = 0.0
            npt = 0
            if m0 is not None and m1 is not None:
                score += m0 - m1
                npt += 1
            if e0 is not None and e1 is not None:
                score += e0 - e1
                npt += 1
            if npt == 0:
                continue
            score /= npt
            seg = str(dict(cur).get("wellness_segment") or dict(prev).get("wellness_segment") or "")[:80]
            norm = normalize_metrics_from_m(c)
            pl = suggest_therapy_payload(norm)
            succ = 1 if score > 0 else 0
            for b in (pl.get("bundles") or [])[:4]:
                bid = (b.get("id") or "").strip()
                if not bid:
                    continue
                agg[(bid, seg)].append((succ, 1))

    fb_rows = await database.fetch_all(wellness_bundle_feedback.select())
    for r in fb_rows:
        bid = (r.get("bundle_id") or "")[:64]
        if not bid:
            continue
        v = int(r.get("vote") or 0)
        succ = 1 if v > 0 else 0
        agg[(bid, "")].append((succ, 1))

    await database.execute(wellness_rec_arm_stats.delete())
    n = 0
    for (bk, seg), pairs in agg.items():
        s = sum(x[0] for x in pairs)
        t = sum(x[1] for x in pairs)
        if t <= 0:
            continue
        await database.execute(
            wellness_rec_arm_stats.insert().values(
                bundle_key=bk,
                segment=seg,
                successes=int(s),
                trials=int(t),
            )
        )
        n += 1
    logger.info("wellness_rec_arm_stats: rebuilt rows=%s", n)
    return n


async def list_rec_arm_stats(limit: int = 40) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        wellness_rec_arm_stats.select()
        .order_by(wellness_rec_arm_stats.c.trials.desc())
        .limit(int(limit))
    )
    return [dict(x) for x in rows]


def thompson_sample_rank(bundle_keys: list[str], stats_by_key: dict[str, tuple[int, int]]) -> list[str]:
    import random

    rng = random.Random()
    scored: list[tuple[float, str]] = []
    for k in bundle_keys:
        s, t = stats_by_key.get(k, (0, 0))
        a = 1.0 + max(0, int(s))
        b = 1.0 + max(0, int(t) - int(s))
        x = rng.betavariate(a, b)
        scored.append((x, k))
    scored.sort(key=lambda z: -z[0])
    return [k for _, k in scored]
