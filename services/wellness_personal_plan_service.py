"""Контекст страницы «Личная памятка»: эвристики по дневнику, без медицинских назначений."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from db.database import database
from db.models import wellness_journal_entries
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


def _compact_text(raw: str, max_len: int = 180) -> str:
    txt = (raw or "").strip()
    if not txt:
        return ""
    if len(txt) <= max_len:
        return txt
    return txt[: max_len - 1].rstrip() + "…"


def _split_role_points(role_text: str) -> list[str]:
    raw = (role_text or "").strip()
    if not raw:
        return []
    work = raw.replace(" • ", "|").replace(" · ", "|").replace(";", "|")
    parts = [p.strip() for p in work.split("|") if p and p.strip()]
    uniq: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq[:4]


def _build_memo_cards(memo_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    color_map = {
        "amanita_muscaria": "muscaria",
        "amanita_pantherina": "panther",
        "amanita_regalis": "regalis",
        "hericium": "hericium",
        "cordyceps": "cordyceps",
        "reishi": "reishi",
        "trametes": "trametes",
        "maitake": "maitake",
        "shiitake": "shiitake",
    }
    cards: list[dict[str, Any]] = []
    for row in memo_rows:
        key = str(row.get("key") or "")
        role_points = _split_role_points(str(row.get("role_for_you") or ""))
        cards.append(
            {
                "key": key,
                "color": color_map.get(key, "muscaria"),
                "name_ru": str(row.get("name_ru") or ""),
                "latin": str(row.get("latin") or ""),
                "core_short": _compact_text(str(row.get("core") or ""), 100),
                "role_points": role_points,
                "who_for": role_points[:2],
                "dose_short": _compact_text(str(row.get("dose_orientation") or ""), 145),
                "dose_full": str(row.get("dose_orientation") or ""),
                "how_apply_short": _compact_text(str(row.get("how_apply") or ""), 145),
                "how_apply_full": str(row.get("how_apply") or ""),
                "course_weeks": str(row.get("course_weeks") or ""),
                "contra_short": _compact_text(str(row.get("contra") or ""), 120),
                "contra_full": str(row.get("contra") or ""),
            }
        )
    return cards


def _format_utc_stamp(raw: Any) -> str:
    if not raw:
        return ""
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        txt = raw.strip()
        if not txt:
            return ""
        try:
            dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        except ValueError:
            return txt[:19] + " UTC"
    else:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _extract_line_by_keywords(text: str, keywords: tuple[str, ...], max_len: int = 220) -> str:
    src = (text or "").replace("\r", "\n")
    parts = [p.strip() for p in src.replace("\n", ". ").split(".") if p and p.strip()]
    kws = tuple(k.lower() for k in keywords if k)
    for p in parts:
        low = p.lower()
        if any(k in low for k in kws):
            return _compact_text(p, max_len)
    return _compact_text(parts[0], max_len) if parts else ""


async def _fetch_training_posts_for_card(card: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    terms = [
        str(card.get("key") or "").replace("_", " ").strip(),
        str(card.get("name_ru") or "").strip(),
        str(card.get("latin") or "").strip(),
    ]
    terms = [t for t in terms if t]
    if not terms:
        return []
    parts: list[str] = []
    params: dict[str, Any] = {"lim": int(max(1, min(12, limit)))}
    for i, t in enumerate(terms):
        k = f"q{i}"
        params[k] = f"%{t}%"
        parts.append(f"(title ILIKE :{k} OR content ILIKE :{k} OR folder ILIKE :{k} OR category ILIKE :{k})")
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT title, content, folder, category, image_url, created_at
            FROM ai_training_posts
            WHERE is_active = true
              AND (
            """
            + " OR ".join(parts)
            + """
              )
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        params,
    )
    return [dict(r) for r in rows]


def _mushroom_aliases(card: dict[str, Any]) -> list[str]:
    key = str(card.get("key") or "").replace("_", " ").strip().lower()
    ru = str(card.get("name_ru") or "").strip().lower()
    latin = str(card.get("latin") or "").strip().lower()
    aliases: list[str] = []
    for raw in (key, ru, latin):
        if raw and raw not in aliases:
            aliases.append(raw)
    if "мухомор красный" in ru:
        aliases.extend(["красный мухомор", "amanita muscaria"])
    if "мухомор пантерный" in ru:
        aliases.extend(["пантерный мухомор", "amanita pantherina"])
    if "ежовик" in ru:
        aliases.extend(["hericium"])
    return list(dict.fromkeys(a.strip().lower() for a in aliases if a.strip()))


async def _mushroom_usage_counts(user_id: int, cards: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {str(c.get("key") or ""): 0 for c in cards}
    aliases_by_key: dict[str, list[str]] = {
        str(c.get("key") or ""): _mushroom_aliases(c) for c in cards
    }
    rows = await database.fetch_all(
        wellness_journal_entries.select()
        .where(wellness_journal_entries.c.user_id == int(user_id))
        .where(wellness_journal_entries.c.role == "user_reply")
        .order_by(wellness_journal_entries.c.created_at.desc())
        .limit(260)
    )
    for row in rows:
        blob_parts: list[str] = []
        raw_text = (row.get("raw_text") or "").strip()
        if raw_text:
            blob_parts.append(raw_text)
        ej = row.get("extracted_json")
        if ej:
            blob_parts.append(str(ej))
            try:
                parsed = json.loads(str(ej))
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                mush = parsed.get("mushrooms")
                if isinstance(mush, list):
                    blob_parts.extend(str(x) for x in mush if x)
                elif isinstance(mush, str):
                    blob_parts.append(mush)
        blob = " ".join(blob_parts).lower()
        if not blob:
            continue
        for key, aliases in aliases_by_key.items():
            if not key:
                continue
            if any(a and a in blob for a in aliases):
                out[key] = int(out.get(key) or 0) + 1
    return out


def _infer_psychotype(
    *,
    segment_display: str,
    goals: dict[str, Any],
    triggers_fired: list[str],
) -> tuple[str, list[str]]:
    pool = " ".join(
        [
            segment_display or "",
            " ".join(triggers_fired or []),
            " ".join(goals.get("mental_symptoms") or []),
            " ".join(goals.get("physical_symptoms") or []),
            str(goals.get("trigger_or_distortion") or ""),
        ]
    ).lower()
    if any(k in pool for k in ("паник", "тревог", "страх", "навязчив")):
        return (
            "Тревожный / гиперконтроль",
            ["высокая реактивность на триггеры", "фокус на стабилизации сна и тревоги"],
        )
    if any(k in pool for k in ("апат", "устал", "истощ", "нет сил", "энер")):
        return (
            "Астенический / истощение",
            ["энергетический дефицит по самонаблюдению", "нужен мягкий ритм восстановления"],
        )
    if any(k in pool for k in ("раздраж", "скачк", "эмоцион", "стресс")):
        return (
            "Эмоционально лабильный / стрессовый",
            ["перепады эмоционального тона", "акцент на управлении триггерами"],
        )
    if any(k in pool for k in ("концентрац", "туман", "памят", "фокус")):
        return (
            "Когнитивно перегруженный",
            ["снижение фокуса и устойчивости внимания", "акцент на когнитивной поддержке"],
        )
    return (
        "Комбинированный / стабилизация",
        ["смешанный профиль симптомов", "приоритет: мягкая системная стабилизация"],
    )


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
    memo_cards = _build_memo_cards(memo_rows)
    psychotype_label, psychotype_points = _infer_psychotype(
        segment_display=str(segment or ""),
        goals=goals,
        triggers_fired=(prof.get("triggers_fired") or []) if prof else [],
    )
    usage_counts = await _mushroom_usage_counts(uid, memo_cards)
    usage_max = max([int(v or 0) for v in usage_counts.values()] + [1])

    science_table: list[dict[str, Any]] = []
    training_source_cards: list[dict[str, Any]] = []
    for card in memo_cards:
        posts = await _fetch_training_posts_for_card(card, limit=5)
        merged = "\n".join((str(p.get("content") or "")[:1600] for p in posts))
        effect_line = _extract_line_by_keywords(
            merged,
            ("эффект", "помога", "сниж", "поддерж", "стресс", "сон", "концентрац", "энерг"),
            220,
        )
        bio_line = _extract_line_by_keywords(
            merged,
            ("биох", "актив", "соедин", "веществ", "бета", "тритерпен", "полисахар", "кордицеп", "мусцим", "ерин"),
            220,
        )
        pd_line = _extract_line_by_keywords(
            merged,
            ("фармакод", "механизм", "gaba", "ngf", "иммун", "митохонд", "рецептор"),
            220,
        )
        image_url = next((str(p.get("image_url") or "").strip() for p in posts if p.get("image_url")), "")
        key = str(card.get("key") or "")
        cnt = int(usage_counts.get(key) or 0)
        card["training_image_url"] = image_url or None
        card["effect_line"] = effect_line or _compact_text(str(card.get("core_short") or ""), 200)
        card["biochemistry_line"] = bio_line or "В обучающих постах нет явной формулировки по биохимии."
        card["pharmacodynamics_line"] = pd_line or "В обучающих постах нет явной формулировки по фармакодинамике."
        card["usage_count"] = cnt
        card["usage_pct"] = max(8, int(round((cnt / usage_max) * 100))) if usage_max > 0 else 8

        science_table.append(
            {
                "key": key,
                "name_ru": card.get("name_ru"),
                "latin": card.get("latin"),
                "image_url": card.get("training_image_url"),
                "function_line": card["effect_line"],
                "indications": ", ".join(card.get("who_for") or []) or "По текущему профилю",
                "biochemistry_line": card["biochemistry_line"],
                "pharmacodynamics_line": card["pharmacodynamics_line"],
                "intake_line": card.get("dose_short") or card.get("how_apply_short") or "По сопровождению",
                "usage_count": cnt,
                "usage_pct": card["usage_pct"],
            }
        )

        for p in posts[:2]:
            title = (p.get("title") or "").strip()
            if not title:
                continue
            training_source_cards.append(
                {
                    "mushroom": card.get("name_ru") or "",
                    "title": title[:140],
                    "folder": (p.get("folder") or p.get("category") or "").strip(),
                    "excerpt": _compact_text(str(p.get("content") or ""), 240),
                    "image_url": (p.get("image_url") or "").strip() or None,
                }
            )

    return {
        "profile_source": profile_source,
        "profile_updated_at": prof.get("updated_at") if prof else None,
        "profile_updated_at_utc": _format_utc_stamp(prof.get("updated_at") if prof else None),
        "orientation_disclaimer": "Информационно. Не медицинское назначение. Решения — с врачом и специалистом по фунготерапии.",
        "cluster_label": prof.get("cluster_label") if prof else None,
        "triggers_fired": (prof.get("triggers_fired") or []) if prof else [],
        "normalized_metrics_lines": norm_lines,
        "segment_display": segment,
        "psychotype_label": psychotype_label,
        "psychotype_points": psychotype_points,
        "bundles": (prof.get("bundles") or []) if prof else [],
        "memo_rows": memo_rows,
        "memo_cards": memo_cards,
        "science_table": science_table,
        "training_source_cards": training_source_cards[:18],
        "survey_mushroom_usage_max": usage_max,
        "goals": goals,
        "latest_ai_recommendation": rec,
        "has_plan_content": bool(memo_rows),
        "wellness_automation": automation,
    }
