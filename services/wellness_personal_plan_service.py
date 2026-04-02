"""Контекст страницы «Личная памятка»: эвристики по дневнику, без медицинских назначений."""
from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from db.database import database
from db.models import users, wellness_journal_entries
from services.mushroom_therapy_kb import (
    build_memo_rows_from_profile,
    build_stored_profile_json,
    format_normalized_metrics_ru,
)
from services.wellness_ai_profile_service import load_wellness_ai_profile_dict
from services.wellness_dose_catalog_service import dose_text_map_for_mushroom_keys
from services.wellness_insights_service import (
    compute_segment_for_user,
    fetch_snapshots_series,
    latest_recommendation_text,
)
from services.wellness_retention_automation_service import get_user_automation


async def _fetch_plan_goals_from_journal(user_id: int) -> dict[str, Any]:
    rows = await database.fetch_all(
        wellness_journal_entries.select()
        .where(wellness_journal_entries.c.user_id == int(user_id))
        .where(wellness_journal_entries.c.role == "user_reply")
        .order_by(wellness_journal_entries.c.created_at.desc())
        .limit(100)
    )
    out: dict[str, Any] = {
        "life_goal_short": None,
        "motivation_why_mushrooms": None,
        "trigger_or_distortion": None,
        "free_summary": None,
        "dose_notes": None,
        "dosage_amount_text": None,
        "timing": None,
        "physical_symptoms": [],
        "mental_symptoms": [],
    }
    phys_seen: set[str] = set()
    ment_seen: set[str] = set()
    max_sym = 24
    for r in rows:
        raw = r.get("extracted_json")
        if not raw:
            continue
        try:
            p = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(p, dict):
            continue
        for key in (
            "life_goal_short",
            "motivation_why_mushrooms",
            "trigger_or_distortion",
            "free_summary",
            "dose_notes",
            "dosage_amount_text",
            "timing",
        ):
            if out[key] is None:
                v = p.get(key)
                if isinstance(v, str) and v.strip():
                    out[key] = v.strip()
        for sym in p.get("physical_symptoms") or []:
            if isinstance(sym, str):
                s = sym.strip()
                if s and s not in phys_seen and len(out["physical_symptoms"]) < max_sym:
                    phys_seen.add(s)
                    out["physical_symptoms"].append(s)
        for sym in p.get("mental_symptoms") or []:
            if isinstance(sym, str):
                s = sym.strip()
                if s and s not in ment_seen and len(out["mental_symptoms"]) < max_sym:
                    ment_seen.add(s)
                    out["mental_symptoms"].append(s)
    return out


def _snapshot_metrics_nonempty(m: dict[str, Any]) -> bool:
    for v in m.values():
        if v is None or v == "" or v == []:
            continue
        return True
    return False


async def build_wellness_personal_plan_context(user_id: int) -> dict[str, Any]:
    uid = int(user_id)
    prof = await load_wellness_ai_profile_dict(uid)
    profile_source = "saved"
    if prof and (prof.get("bundles") or prof.get("single_hints")):
        pass
    else:
        series = await fetch_snapshots_series(uid, 28)
        last_m: dict[str, Any] = {}
        if series:
            last_m = series[-1].get("m") or {}
        if isinstance(last_m, dict) and _snapshot_metrics_nonempty(last_m):
            prof = build_stored_profile_json(last_m)
            profile_source = "snapshot"
        else:
            prof = prof or {}
            profile_source = "empty"

    segment = (prof.get("wellness_segment_snapshot") or "").strip() if prof else ""
    if not segment:
        segment = await compute_segment_for_user(uid)

    memo_rows = build_memo_rows_from_profile(prof) if prof else []
    if memo_rows:
        try:
            dmap = await dose_text_map_for_mushroom_keys([str(r["key"]) for r in memo_rows])
            for r in memo_rows:
                dt = dmap.get(str(r.get("key") or ""))
                if dt:
                    r["dose_orientation"] = dt
        except Exception:
            pass
    norm = prof.get("normalized_metrics") if isinstance(prof, dict) else None
    norm_lines = format_normalized_metrics_ru(norm) if isinstance(norm, dict) else []

    goals = await _fetch_plan_goals_from_journal(uid)
    rec = await latest_recommendation_text(uid)
    automation = await get_user_automation(uid)

    return {
        "profile_source": profile_source,
        "profile_updated_at": prof.get("updated_at") if prof else None,
        "cluster_label": prof.get("cluster_label") if prof else None,
        "triggers_fired": (prof.get("triggers_fired") or []) if prof else [],
        "normalized_metrics_lines": norm_lines,
        "segment_display": segment,
        "bundles": (prof.get("bundles") or []) if prof else [],
        "memo_rows": memo_rows,
        "goals": goals,
        "latest_ai_recommendation": rec,
        "has_plan_content": bool(memo_rows),
        "wellness_automation": automation,
    }
