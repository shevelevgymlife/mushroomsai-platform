"""Один проход: состояния, сиды, k-means, bandit-пересчёт, коллективный интеллект, retention/auto-scheme."""
from __future__ import annotations

import logging
from typing import Any

from services.wellness_bandit_service import refresh_wellness_rec_arm_stats_full
from services.wellness_clustering_service import run_wellness_kmeans_job
from services.wellness_dose_catalog_service import ensure_mushroom_dose_seed
from services.wellness_insights_service import refresh_scheme_effect_stats_simple
from services.wellness_retention_automation_service import run_retention_early_warning_and_auto_scheme_job
from services.wellness_scheme_seed_service import ensure_default_experiment_row, ensure_wellness_scheme_catalog_seeded
from services.wellness_state_service import backfill_wellness_user_state_from_snapshots

logger = logging.getLogger(__name__)


async def run_wellness_analytics_pipeline() -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True}
    try:
        out["state_backfill_n"] = await backfill_wellness_user_state_from_snapshots(90)
    except Exception:
        logger.exception("wellness pipeline: state backfill")
        out["state_backfill_n"] = -1
    try:
        out["scheme_seed_n"] = await ensure_wellness_scheme_catalog_seeded()
        await ensure_default_experiment_row()
    except Exception:
        logger.exception("wellness pipeline: scheme seed")
    try:
        out["dose_seed_n"] = await ensure_mushroom_dose_seed()
    except Exception:
        logger.exception("wellness pipeline: dose seed")
    try:
        out["kmeans"] = await run_wellness_kmeans_job()
    except Exception:
        logger.exception("wellness pipeline: kmeans")
        out["kmeans"] = {"ok": False, "error": True}
    try:
        out["rec_arm_rows"] = await refresh_wellness_rec_arm_stats_full(60)
    except Exception:
        logger.exception("wellness pipeline: rec arm stats")
        out["rec_arm_rows"] = -1
    try:
        await refresh_scheme_effect_stats_simple()
    except Exception:
        logger.exception("wellness pipeline: scheme effect refresh")
    try:
        out["retention"] = await run_retention_early_warning_and_auto_scheme_job()
    except Exception:
        logger.exception("wellness pipeline: retention")
        out["retention"] = {}
    return out
