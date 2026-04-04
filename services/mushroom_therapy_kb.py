"""
Образовательная база «гриб → функция → показания» + эвристики связок.
Не медицинское назначение: только ориентиры для самонаблюдения и контекста AI.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

KB_VERSION = 1

MUSHROOMS: dict[str, dict[str, Any]] = {
    "amanita_muscaria": {
        "name_ru": "Мухомор красный",
        "latin": "Amanita muscaria",
        "core": "GABA-модуляция, снижение гиперактивности ЦНС",
        "indications": [
            "тревожность",
            "перегруз ЦНС",
            "раздражительность",
            "сон",
            "навязчивые мысли",
        ],
        "contra": "Не назначение; только с квалифицированным сопровождением и правовым статусом в регионе.",
        "dose_hint": "Дозы и режим — только в рамках вашего сопровождения, не как указание из приложения.",
    },
    "amanita_pantherina": {
        "name_ru": "Мухомор пантерный",
        "latin": "Amanita pantherina",
        "core": "более выраженная GABA-модуляция (образовательно: «глубже разгрузка»)",
        "indications": ["сильная тревога", "панические состояния", "истощение нервной системы", "травматические зажимы"],
        "contra": "Высокий риск при самолечении; только образовательный контекст.",
        "dose_hint": "Обсуждается индивидуально со специалистом по фунготерапии.",
    },
    "amanita_regalis": {
        "name_ru": "Мухомор королевский",
        "latin": "Amanita regalis",
        "core": "баланс между красным и пантерным (образовательно)",
        "indications": ["нестабильное состояние", "скачки настроения", "комбинированные симптомы"],
        "contra": "Не назначение из приложения.",
        "dose_hint": "Индивидуально со сопровождением.",
    },
    "hericium": {
        "name_ru": "Ежовик гребенчатый",
        "latin": "Hericium erinaceus",
        "core": "поддержка нейромедиаторов / NGF (образовательно)",
        "indications": ["память", "концентрация", "«туман в голове»", "после стресса"],
        "contra": "Не замена лечению заболеваний ЦНС.",
        "dose_hint": "Типично курсами; уточнять у сопровождения.",
    },
    "cordyceps": {
        "name_ru": "Кордицепс",
        "latin": "Cordyceps",
        "core": "энергия, выносливость, митохондрии (образовательно)",
        "indications": ["усталость", "низкая энергия", "слабая выносливость"],
        "contra": "Противопоказания по соматике — у врача.",
        "dose_hint": "Подбирается индивидуально.",
    },
    "reishi": {
        "name_ru": "Рейши (трутовик лакированный)",
        "latin": "Ganoderma lucidum",
        "core": "адаптоген, иммуномодуляция (образовательно)",
        "indications": ["хронический стресс", "сон", "иммунитет", "воспаления (общий фон)"],
        "contra": "Не монотерапия при тяжёлых состояниях.",
        "dose_hint": "Длительность курса — со специалистом.",
    },
    "trametes": {
        "name_ru": "Траметес разноцветный",
        "latin": "Trametes versicolor",
        "core": "иммунная регуляция PSK/PSP (образовательно)",
        "indications": ["иммунитет", "после болезней", "онко-поддержка как вспомогательное"],
        "contra": "Не вместо онкологического протокола.",
        "dose_hint": "По схеме сопровождения.",
    },
    "maitake": {
        "name_ru": "Майтаке",
        "latin": "Grifola frondosa",
        "core": "метаболизм, инсулиночувствительность (образовательно)",
        "indications": ["инсулинорезистентность", "вес", "метаболический синдром"],
        "contra": "Сахарный диабет — врач.",
        "dose_hint": "Индивидуально.",
    },
    "shiitake": {
        "name_ru": "Шиитаке",
        "latin": "Lentinula edodes",
        "core": "иммунитет, липиды (образовательно)",
        "indications": ["холестерин", "иммунитет", "воспалительный фон"],
        "contra": "Аллергии — осторожно.",
        "dose_hint": "Индивидуально.",
    },
}

BUNDLES: dict[str, dict[str, Any]] = {
    "anti_stress": {
        "title": "Ориентир: анти-стресс",
        "keys": ["amanita_muscaria", "reishi"],
        "rationale": "При выраженной тревоге и стрессе в базе часто связывают мягкую GABA-линию и адаптоген.",
    },
    "energy_brain": {
        "title": "Ориентир: энергия + мозг",
        "keys": ["cordyceps", "hericium"],
        "rationale": "Низкая энергия и когнитивный спад — связка «митохондрии + нейроподдержка» (образовательно).",
    },
    "cns_recovery": {
        "title": "Ориентир: восстановление ЦНС",
        "keys": ["amanita_muscaria", "hericium", "reishi"],
        "rationale": "После перегруза и для когниции — многоуровневая связка в обучающих материалах.",
    },
    "immunity_stack": {
        "title": "Ориентир: иммунитет",
        "keys": ["trametes", "shiitake", "reishi"],
        "rationale": "Стресс + слабый иммунитет — комбинации из адаптогена и полипоровых.",
    },
    "metabolic_stack": {
        "title": "Ориентир: метаболизм",
        "keys": ["maitake", "shiitake"],
        "rationale": "Акцент на метаболизм и липиды в образовательной базе.",
    },
}


def list_bundle_ids() -> list[str]:
    return list(BUNDLES.keys())

# Памятка: как применять и курс — только образовательные ориентиры, не рецепт.
MUSHROOM_PLAN_MEMO: dict[str, dict[str, str]] = {
    "amanita_muscaria": {
        "dose_addendum": "Конкретные мг/капли и шаг титрации задаёт только специалист по фунготерапии.",
        "how_apply": "Курс строят поэтапно (подстройка → поддержание). Вести дневник сна, тревоги и тела. Не сочетать с алкоголем и седативными без врача.",
        "course_weeks": "Ориентир первого цикла 4–10 недель с пересмотром у сопровождающего.",
    },
    "amanita_pantherina": {
        "dose_addendum": "Только под наблюдением; доза подбирается индивидуально, не по шаблону из приложения.",
        "how_apply": "Обсуждать частоту и окна приёма со специалистом; при ухудшении — немедленно к врачу.",
        "course_weeks": "Короткие контролируемые фазы с оценкой переносимости.",
    },
    "amanita_regalis": {
        "dose_addendum": "Индивидуально со специалистом; не копировать чужие схемы.",
        "how_apply": "Отслеживать сон и эмоциональные качели; дневник обязателен.",
        "course_weeks": "Ориентир 4–8 недель с коррекцией.",
    },
    "hericium": {
        "dose_addendum": "По маркировке БАД/порошка и по согласованию с врачом (типично обсуждают 1–3 г экстракта в сутки или капсулы по схеме производителя).",
        "how_apply": "Принимать согласованно с приёмом пищи или натощак — как решите со специалистом; курс для когниции обычно не менее 4–8 недель.",
        "course_weeks": "6–12 недель с оценкой концентрации и сна.",
    },
    "cordyceps": {
        "dose_addendum": "Доза зависит от экстракта; уточнять у специалиста и по инструкции к продукту.",
        "how_apply": "Часто утром или до нагрузки (если нет противопоказаний по давлению/щитовидке — у врача).",
        "course_weeks": "4–8 недель для оценки энергии и выносливости.",
    },
    "reishi": {
        "dose_addendum": "Порошок/настой/капсулы — по маркировке и согласованию; не резко повышать дозу самостоятельно.",
        "how_apply": "Часто вечером для расслабления или в 2 приёма; не с алкоголем без согласования.",
        "course_weeks": "8–12 недель для фона стресса и сна.",
    },
    "trametes": {
        "dose_addendum": "PSK/PSP-продукты — строго по схеме сопровождения и маркировке.",
        "how_apply": "Длительные курсы; согласовать с онкологом/терапевтом при любой соматике.",
        "course_weeks": "От 8 недель и более — по протоколу специалиста.",
    },
    "maitake": {
        "dose_addendum": "При диабете и метформинах — только по согласованию с эндокринологом.",
        "how_apply": "Отслеживать глюкозу и самочувствие; принимать стабильно в одно время суток.",
        "course_weeks": "8–12 недель для оценки метаболических маркеров.",
    },
    "shiitake": {
        "dose_addendum": "Пищевой порошок или экстракт — по маркировке; при аллергии на грибы не использовать.",
        "how_apply": "В составе курса иммунитета и липидов вместе со сном и питанием.",
        "course_weeks": "6–10 недель.",
    },
}


def _f10(m: dict[str, Any], key: str) -> Optional[float]:
    v = m.get(key)
    if v is None:
        return None
    try:
        x = float(v)
        if 0 <= x <= 10:
            return x
    except (TypeError, ValueError):
        pass
    return None


def normalize_metrics_from_m(m: dict[str, Any]) -> dict[str, Any]:
    """Единая шкала 0–10 + флаги для правил."""
    anxiety = _f10(m, "anxiety_0_10")
    energy = _f10(m, "energy_0_10")
    sleep = _f10(m, "sleep_quality_0_10")
    focus = _f10(m, "concentration_0_10")
    stress_direct = _f10(m, "stress_0_10")
    irrit = _f10(m, "irritability_0_10")
    stress = stress_direct
    if stress is None:
        parts = [x for x in (anxiety, irrit) if x is not None]
        stress = max(parts) if parts else None
    immunity = _f10(m, "immunity_perceived_0_10")
    fatigue = _f10(m, "fatigue_0_10")
    metabolic = bool(m.get("metabolic_weight_focus")) or bool(m.get("metabolic_focus"))
    panic = m.get("panic_today") is True
    return {
        "anxiety": anxiety,
        "energy": energy,
        "sleep": sleep,
        "focus": focus,
        "stress": stress,
        "immunity": immunity,
        "fatigue": fatigue,
        "metabolic": metabolic,
        "panic": panic,
    }


def infer_heuristic_cluster(norm: dict[str, Any]) -> str:
    a, e, f, focus = norm.get("anxiety"), norm.get("energy"), norm.get("fatigue"), norm.get("focus")
    if norm.get("panic") or (a is not None and a >= 7):
        return "тревожный тип / перегруз ЦНС"
    if e is not None and e <= 4 and f is not None and f >= 6:
        return "истощённый тип"
    if focus is not None and focus <= 5:
        return "акцент на когницию / концентрацию"
    if norm.get("metabolic"):
        return "метаболический акцент"
    st, im = norm.get("stress"), norm.get("immunity")
    if st is not None and st >= 7 and im is not None and im <= 4:
        return "стресс + слабый «иммунитет по ощущениям»"
    return "комбинированный / стабилизация"


def _mushroom_line(key: str) -> str:
    spec = MUSHROOMS.get(key) or {}
    return f"{spec.get('name_ru', key)} — {spec.get('core', '')}"


def suggest_therapy_payload(norm: dict[str, Any]) -> dict[str, Any]:
    """Эвристики if/else + связки. Результат — только образовательный."""
    triggers: list[str] = []
    bundle_ids: list[str] = []
    singles: list[dict[str, str]] = []

    a = norm.get("anxiety")
    e = norm.get("energy")
    focus = norm.get("focus")
    st = norm.get("stress")
    im = norm.get("immunity")
    panic = norm.get("panic")
    metabolic = norm.get("metabolic")

    if panic or (a is not None and a > 7):
        triggers.append("тревога > 7 или паника")
        bundle_ids.append("anti_stress")
        if a is not None and a >= 8:
            singles.append(
                {
                    "key": "amanita_pantherina",
                    "why": "В базе пантерный упоминают при очень высокой тревоге, если «красный» недостаточен — только со специалистом.",
                }
            )
        else:
            singles.append({"key": "amanita_muscaria", "why": "Базовая линия при сильной тревоге (образовательно)."})
    elif a is not None and a >= 7:
        triggers.append("тревога ≥ 7")
        bundle_ids.append("anti_stress")
        singles.append({"key": "amanita_muscaria", "why": "Ориентир при выраженной тревоге."})

    if e is not None and e < 4:
        triggers.append("энергия < 4")
        bundle_ids.append("energy_brain")
        singles.append({"key": "cordyceps", "why": "Ориентир при низкой энергии."})

    if focus is not None and focus < 5:
        triggers.append("концентрация < 5")
        if "energy_brain" not in bundle_ids:
            bundle_ids.append("energy_brain")
        singles.append({"key": "hericium", "why": "Ориентир при снижении концентрации / тумане."})

    if st is not None and st >= 6 and im is not None and im <= 5:
        triggers.append("стресс + слабый иммунитет (по шкалам)")
        bundle_ids.append("immunity_stack")

    if metabolic:
        triggers.append("метаболика / вес (флаг в дневнике)")
        bundle_ids.append("metabolic_stack")

    # Восстановление ЦНС: перегруз + когниция
    if (a is not None and a >= 6) and (focus is not None and focus <= 6):
        if "cns_recovery" not in bundle_ids and len(bundle_ids) < 4:
            bundle_ids.append("cns_recovery")

    seen: set[str] = set()
    uniq_bundles: list[str] = []
    for b in bundle_ids:
        if b not in seen:
            seen.add(b)
            uniq_bundles.append(b)

    bundles_out: list[dict[str, Any]] = []
    for bid in uniq_bundles:
        spec = BUNDLES.get(bid)
        if not spec:
            continue
        names = [MUSHROOMS[k]["name_ru"] for k in spec["keys"] if k in MUSHROOMS]
        bundles_out.append(
            {
                "id": bid,
                "title": spec["title"],
                "mushroom_names": names,
                "rationale": spec["rationale"],
            }
        )

    singles_seen: set[str] = set()
    singles_out: list[dict[str, str]] = []
    for s in singles:
        k = s["key"]
        if k in singles_seen:
            continue
        singles_seen.add(k)
        spec = MUSHROOMS.get(k, {})
        singles_out.append(
            {
                "key": k,
                "name_ru": spec.get("name_ru", k),
                "why": s["why"],
                "dose_hint": spec.get("dose_hint", ""),
            }
        )

    cluster = infer_heuristic_cluster(norm)
    return {
        "cluster_label": cluster,
        "triggers_fired": triggers,
        "bundles": bundles_out,
        "single_hints": singles_out,
    }


def build_stored_profile_json(merged_metrics: dict[str, Any]) -> dict[str, Any]:
    norm = normalize_metrics_from_m(merged_metrics)
    payload = suggest_therapy_payload(norm)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    compact_lines = [
        f"Кластер (эвристика): {payload['cluster_label']}.",
        f"Триггеры: {', '.join(payload['triggers_fired']) or 'нет явных порогов'}.",
    ]
    for b in payload["bundles"][:5]:
        compact_lines.append(f"Связка «{b['title']}»: {', '.join(b['mushroom_names'])}.")
    for s in payload["single_hints"][:6]:
        compact_lines.append(f"Ориентир: {s['name_ru']} — {s['why'][:120]}")
    ai_compact = " ".join(compact_lines)[:3500]
    return {
        "kb_version": KB_VERSION,
        "updated_at": now,
        "cluster_label": payload["cluster_label"],
        "normalized_metrics": {k: v for k, v in norm.items() if v is not None},
        "triggers_fired": payload["triggers_fired"],
        "bundles": payload["bundles"],
        "single_hints": payload["single_hints"],
        "ai_context_compact": ai_compact,
    }


def build_memo_row_for_key(key: str) -> dict[str, Any] | None:
    """Одна строка памятки по ключу из KB (без персональных ролей из профиля)."""
    if key not in MUSHROOMS:
        return None
    spec = MUSHROOMS[key]
    memo = MUSHROOM_PLAN_MEMO.get(key, {})
    dose_line = (spec.get("dose_hint") or "").strip()
    if memo.get("dose_addendum"):
        dose_line = (dose_line + " " + memo["dose_addendum"]).strip()
    return {
        "key": key,
        "name_ru": spec["name_ru"],
        "latin": spec["latin"],
        "core": spec["core"],
        "indications": ", ".join(spec.get("indications") or []),
        "role_for_you": "",
        "dose_orientation": dose_line,
        "how_apply": memo.get("how_apply", ""),
        "course_weeks": memo.get("course_weeks", ""),
        "contra": spec.get("contra", ""),
    }


def build_merged_memo_rows(stored: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Все грибы из справочника + персональные роли из профиля. Возвращает (полный список, только строки профиля)."""
    prof_rows = build_memo_rows_from_profile(stored) if stored else []
    prof_by_key = {str(r["key"]): r for r in prof_rows}
    merged: list[dict[str, Any]] = []
    for key in MUSHROOMS.keys():
        base = build_memo_row_for_key(key)
        if not base:
            continue
        pr = prof_by_key.get(key)
        if pr:
            base["role_for_you"] = pr.get("role_for_you") or ""
        merged.append(base)
    return merged, prof_rows


def build_memo_rows_from_profile(stored: dict[str, Any]) -> list[dict[str, Any]]:
    """Строки для таблицы «личная памятка» по сохранённому или эфемерному профилю."""
    roles: dict[str, list[str]] = defaultdict(list)
    order: list[str] = []
    seen: set[str] = set()
    for b in stored.get("bundles") or []:
        bid = b.get("id")
        sp = BUNDLES.get(bid) or {}
        title = (b.get("title") or "").strip()
        for k in sp.get("keys") or []:
            if k not in MUSHROOMS:
                continue
            roles[k].append(f"связка «{title}»")
            if k not in seen:
                seen.add(k)
                order.append(k)
    for s in stored.get("single_hints") or []:
        k = s.get("key")
        if not k or k not in MUSHROOMS:
            continue
        why = (s.get("why") or "").strip()
        if why:
            roles[k].append(why)
        if k not in seen:
            seen.add(k)
            order.append(k)
    rows: list[dict[str, Any]] = []
    for k in order:
        spec = MUSHROOMS[k]
        memo = MUSHROOM_PLAN_MEMO.get(k, {})
        dose_line = (spec.get("dose_hint") or "").strip()
        if memo.get("dose_addendum"):
            dose_line = (dose_line + " " + memo["dose_addendum"]).strip()
        rows.append(
            {
                "key": k,
                "name_ru": spec["name_ru"],
                "latin": spec["latin"],
                "core": spec["core"],
                "indications": ", ".join(spec.get("indications") or []),
                "role_for_you": " · ".join(roles.get(k, [])),
                "dose_orientation": dose_line,
                "how_apply": memo.get("how_apply", ""),
                "course_weeks": memo.get("course_weeks", ""),
                "contra": spec.get("contra", ""),
            }
        )
    return rows


def format_normalized_metrics_ru(norm: dict[str, Any]) -> list[str]:
    """Короткие строки для блока «ваши шкалы»."""
    if not norm:
        return []
    labels = {
        "anxiety": "тревога",
        "energy": "энергия",
        "sleep": "сон",
        "focus": "концентрация",
        "stress": "стресс (оценка)",
        "immunity": "иммунитет по ощущениям",
        "fatigue": "усталость",
        "metabolic": "метаболика/вес (флаг)",
        "panic": "паника сегодня",
    }
    lines: list[str] = []
    for k, lab in labels.items():
        v = norm.get(k)
        if v is None:
            continue
        if k == "metabolic":
            lines.append(f"{lab}: да" if v else f"{lab}: нет")
        elif k == "panic":
            lines.append(f"{lab}: да" if v else f"{lab}: нет")
        else:
            lines.append(f"{lab}: {v}/10")
    return lines


def format_therapy_context_for_coach(stored: Optional[dict[str, Any]]) -> str:
    if not stored:
        return ""
    block = stored.get("ai_context_compact") or ""
    seg = stored.get("wellness_segment_snapshot")
    if seg:
        block = f"Сегмент (из снимка): {seg}\n" + block
    return (
        "\n\n=== ВНУТРЕННИЙ ОБРАЗОВАТЕЛЬНЫЙ КОНТЕКСТ (не назначение) ===\n"
        "База грибов и эвристики связок — только для мягких формулировок и вопросов пользователю. "
        "Не выдавай дозы как приказ; отсылай к сопровождению и врачу.\n"
        f"{block[:4000]}\n=== КОНЕЦ КОНТЕКСТА ===\n"
    )


def therapy_panel_from_stored(stored: dict[str, Any]) -> dict[str, Any]:
    """Для шаблона личного кабинета."""
    bundles = stored.get("bundles") or []
    singles = stored.get("single_hints") or []
    triggers = stored.get("triggers_fired") or []
    metrics = stored.get("normalized_metrics") or {}
    if not bundles and not singles and not triggers and not metrics:
        return {"show": False}
    mush_ref = [
        {"key": k, "name": v["name_ru"], "core": v["core"], "indications": ", ".join(v["indications"][:5])}
        for k, v in MUSHROOMS.items()
    ]
    return {
        "show": True,
        "cluster": stored.get("cluster_label") or "—",
        "updated_at": stored.get("updated_at") or "",
        "triggers": stored.get("triggers_fired") or [],
        "bundles": bundles,
        "singles": singles,
        "mushroom_catalog": mush_ref,
        "disclaimer": "Информационно. Не медицинское назначение. Решения — с врачом и специалистом по фунготерапии.",
    }
