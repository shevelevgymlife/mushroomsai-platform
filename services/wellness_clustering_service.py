"""K-means по средним векторам состояний пользователей; cluster_id в users."""
from __future__ import annotations

import json
import logging
import random
from datetime import timedelta
from typing import Any, Optional

import sqlalchemy as sa

from db.database import database
from db.models import users, wellness_cluster_models, wellness_user_state_daily
from services.wellness_insights_service import _utc_today
from services.wellness_state_service import fetch_user_mean_feature_vector

logger = logging.getLogger(__name__)

_DIM = 7


def _kmeans_lloyd(
    points: list[list[float]], k: int, *, max_iter: int = 35, seed: int = 42
) -> tuple[list[list[float]], list[int]]:
    rng = random.Random(seed)
    n = len(points)
    if n == 0:
        return [], []
    k = max(1, min(k, n))
    idxs = rng.sample(range(n), k)
    centroids = [points[i][:] for i in idxs]
    dim = len(points[0])
    assignments = [0] * n
    for _ in range(max_iter):
        for i, p in enumerate(points):
            best_d, best_j = 1e18, 0
            for j, c in enumerate(centroids):
                d = sum((p[t] - c[t]) ** 2 for t in range(dim))
                if d < best_d:
                    best_d, best_j = d, j
            assignments[i] = best_j
        new_c = [[0.0] * dim for _ in range(k)]
        counts = [0] * k
        for i, p in enumerate(points):
            j = assignments[i]
            for t in range(dim):
                new_c[j][t] += p[t]
            counts[j] += 1
        moved = False
        for j in range(k):
            if counts[j] > 0:
                for t in range(dim):
                    nc = new_c[j][t] / counts[j]
                    if abs(nc - centroids[j][t]) > 1e-6:
                        moved = True
                    centroids[j][t] = nc
            else:
                centroids[j] = rng.choice(points)[:]
                moved = True
        if not moved:
            break
    return centroids, assignments


async def run_wellness_kmeans_job(
    *,
    min_users: int = 10,
    days: int = 14,
) -> dict[str, Any]:
    since = _utc_today() - timedelta(days=days + 3)
    urows = await database.fetch_all(
        sa.select(wellness_user_state_daily.c.user_id)
        .where(wellness_user_state_daily.c.state_date >= since)
        .distinct()
    )
    uids = [int(r["user_id"]) for r in urows]
    points: list[list[float]] = []
    uid_for_point: list[int] = []
    for uid in uids:
        row = await database.fetch_one(
            users.select().where(users.c.id == uid).where(users.c.primary_user_id.is_(None))
        )
        if not row:
            continue
        v = await fetch_user_mean_feature_vector(uid, days=days)
        if v is not None and len(v) == _DIM:
            points.append(v)
            uid_for_point.append(uid)
    if len(points) < min_users:
        return {"ok": True, "skipped": True, "reason": "not_enough_users", "n": len(points)}
    k = min(8, max(2, len(points) // 25))
    if k < 2:
        k = 2
    centroids, assign = _kmeans_lloyd(points, k)
    mv = await database.fetch_val(sa.select(sa.func.max(wellness_cluster_models.c.model_version)))
    next_ver = int(mv or 0) + 1
    for uid, cid in zip(uid_for_point, assign):
        await database.execute(
            users.update().where(users.c.id == uid).values(wellness_kmeans_cluster_id=int(cid))
        )
    await database.execute(
        wellness_cluster_models.insert().values(
            k=k,
            model_version=next_ver,
            centroids_json=json.dumps(centroids, ensure_ascii=False),
            user_count=len(points),
        )
    )
    logger.info("wellness k-means: users=%s k=%s version=%s", len(points), k, next_ver)
    return {"ok": True, "skipped": False, "k": k, "users": len(points), "model_version": next_ver}


async def latest_cluster_model_summary() -> Optional[dict[str, Any]]:
    row = await database.fetch_one(
        wellness_cluster_models.select().order_by(wellness_cluster_models.c.id.desc()).limit(1)
    )
    return dict(row) if row else None
