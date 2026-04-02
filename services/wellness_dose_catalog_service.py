"""Структурированные строки доз из БД (сид из KB); подмена текстов в памятке."""
from __future__ import annotations

import json
import logging
import sqlalchemy as sa

from db.database import database
from db.models import wellness_mushroom_dose_rules
from services.mushroom_therapy_kb import MUSHROOM_PLAN_MEMO, MUSHROOMS

logger = logging.getLogger(__name__)


def _compose_dose_line(mushroom_key: str) -> str:
    spec = MUSHROOMS.get(mushroom_key) or {}
    memo = MUSHROOM_PLAN_MEMO.get(mushroom_key, {})
    dose_line = (spec.get("dose_hint") or "").strip()
    if memo.get("dose_addendum"):
        dose_line = (dose_line + " " + memo["dose_addendum"]).strip()
    cw = (memo.get("course_weeks") or "").strip()
    if cw:
        dose_line = (dose_line + " Курс (ориентир): " + cw).strip()
    return dose_line


async def ensure_mushroom_dose_seed() -> int:
    n = 0
    cnt = await database.fetch_val(sa.select(sa.func.count()).select_from(wellness_mushroom_dose_rules))
    if int(cnt or 0) > 0:
        return 0
    for mk in MUSHROOMS:
        text = _compose_dose_line(mk)
        if not text:
            continue
        await database.execute(
            wellness_mushroom_dose_rules.insert().values(
                mushroom_key=mk,
                form="general",
                dose_text_ru=text[:8000],
                course_weeks_hint=(MUSHROOM_PLAN_MEMO.get(mk) or {}).get("course_weeks"),
                cautions_ru=(MUSHROOMS.get(mk) or {}).get("contra"),
                sort_order=n,
            )
        )
        n += 1
    if n:
        logger.info("wellness_mushroom_dose_rules: seeded %s rows", n)
    return n


async def dose_text_map_for_mushroom_keys(keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    rows = await database.fetch_all(
        wellness_mushroom_dose_rules.select().where(wellness_mushroom_dose_rules.c.mushroom_key.in_(keys))
    )
    out: dict[str, str] = {}
    for r in rows:
        k = r.get("mushroom_key")
        t = (r.get("dose_text_ru") or "").strip()
        if k and t:
            out[str(k)] = t
    return out
