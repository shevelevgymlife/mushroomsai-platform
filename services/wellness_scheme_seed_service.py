"""Каталог схем и черновик A/B эксперимента (данные для states/schemes/experiments)."""
from __future__ import annotations

import json
import logging

from db.database import database
from db.models import wellness_experiments, wellness_scheme_catalog
from services.mushroom_therapy_kb import BUNDLES

logger = logging.getLogger(__name__)


async def ensure_wellness_scheme_catalog_seeded() -> int:
    n = 0
    for bid, spec in BUNDLES.items():
        row = await database.fetch_one(
            wellness_scheme_catalog.select().where(wellness_scheme_catalog.c.scheme_key == bid)
        )
        if row:
            continue
        await database.execute(
            wellness_scheme_catalog.insert().values(
                scheme_key=bid,
                title=(spec.get("title") or bid)[:500],
                description=(spec.get("rationale") or "")[:4000],
                bundle_ids_json=json.dumps([bid], ensure_ascii=False),
                is_active=True,
            )
        )
        n += 1
    if n:
        logger.info("wellness_scheme_catalog: inserted %s rows", n)
    return n


async def ensure_default_experiment_row() -> None:
    row = await database.fetch_one(
        wellness_experiments.select().where(wellness_experiments.c.experiment_key == "ab_stress_vs_energy")
    )
    if row:
        return
    await database.execute(
        wellness_experiments.insert().values(
            experiment_key="ab_stress_vs_energy",
            title="Черновик: анти-стресс vs энергия+мозг",
            scheme_a_key="anti_stress",
            scheme_b_key="energy_brain",
            status="draft",
            config_json=json.dumps({"note": "Назначение рук через админку/логику позже"}, ensure_ascii=False),
        )
    )
    logger.info("wellness_experiments: seeded default draft")
