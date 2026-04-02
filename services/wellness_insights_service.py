"""Снимки метрик по дням, сегменты, графики, рекомендации AI, агрегаты для админки (не медицина)."""
from __future__ import annotations

import json
import logging
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from config import settings
from db.database import database
from db.models import (
    users,
    wellness_ai_recommendations,
    wellness_daily_snapshots,
    wellness_journal_entries,
    wellness_scheme_effect_stats,
)

logger = logging.getLogger(__name__)


def _utc_today() -> date:
    return datetime.utcnow().date()


def calendar_week_strip_for_user(
    series: list[dict[str, Any]], *, today: Optional[date] = None
) -> list[dict[str, Any]]:
    """Пн–Вс календарной недели (UTC): метки дня, есть ли снимок."""
    d0 = today or _utc_today()
    monday = d0 - timedelta(days=d0.weekday())
    have = {str(x.get("date") or "") for x in series}
    labels = ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")
    out: list[dict[str, Any]] = []
    for i in range(7):
        dt = monday + timedelta(days=i)
        iso = dt.isoformat()
        out.append(
            {
                "label": labels[i],
                "num": dt.day,
                "iso": iso,
                "is_today": dt == d0,
                "has_data": iso in have,
            }
        )
    return out


def _metric_float_for_index(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def annotate_week_strip_best_worst(
    strip: list[dict[str, Any]], series: list[dict[str, Any]]
) -> bool:
    """Помечает лучший/худший день недели по сводному индексу (как кольцо на дашборде). Возвращает, показывать ли легенду."""
    by_date: dict[str, dict[str, Any]] = {}
    for x in series:
        iso = str(x.get("date") or "")
        if len(iso) >= 10:
            by_date[iso] = x.get("m") or {}
    scored: list[tuple[str, int]] = []
    for cell in strip:
        cell["is_week_best"] = False
        cell["is_week_worst"] = False
        iso = str(cell.get("iso") or "")
        if not cell.get("has_data") or not iso:
            continue
        m = by_date.get(iso, {})
        pct = wellness_composite_index_percent(
            _metric_float_for_index(m.get("mood_0_10")),
            _metric_float_for_index(m.get("energy_0_10")),
            _metric_float_for_index(m.get("anxiety_0_10")),
        )
        if pct is not None:
            scored.append((iso, int(pct)))
    if len(scored) < 2:
        return False
    mx = max(s[1] for s in scored)
    mn = min(s[1] for s in scored)
    if mx == mn:
        return False
    best_isos = {iso for iso, sc in scored if sc == mx}
    worst_isos = {iso for iso, sc in scored if sc == mn}
    for cell in strip:
        iso = str(cell.get("iso") or "")
        cell["is_week_best"] = iso in best_isos
        cell["is_week_worst"] = iso in worst_isos
    return True


def parse_wellness_chart_range(raw: Optional[str]) -> tuple[str, int]:
    """Ключ периода и число календарных дней в окне (включая сегодня)."""
    s = (raw or "").strip().lower()
    if s in ("d", "day", "1"):
        return ("day", 2)
    if s in ("m", "month", "30"):
        return ("month", 30)
    if s in ("w", "week", "7"):
        return ("week", 7)
    return ("week", 7)


def slice_series_calendar_days(series: list[dict[str, Any]], days_inclusive: int) -> list[dict[str, Any]]:
    """Точки с датой >= (сегодня UTC − days_inclusive + 1). При days_inclusive=1 — только сегодня."""
    if days_inclusive < 1 or not series:
        return []
    end = _utc_today()
    start = end - timedelta(days=days_inclusive - 1)
    start_iso = start.isoformat()
    return [x for x in series if str(x.get("date") or "") >= start_iso]


def short_chart_date_labels(labels: list[str]) -> list[str]:
    out: list[str] = []
    for lb in labels:
        if isinstance(lb, str) and len(lb) >= 10:
            out.append(lb[8:10] + "." + lb[5:7])
        else:
            out.append(str(lb))
    return out


def sparkline_polyline_points(
    values: list[Optional[float]],
    width: float = 108.0,
    height: float = 34.0,
    pad: float = 3.0,
) -> Optional[str]:
    """Координаты для SVG polyline points= (несколько точек по последним слотам)."""
    if not values:
        return None
    pts_idx: list[tuple[int, float]] = []
    for i, v in enumerate(values):
        if v is None or str(v).strip() == "":
            continue
        try:
            pts_idx.append((i, float(v)))
        except (TypeError, ValueError):
            continue
    if len(pts_idx) == 0:
        return None
    if len(pts_idx) == 1:
        i, val = pts_idx[0]
        x = pad + (i / max(len(values) - 1, 1)) * (width - 2 * pad)
        vmin = val - 0.5
        vmax = val + 0.5
        t = 0.5
        y = pad + (1 - t) * (height - 2 * pad)
        x2 = min(x + 8, width - pad)
        return f"{x:.2f},{y:.2f} {x2:.2f},{y:.2f}"
    nv = [p[1] for p in pts_idx]
    vmin, vmax = min(nv), max(nv)
    if abs(vmax - vmin) < 1e-6:
        vmin -= 0.5
        vmax += 0.5
    out_coords: list[str] = []
    for i, val in pts_idx:
        x = pad + (i / max(len(values) - 1, 1)) * (width - 2 * pad)
        t = (val - vmin) / (vmax - vmin)
        y = pad + (1 - t) * (height - 2 * pad)
        out_coords.append(f"{x:.2f},{y:.2f}")
    return " ".join(out_coords)


def wellness_composite_index_percent(
    mood: Optional[float],
    energy: Optional[float],
    anxiety: Optional[float],
) -> Optional[int]:
    """0–100: среднее нормализованных настроение, энергия и (10 − тревога). Не медицинский скоринг."""
    acc = 0.0
    n = 0
    if mood is not None:
        acc += max(0.0, min(10.0, float(mood))) / 10.0
        n += 1
    if energy is not None:
        acc += max(0.0, min(10.0, float(energy))) / 10.0
        n += 1
    if anxiety is not None:
        acc += max(0.0, min(10.0, 10.0 - float(anxiety))) / 10.0
        n += 1
    if n == 0:
        return None
    return int(round(acc / n * 100.0))


def mood_stability_pstdev_last(series: list[dict[str, Any]], max_days: int = 7) -> Optional[float]:
    """σ настроения по последним дням окна (не больше max_days точек)."""
    tail = series[-max_days:] if len(series) > max_days else series
    moods: list[float] = []
    for x in tail:
        v = (x.get("m") or {}).get("mood_0_10")
        if v is None:
            continue
        try:
            moods.append(float(v))
        except (TypeError, ValueError):
            pass
    if len(moods) < 2:
        return None
    try:
        return round(statistics.pstdev(moods), 2)
    except statistics.StatisticsError:
        return None


async def admin_snapshot_counts_rolling_days(days: int = 7) -> list[dict[str, Any]]:
    """Последние `days` дат UTC: сколько снимков (строк) в день по всей платформе."""
    end = _utc_today()
    since = end - timedelta(days=max(1, days) - 1)
    rows = await database.fetch_all(
        sa.select(
            wellness_daily_snapshots.c.snapshot_date,
            sa.func.count().label("cnt"),
        )
        .where(wellness_daily_snapshots.c.snapshot_date >= since)
        .group_by(wellness_daily_snapshots.c.snapshot_date)
        .order_by(wellness_daily_snapshots.c.snapshot_date.asc())
    )
    counts: dict[str, int] = {}
    for r in rows:
        sd = r["snapshot_date"]
        iso = sd.isoformat() if hasattr(sd, "isoformat") else str(sd)
        counts[iso] = int(r["cnt"] or 0)
    labels = ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")
    out: list[dict[str, Any]] = []
    d = since
    while d <= end:
        iso = d.isoformat()
        out.append(
            {
                "label": labels[d.weekday()],
                "num": d.day,
                "iso": iso,
                "is_today": d == end,
                "n": counts.get(iso, 0),
            }
        )
        d += timedelta(days=1)
    return out


def _parse_entry_date(row: dict) -> date:
    ca = row.get("created_at")
    if isinstance(ca, datetime):
        return ca.date()
    return _utc_today()


def _clean_patch(data: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, list) and len(v) == 0:
            continue
        out[k] = v
    return out


async def compute_segment_for_user(user_id: int) -> str:
    since = _utc_today() - timedelta(days=7)
    rows = await database.fetch_all(
        wellness_daily_snapshots.select()
        .where(wellness_daily_snapshots.c.user_id == int(user_id))
        .where(wellness_daily_snapshots.c.snapshot_date >= since)
        .order_by(wellness_daily_snapshots.c.snapshot_date.desc())
        .limit(14)
    )
    if len(rows) < 2:
        return "мало данных"
    anx, mood, en = [], [], []
    for r in rows:
        try:
            m = json.loads(r["metrics_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for arr, key in ((anx, "anxiety_0_10"), (mood, "mood_0_10"), (en, "energy_0_10")):
            v = m.get(key)
            if v is None:
                continue
            try:
                arr.append(float(v))
            except (TypeError, ValueError):
                pass
    parts: list[str] = []
    if anx and statistics.mean(anx) >= 6.5:
        parts.append("высокая тревожность")
    if en and statistics.mean(en) <= 4.5:
        parts.append("низкая энергия")
    if mood and len(mood) >= 3:
        try:
            if statistics.pstdev(mood) >= 2.5:
                parts.append("резкие колебания настроения")
        except statistics.StatisticsError:
            pass
    if not parts:
        parts.append("стабильный фон")
    return ", ".join(parts[:3])


async def upsert_daily_snapshot_from_extracted_entry(entry_id: int, extracted_json_str: str) -> None:
    ent = await database.fetch_one(
        wellness_journal_entries.select().where(wellness_journal_entries.c.id == int(entry_id))
    )
    if not ent or (ent.get("role") or "") != "user_reply":
        return
    if ent.get("statistics_excluded"):
        return
    try:
        data = json.loads(extracted_json_str)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    uid = int(ent["user_id"])
    snap_d = _parse_entry_date(dict(ent))
    patch = _clean_patch(data)

    ex = await database.fetch_one(
        wellness_daily_snapshots.select()
        .where(wellness_daily_snapshots.c.user_id == uid)
        .where(wellness_daily_snapshots.c.snapshot_date == snap_d)
    )
    merged: dict[str, Any] = {}
    if ex and ex.get("metrics_json"):
        try:
            merged = json.loads(ex["metrics_json"])
        except json.JSONDecodeError:
            merged = {}
    merged.update(patch)
    seg = await compute_segment_for_user(uid)
    payload = json.dumps(merged, ensure_ascii=False)
    now = datetime.utcnow()

    if ex:
        await database.execute(
            wellness_daily_snapshots.update()
            .where(wellness_daily_snapshots.c.id == ex["id"])
            .values(
                metrics_json=payload,
                source_wellness_entry_id=int(entry_id),
                wellness_segment=seg,
                updated_at=now,
            )
        )
    else:
        await database.execute(
            wellness_daily_snapshots.insert().values(
                user_id=uid,
                snapshot_date=snap_d,
                metrics_json=payload,
                source_wellness_entry_id=int(entry_id),
                wellness_segment=seg,
                created_at=now,
                updated_at=now,
            )
        )


async def count_checkin_streak(user_id: int) -> int:
    rows = await database.fetch_all(
        sa.select(wellness_daily_snapshots.c.snapshot_date)
        .where(wellness_daily_snapshots.c.user_id == int(user_id))
        .order_by(wellness_daily_snapshots.c.snapshot_date.desc())
        .limit(400)
    )
    if not rows:
        return 0
    have = {r["snapshot_date"] for r in rows}
    d = _utc_today()
    streak = 0
    for _ in range(400):
        if d not in have:
            break
        streak += 1
        d = d - timedelta(days=1)
    return streak


async def fetch_snapshots_series(user_id: int, days: int = 14) -> list[dict[str, Any]]:
    since = _utc_today() - timedelta(days=days)
    rows = await database.fetch_all(
        wellness_daily_snapshots.select()
        .where(wellness_daily_snapshots.c.user_id == int(user_id))
        .where(wellness_daily_snapshots.c.snapshot_date >= since)
        .order_by(wellness_daily_snapshots.c.snapshot_date.asc())
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            m = json.loads(r["metrics_json"] or "{}")
        except json.JSONDecodeError:
            m = {}
        sd = r["snapshot_date"]
        out.append(
            {
                "date": sd.isoformat() if hasattr(sd, "isoformat") else str(sd),
                "m": m,
                "segment": r.get("wellness_segment"),
            }
        )
    return out


def quick_mood_progress_percent(series: list[dict[str, Any]]) -> Optional[float]:
    if len(series) < 2:
        return None
    try:
        m0 = series[0]["m"].get("mood_0_10")
        m1 = series[-1]["m"].get("mood_0_10")
        if m0 is None or m1 is None:
            return None
        return round((float(m1) - float(m0)) / 10.0 * 100.0, 1)
    except (TypeError, ValueError):
        return None


def latest_metric_value(series: list[dict[str, Any]], key: str) -> Optional[float]:
    for x in reversed(series):
        v = x["m"].get(key)
        if v is None or str(v).strip() == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def chartjs_line_config_dict(
    labels: list[str],
    data: list[Optional[float]],
    *,
    dataset_label: str,
    border_color: str = "#3dd4e0",
    y_max: float = 10.0,
) -> dict[str, Any]:
    return {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": dataset_label,
                    "data": data,
                    "borderColor": border_color,
                    "backgroundColor": border_color + "33",
                    "spanGaps": True,
                    "tension": 0.25,
                    "fill": False,
                }
            ],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"labels": {"color": "#aaa"}}},
            "scales": {
                "x": {"ticks": {"color": "#888"}, "grid": {"color": "rgba(255,255,255,0.06)"}},
                "y": {
                    "min": 0,
                    "max": y_max,
                    "ticks": {"color": "#888"},
                    "grid": {"color": "rgba(255,255,255,0.06)"},
                },
            },
        },
    }


def chartjs_line_spec(
    labels: list[str],
    data: list[Optional[float]],
    *,
    dataset_label: str,
    border_color: str = "#3dd4e0",
    y_max: float = 10.0,
) -> str:
    return json.dumps(
        chartjs_line_config_dict(
            labels, data, dataset_label=dataset_label, border_color=border_color, y_max=y_max
        ),
        ensure_ascii=False,
    )


def dosage_mood_scatter_chart_config(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Точки (число из dosage_amount_text, настроение 0–10), минимум 2 точки."""
    pts: list[dict[str, float]] = []
    for x in series:
        m = x.get("m") or {}
        mood = m.get("mood_0_10")
        dose_txt = m.get("dosage_amount_text")
        if mood is None or dose_txt is None:
            continue
        mo = re.search(r"(\d+(?:[.,]\d+)?)", str(dose_txt))
        if not mo:
            continue
        try:
            dx = float(mo.group(1).replace(",", "."))
            pts.append({"x": dx, "y": float(mood)})
        except (TypeError, ValueError):
            continue
    if len(pts) < 2:
        return None
    return {
        "type": "scatter",
        "data": {
            "datasets": [
                {
                    "label": "Настроение vs число из текста дозы",
                    "data": pts,
                    "backgroundColor": "rgba(61, 212, 224, 0.45)",
                    "borderColor": "#3dd4e0",
                    "borderWidth": 1,
                }
            ]
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"labels": {"color": "#aaa"}}},
            "scales": {
                "x": {
                    "title": {"display": True, "text": "Доза (из текста)", "color": "#888"},
                    "ticks": {"color": "#888"},
                    "grid": {"color": "rgba(255,255,255,0.06)"},
                },
                "y": {
                    "min": 0,
                    "max": 10,
                    "title": {"display": True, "text": "Настроение", "color": "#888"},
                    "ticks": {"color": "#888"},
                    "grid": {"color": "rgba(255,255,255,0.06)"},
                },
            },
        },
    }


def series_metric_arrays(series: list[dict[str, Any]], key: str) -> tuple[list[str], list[Optional[float]]]:
    labels = [x["date"] for x in series]
    data: list[Optional[float]] = []
    for x in series:
        v = x["m"].get(key)
        try:
            data.append(float(v) if v is not None and str(v).strip() != "" else None)
        except (TypeError, ValueError):
            data.append(None)
    return labels, data


async def latest_recommendation_text(user_id: int) -> Optional[str]:
    row = await database.fetch_one(
        sa.select(wellness_ai_recommendations.c.body_text)
        .where(wellness_ai_recommendations.c.user_id == int(user_id))
        .order_by(wellness_ai_recommendations.c.rec_date.desc())
        .limit(1)
    )
    return (row["body_text"] or "").strip() if row else None


_REC_SYSTEM = """Ты — аналитический модуль NeuroFungi AI (не врач, не ставишь диагнозов).

Правила:
- Только информационные формулировки: «можно рассмотреть», «наблюдается тенденция».
- Не медицинские назначения; не жёсткие дозировки как приказ.
- Опирайся на переданные данные пользователя и краткую анонимную сводку по платформе.
- Если данных мало — честно скажи и предложи продолжать отмечать самочувствие.

Формат ответа (русский, без символа # в начале строк):
1) Состояние: ...
2) Что происходит: ...
3) Возможное объяснение (связь с приёмом / сном, осторожно): ...
4) Рекомендация (мягко): ...
5) Цель: ...

Объём до ~1100 символов, конкретно и без воды."""


_METRIC_KPI_SPEC: tuple[tuple[str, str, str, bool], ...] = (
    ("mood_0_10", "mean_mood_0_10", "Настроение", False),
    ("energy_0_10", "mean_energy_0_10", "Энергия", False),
    ("anxiety_0_10", "mean_anxiety_0_10", "Тревога", True),
    ("sleep_quality_0_10", "mean_sleep_quality_0_10", "Сон", False),
    ("concentration_0_10", "mean_concentration_0_10", "Концентрация", False),
)

# Доп. шкалы из снимка (только личный KPI; на платформе в aggregate нет средних по ним).
_USER_EXTRA_SCALE_KEYS: tuple[tuple[str, str, bool], ...] = (
    ("fatigue_0_10", "Усталость", True),
    ("body_tension_0_10", "Напряжение", True),
    ("apathy_0_10", "Апатия", True),
    ("irritability_0_10", "Раздражительность", True),
    ("appetite_0_10", "Аппетит", False),
    ("libido_0_10", "Либидо", False),
)


def _metric_val_from_m(m: dict[str, Any], key: str) -> Optional[float]:
    v = m.get(key)
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_scale_10(v: float) -> str:
    if abs(v - round(v)) < 0.02:
        return f"{int(round(v))}/10"
    return f"{round(v, 1)}/10"


def build_wellness_kpis_platform_block(
    plat: dict[str, Any], *, chart_range_label: str
) -> dict[str, Any]:
    """Краткий KPI-блок для режима «вся платформа» (уже есть в aggregate_platform_snapshot_means)."""
    chips: list[dict[str, Any]] = []
    n_rows = int(plat.get("snapshot_rows") or 0)
    chips.append(
        {
            "label": "Строк снимков",
            "value": str(n_rows),
            "detail": "суммарно по всем пользователям в окне",
        }
    )
    for _uk, pk, title, _invert in _METRIC_KPI_SPEC:
        mv = plat.get(pk)
        if mv is None:
            continue
        try:
            mvf = float(mv)
        except (TypeError, ValueError):
            continue
        chips.append(
            {
                "label": f"{title} Ø",
                "value": _fmt_scale_10(mvf),
                "detail": "среднее по всем ответам в окне",
            }
        )
    footnotes = [
        "Не медицина. Средние по анонимным снимкам за выбранный период.",
    ]
    return {
        "wellness_kpi_heading": f"Платформа · сводка ({chart_range_label})",
        "wellness_kpi_chips": chips,
        "wellness_kpi_footnotes": footnotes,
    }


def build_wellness_kpis_user_block(
    series: list[dict[str, Any]],
    *,
    chart_range_label: str,
    days_in_window: int,
    platform_means: dict[str, Any],
) -> dict[str, Any]:
    """Расширенные KPI пользователя за окно + сравнение с анонимным средним платформы за тот же период."""
    chips: list[dict[str, Any]] = []
    footnotes: list[str] = []
    n = len(series)
    if n == 0:
        return {
            "wellness_kpi_heading": "",
            "wellness_kpi_chips": [],
            "wellness_kpi_footnotes": [],
        }

    first_d = str(series[0].get("date") or "")[:10]
    last_d = str(series[-1].get("date") or "")[:10]
    chips.append(
        {
            "label": "Дней с данными",
            "value": f"{n}/{max(1, int(days_in_window))}",
            "detail": f"период {first_d} — {last_d}" if first_d and last_d else "в выбранном окне",
        }
    )

    took_y = took_n = took_u = 0
    dose_days = 0
    composites: list[int] = []
    for x in series:
        m = x.get("m") or {}
        t = m.get("took_mushrooms_today")
        if t is True:
            took_y += 1
        elif t is False:
            took_n += 1
        else:
            took_u += 1
        dose_raw = m.get("dosage_amount_text")
        if dose_raw is not None and str(dose_raw).strip():
            dose_days += 1
        mo = _metric_val_from_m(m, "mood_0_10")
        en = _metric_val_from_m(m, "energy_0_10")
        ax = _metric_val_from_m(m, "anxiety_0_10")
        ci = wellness_composite_index_percent(mo, en, ax)
        if ci is not None:
            composites.append(int(ci))

    if took_y or took_n or took_u:
        chips.append(
            {
                "label": "Отметки «грибы сегодня»",
                "value": f"да {took_y} · нет {took_n}" + (f" · — {took_u}" if took_u else ""),
                "detail": "по дням с заполненным полем в опросе",
            }
        )
    if dose_days:
        chips.append(
            {
                "label": "Дней с текстом дозы",
                "value": str(dose_days),
                "detail": "есть запись о дозировке в снимке",
            }
        )
    if composites:
        cm = round(statistics.mean(composites), 1)
        chips.append(
            {
                "label": "Индекс дня Ø",
                "value": f"{cm}%",
                "detail": "по дням, где заполнены шкалы для кольца",
            }
        )

    for uk, pk, title, invert in _METRIC_KPI_SPEC:
        vals: list[float] = []
        for x in series:
            m = x.get("m") or {}
            v = _metric_val_from_m(m, uk)
            if v is not None:
                vals.append(v)
        if not vals:
            continue
        mean_u = statistics.mean(vals)
        mn, mx = min(vals), max(vals)
        std = round(statistics.pstdev(vals), 2) if len(vals) >= 2 else None
        detail = f"min {mn:.1f} · max {mx:.1f}"
        if std is not None:
            detail += f" · σ {std}"
        chip: dict[str, Any] = {
            "label": f"{title} Ø",
            "value": _fmt_scale_10(mean_u),
            "detail": detail,
        }
        plat_mv = platform_means.get(pk)
        if plat_mv is not None:
            try:
                pv = float(plat_mv)
                delta = round(mean_u - pv, 2)
                if abs(delta) < 0.05:
                    chip["compare"] = "как среднее по платформе"
                elif (delta > 0 and not invert) or (delta < 0 and invert):
                    chip["compare"] = f"↑ на {abs(delta):.1f} к платформе"
                else:
                    chip["compare"] = f"↓ на {abs(delta):.1f} к платформе"
            except (TypeError, ValueError):
                pass
        chips.append(chip)

    for uk, title, invert in _USER_EXTRA_SCALE_KEYS:
        vals = []
        for x in series:
            m = x.get("m") or {}
            v = _metric_val_from_m(m, uk)
            if v is not None:
                vals.append(v)
        if not vals:
            continue
        mean_u = statistics.mean(vals)
        mn, mx = min(vals), max(vals)
        std = round(statistics.pstdev(vals), 2) if len(vals) >= 2 else None
        detail = f"min {mn:.1f} · max {mx:.1f}"
        if std is not None:
            detail += f" · σ {std}"
        chips.append(
            {
                "label": f"{title} Ø",
                "value": _fmt_scale_10(mean_u),
                "detail": detail + (" · чем ниже, тем лучше" if invert else ""),
            }
        )

    footnotes.append(
        "Сравнение с анонимным средним по всем пользователям за тот же календарный период (окно вкладки)."
    )
    footnotes.append("Не диагноз и не назначение; только самонаблюдение.")

    return {
        "wellness_kpi_heading": f"Ваши показатели ({chart_range_label})",
        "wellness_kpi_chips": chips,
        "wellness_kpi_footnotes": footnotes,
    }


async def aggregate_platform_snapshot_means(days: int = 14) -> dict[str, Any]:
    since = _utc_today() - timedelta(days=days)
    rows = await database.fetch_all(
        wellness_daily_snapshots.select().where(wellness_daily_snapshots.c.snapshot_date >= since)
    )
    acc: dict[str, list[float]] = {}
    n = 0
    for r in rows:
        try:
            m = json.loads(r["metrics_json"] or "{}")
        except json.JSONDecodeError:
            continue
        n += 1
        for k in (
            "anxiety_0_10",
            "mood_0_10",
            "energy_0_10",
            "sleep_quality_0_10",
            "concentration_0_10",
        ):
            v = m.get(k)
            if v is None:
                continue
            try:
                acc.setdefault(k, []).append(float(v))
            except (TypeError, ValueError):
                pass
    out: dict[str, Any] = {"snapshot_rows": n}
    for k, vals in acc.items():
        if vals:
            out[f"mean_{k}"] = round(statistics.mean(vals), 2)
    return out


async def _push_recommendation_dm(notify_uid: int, body: str) -> None:
    from services.wellness_journal_service import MSG_PREFIX, _insert_coach_dm
    from services.system_support_delivery import resolve_wellness_dm_sender_id

    coach = await resolve_wellness_dm_sender_id(int(notify_uid))
    if not coach:
        return
    text = (
        MSG_PREFIX
        + "🤖 Краткий разбор и рекомендация на сегодня (информационно, не медицинский совет):\n\n"
        + body[:3400]
    )
    await _insert_coach_dm(int(coach), int(notify_uid), text)


async def generate_and_store_daily_recommendation(
    user_id: int, *, send_dm_if_enabled: bool = True
) -> bool:
    if not getattr(settings, "OPENAI_API_KEY", None):
        return False
    uid = int(user_id)
    today = _utc_today()
    ex = await database.fetch_one(
        sa.select(wellness_ai_recommendations.c.id)
        .where(wellness_ai_recommendations.c.user_id == uid)
        .where(wellness_ai_recommendations.c.rec_date == today)
    )
    if ex:
        return False
    series = await fetch_snapshots_series(uid, 14)
    if len(series) < 1:
        return False
    agg = await aggregate_platform_snapshot_means(14)
    user_payload = json.dumps(series[-14:], ensure_ascii=False, default=str)[:8000]
    agg_payload = json.dumps(agg, ensure_ascii=False)[:4000]
    try:
        from openai import AsyncOpenAI

        cli = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        resp = await cli.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _REC_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "Данные пользователя за период (JSON по дням):\n"
                        f"{user_payload}\n\n"
                        f"Анонимные средние по платформе:\n{agg_payload}"
                    ),
                },
            ],
            temperature=0.35,
            max_tokens=900,
        )
        body = (resp.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("wellness_insights: recommendation AI failed uid=%s", uid)
        return False
    if len(body) < 40:
        return False
    try:
        await database.execute(
            wellness_ai_recommendations.insert().values(
                user_id=uid, rec_date=today, body_text=body[:12000]
            )
        )
    except Exception:
        logger.exception("wellness_insights: save recommendation uid=%s", uid)
        return False
    if send_dm_if_enabled:
        try:
            row = await database.fetch_one(users.select().where(users.c.id == uid))
            notify = int(row.get("primary_user_id") or uid) if row else uid
            await _push_recommendation_dm(notify, body)
        except Exception:
            logger.debug("wellness_insights: dm skip", exc_info=True)
    return True


async def admin_platform_series_for_charts(days: int = 30) -> list[dict[str, Any]]:
    since = _utc_today() - timedelta(days=days)
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT snapshot_date::text AS d,
              AVG((metrics_json::json->>'anxiety_0_10')::float)
                FILTER (WHERE (metrics_json::json->>'anxiety_0_10') IS NOT NULL
                  AND (metrics_json::json->>'anxiety_0_10') ~ '^[0-9.]+$') AS anx,
              AVG((metrics_json::json->>'mood_0_10')::float)
                FILTER (WHERE (metrics_json::json->>'mood_0_10') IS NOT NULL
                  AND (metrics_json::json->>'mood_0_10') ~ '^[0-9.]+$') AS mood,
              AVG((metrics_json::json->>'energy_0_10')::float)
                FILTER (WHERE (metrics_json::json->>'energy_0_10') IS NOT NULL
                  AND (metrics_json::json->>'energy_0_10') ~ '^[0-9.]+$') AS energy,
              AVG((metrics_json::json->>'sleep_quality_0_10')::float)
                FILTER (WHERE (metrics_json::json->>'sleep_quality_0_10') IS NOT NULL
                  AND (metrics_json::json->>'sleep_quality_0_10') ~ '^[0-9.]+$') AS sleep_q,
              AVG((metrics_json::json->>'concentration_0_10')::float)
                FILTER (WHERE (metrics_json::json->>'concentration_0_10') IS NOT NULL
                  AND (metrics_json::json->>'concentration_0_10') ~ '^[0-9.]+$') AS conc,
              COUNT(*)::int AS n
            FROM wellness_daily_snapshots
            WHERE snapshot_date >= :since
            GROUP BY snapshot_date
            ORDER BY snapshot_date ASC
            """
        ),
        {"since": since},
    )
    return [dict(r) for r in rows]


async def admin_user_ids_with_snapshots(limit: int = 200) -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT u.id, u.name, u.email, COUNT(s.id)::int AS snap_n
            FROM wellness_daily_snapshots s
            JOIN users u ON u.id = s.user_id AND u.primary_user_id IS NULL
            GROUP BY u.id, u.name, u.email
            ORDER BY MAX(s.snapshot_date) DESC NULLS LAST
            LIMIT :lim
            """
        ),
        {"lim": limit},
    )
    return [dict(r) for r in rows]


async def refresh_scheme_effect_stats_simple() -> None:
    """Грубая эвристика: дельта настроения/энергии между соседними днями по user_id."""
    since = _utc_today() - timedelta(days=60)
    rows = await database.fetch_all(
        wellness_daily_snapshots.select()
        .where(wellness_daily_snapshots.c.snapshot_date >= since)
        .order_by(wellness_daily_snapshots.c.user_id, wellness_daily_snapshots.c.snapshot_date)
    )
    by_uid: dict[int, list[Any]] = defaultdict(list)
    for r in rows:
        by_uid[int(r["user_id"])].append(r)
    bucket: dict[tuple[str, str], list[float]] = defaultdict(list)
    for uid, lst in by_uid.items():
        for i in range(1, len(lst)):
            prev, cur = lst[i - 1], lst[i]
            try:
                p = json.loads(prev["metrics_json"] or "{}")
                c = json.loads(cur["metrics_json"] or "{}")
            except json.JSONDecodeError:
                continue
            mush = c.get("mushrooms") or p.get("mushrooms")
            if isinstance(mush, list) and mush:
                mk = str(mush[0]).strip().lower()[:120] or "не указано"
            elif isinstance(mush, str) and mush.strip():
                mk = mush.strip().lower()[:120]
            else:
                mk = "не указано"
            seg = str(dict(cur).get("wellness_segment") or dict(prev).get("wellness_segment") or "")[
                :80
            ]
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
            bucket[(mk, seg)].append(score / npt)
    await database.execute(wellness_scheme_effect_stats.delete())
    for (mk, seg), vals in bucket.items():
        if len(vals) < 2:
            continue
        await database.execute(
            wellness_scheme_effect_stats.insert().values(
                mushroom_key=mk,
                segment=seg or "",
                sample_n=len(vals),
                avg_progress_score=round(statistics.mean(vals), 4),
            )
        )


def _parse_float_row(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def slice_platform_series_rows(rows: list[dict[str, Any]], days_inclusive: int) -> list[dict[str, Any]]:
    if days_inclusive < 1 or not rows:
        return []
    end = _utc_today()
    start = end - timedelta(days=days_inclusive - 1)
    start_iso = start.isoformat()
    return [r for r in rows if str(r.get("d") or "") >= start_iso]


def minimal_admin_user_insights_shell(range_raw: str) -> dict[str, Any]:
    """Пустой контекст при mode=user без выбранного user_id (только вкладки периода)."""
    chart_range, _ = parse_wellness_chart_range(range_raw)
    range_labels = {"day": "2 дня", "week": "7 дней", "month": "30 дней"}
    rl = {"day": "d", "week": "w", "month": "m"}.get(chart_range, "w")
    return {
        "chart_range": chart_range,
        "chart_range_label": range_labels.get(chart_range, ""),
        "chart_range_letter": rl,
        "wd_tab_urls": _tab_urls_for_wellness("/admin/wellness-journal/insights", "mode=user"),
        "insights_view": "user",
        "user_charts": [],
        "heatmap_rows": [],
        "heatmap_mode": "user",
        "wellness_week_strip_show_extremes": False,
        "wellness_kpi_heading": "",
        "wellness_kpi_chips": [],
        "wellness_kpi_footnotes": [],
    }


def _tab_urls_for_wellness(base: str, extra: str) -> dict[str, str]:
    q = (extra or "").strip().strip("&")
    if q:
        return {
            "d": f"{base}?range=d&{q}",
            "w": f"{base}?range=w&{q}",
            "m": f"{base}?range=m&{q}",
        }
    return {"d": f"{base}?range=d", "w": f"{base}?range=w", "m": f"{base}?range=m"}


async def build_user_insights_dashboard_context(
    user_id: int,
    range_raw: str,
    *,
    canvas_prefix: str = "wr",
    tab_url_base: str = "/account/wellness-results",
    tab_extra_query: str = "",
) -> dict[str, Any]:
    """Контекст дашборда снимков для страницы пользователя или админки (один user_id)."""
    chart_range, range_days = parse_wellness_chart_range(range_raw)
    uid = int(user_id)
    series_long = await fetch_snapshots_series(uid, 40)
    series_chart = slice_series_calendar_days(series_long, range_days)
    wellness_week_strip = calendar_week_strip_for_user(series_long)
    wellness_week_strip_show_extremes = annotate_week_strip_best_worst(
        wellness_week_strip, series_long
    )

    streak = await count_checkin_streak(uid)
    rec_text = await latest_recommendation_text(uid)
    segment = await compute_segment_for_user(uid)
    mood_prog = quick_mood_progress_percent(series_chart)
    today_mood = latest_metric_value(series_long, "mood_0_10")
    today_energy = latest_metric_value(series_long, "energy_0_10")
    today_anxiety = latest_metric_value(series_long, "anxiety_0_10")
    wellness_index_pct = wellness_composite_index_percent(today_mood, today_energy, today_anxiety)

    spark_src = series_long[-14:] if len(series_long) > 14 else series_long
    _, sp_m = series_metric_arrays(spark_src, "mood_0_10")
    _, sp_e = series_metric_arrays(spark_src, "energy_0_10")
    _, sp_a = series_metric_arrays(spark_src, "anxiety_0_10")
    spark_mood_pts = sparkline_polyline_points(sp_m)
    spark_energy_pts = sparkline_polyline_points(sp_e)
    spark_anxiety_pts = sparkline_polyline_points(sp_a)

    pfx = (canvas_prefix or "wr").rstrip("-")
    user_charts: list[dict[str, Any]] = []
    for mk, ru, col in (
        ("anxiety_0_10", "Тревога", "#f472b6"),
        ("mood_0_10", "Настроение", "#3dd4e0"),
        ("energy_0_10", "Энергия", "#a78bfa"),
        ("sleep_quality_0_10", "Сон", "#34d399"),
        ("concentration_0_10", "Концентрация", "#fbbf24"),
    ):
        lab, dat = series_metric_arrays(series_chart, mk)
        if any(x is not None for x in dat):
            user_charts.append(
                {
                    "canvas_id": f"{pfx}-" + mk.replace("_", "-") + "-" + chart_range,
                    "config": chartjs_line_config_dict(
                        short_chart_date_labels(lab),
                        dat,
                        dataset_label=ru,
                        border_color=col,
                    ),
                }
            )
    if chart_range != "day":
        sc_cfg = dosage_mood_scatter_chart_config(series_chart)
        if sc_cfg:
            user_charts.append({"canvas_id": f"{pfx}-dose-mood-" + chart_range, "config": sc_cfg})

    heatmap_rows: list[dict[str, Any]] = []
    for x in series_chart:
        m = x.get("m") or {}
        dose_raw = m.get("dosage_amount_text")
        heatmap_rows.append(
            {
                "d": x.get("date"),
                "anxiety": m.get("anxiety_0_10"),
                "mood": m.get("mood_0_10"),
                "energy": m.get("energy_0_10"),
                "sleep": m.get("sleep_quality_0_10"),
                "conc": m.get("concentration_0_10"),
                "dose": (str(dose_raw).strip()[:32] if dose_raw else ""),
                "took": m.get("took_mushrooms_today"),
            }
        )

    stab = mood_stability_pstdev_last(
        series_chart, max_days=min(7, max(2, len(series_chart)))
    )
    range_labels = {"day": "2 дня", "week": "7 дней", "month": "30 дней"}
    range_letter = {"day": "d", "week": "w", "month": "m"}.get(chart_range, "w")
    plat_means_window = await aggregate_platform_snapshot_means(range_days)
    kpi_blk = build_wellness_kpis_user_block(
        series_chart,
        chart_range_label=range_labels.get(chart_range, ""),
        days_in_window=range_days,
        platform_means=plat_means_window,
    )
    return {
        "chart_range": chart_range,
        "chart_range_days": range_days,
        "chart_range_label": range_labels.get(chart_range, ""),
        "chart_range_letter": range_letter,
        "wellness_week_strip": wellness_week_strip,
        "wellness_week_strip_show_extremes": wellness_week_strip_show_extremes,
        "wellness_streak": streak,
        "wellness_segment": segment,
        "wellness_rec_text": rec_text,
        "wellness_mood_progress_pct": mood_prog,
        "today_mood": today_mood,
        "today_energy": today_energy,
        "today_anxiety": today_anxiety,
        "wellness_index_pct": wellness_index_pct,
        "spark_mood_pts": spark_mood_pts,
        "spark_energy_pts": spark_energy_pts,
        "spark_anxiety_pts": spark_anxiety_pts,
        "user_charts": user_charts,
        "heatmap_rows": heatmap_rows,
        "heatmap_mode": "user",
        "wellness_stability": stab,
        "wellness_series_n": len(series_chart),
        "wellness_series_long_n": len(series_long),
        "wd_tab_urls": _tab_urls_for_wellness(tab_url_base, tab_extra_query),
        "insights_view": "user",
        **kpi_blk,
    }


async def build_platform_insights_dashboard_context(
    range_raw: str,
    *,
    canvas_prefix: str = "adm-plat",
) -> dict[str, Any]:
    """Агрегированный дашборд по всем снимкам (средние по дням)."""
    chart_range, range_days = parse_wellness_chart_range(range_raw)
    plat_full = await admin_platform_series_for_charts(40)
    plat_slice = slice_platform_series_rows(plat_full, range_days)
    labels = short_chart_date_labels([str(r.get("d") or "") for r in plat_slice])

    pfx = (canvas_prefix or "adm-plat").rstrip("-")
    charts: list[dict[str, Any]] = []
    for key, ru, col in (
        ("anx", "Тревога (ср.)", "#f472b6"),
        ("mood", "Настроение (ср.)", "#3dd4e0"),
        ("energy", "Энергия (ср.)", "#a78bfa"),
        ("sleep_q", "Сон (ср.)", "#34d399"),
        ("conc", "Концентрация (ср.)", "#fbbf24"),
    ):
        dat = [_parse_float_row(r.get(key)) for r in plat_slice]
        if any(x is not None for x in dat):
            charts.append(
                {
                    "canvas_id": f"{pfx}-{key}-{chart_range}",
                    "config": chartjs_line_config_dict(
                        labels, dat, dataset_label=ru, border_color=col
                    ),
                }
            )

    plat_means = await aggregate_platform_snapshot_means(range_days)
    activity = await admin_snapshot_counts_rolling_days(max(range_days, 2))

    tail = plat_full[-14:] if len(plat_full) > 14 else plat_full
    sp_m = [_parse_float_row(r.get("mood")) for r in tail]
    sp_e = [_parse_float_row(r.get("energy")) for r in tail]
    sp_a = [_parse_float_row(r.get("anx")) for r in tail]
    spark_mood_pts = sparkline_polyline_points(sp_m)
    spark_energy_pts = sparkline_polyline_points(sp_e)
    spark_anxiety_pts = sparkline_polyline_points(sp_a)

    last = plat_slice[-1] if plat_slice else (plat_full[-1] if plat_full else {})
    today_mood = _parse_float_row(last.get("mood"))
    today_energy = _parse_float_row(last.get("energy"))
    today_anxiety = _parse_float_row(last.get("anx"))
    wellness_index_pct = wellness_composite_index_percent(today_mood, today_energy, today_anxiety)

    mood_prog = None
    if len(plat_slice) >= 2:
        m0 = _parse_float_row(plat_slice[0].get("mood"))
        m1 = _parse_float_row(plat_slice[-1].get("mood"))
        if m0 is not None and m1 is not None:
            mood_prog = round((m1 - m0) / 10.0 * 100.0, 1)

    fake_series = [
        {"m": {"mood_0_10": _parse_float_row(r.get("mood"))}} for r in plat_slice
    ]
    stab = mood_stability_pstdev_last(
        fake_series, max_days=min(7, max(2, len(fake_series)))
    )

    heatmap_rows: list[dict[str, Any]] = []
    for r in plat_slice:
        heatmap_rows.append(
            {
                "d": str(r.get("d") or ""),
                "n": int(r.get("n") or 0),
                "anxiety": _parse_float_row(r.get("anx")),
                "mood": _parse_float_row(r.get("mood")),
                "energy": _parse_float_row(r.get("energy")),
                "sleep": _parse_float_row(r.get("sleep_q")),
                "conc": _parse_float_row(r.get("conc")),
            }
        )

    range_labels = {"day": "2 дня", "week": "7 дней", "month": "30 дней"}
    range_letter = {"day": "d", "week": "w", "month": "m"}.get(chart_range, "w")
    base = "/admin/wellness-journal/insights"
    extra = "mode=platform"
    kpi_plat = build_wellness_kpis_platform_block(
        plat_means, chart_range_label=range_labels.get(chart_range, "")
    )
    return {
        "chart_range": chart_range,
        "chart_range_days": range_days,
        "chart_range_label": range_labels.get(chart_range, ""),
        "chart_range_letter": range_letter,
        "wellness_week_strip": [],
        "wellness_week_strip_show_extremes": False,
        "plat_activity_strip": activity,
        "wellness_streak": int(plat_means.get("snapshot_rows") or 0),
        "wellness_segment": "Среднее по всем пользователям за выбранное окно (анонимно).",
        "wellness_rec_text": None,
        "wellness_mood_progress_pct": mood_prog,
        "today_mood": today_mood,
        "today_energy": today_energy,
        "today_anxiety": today_anxiety,
        "wellness_index_pct": wellness_index_pct,
        "spark_mood_pts": spark_mood_pts,
        "spark_energy_pts": spark_energy_pts,
        "spark_anxiety_pts": spark_anxiety_pts,
        "user_charts": charts,
        "heatmap_rows": heatmap_rows,
        "heatmap_mode": "platform",
        "wellness_stability": stab,
        "wellness_series_n": len(plat_slice),
        "wellness_series_long_n": len(plat_full),
        "platform_kpis": plat_means,
        "wd_tab_urls": _tab_urls_for_wellness(base, extra),
        "insights_view": "platform",
        **kpi_plat,
    }


async def run_daily_wellness_recommendations_job() -> None:
    from services.wellness_journal_service import user_has_wellness_journal_access, wellness_journal_globally_enabled

    if not await wellness_journal_globally_enabled():
        return
    rows = await database.fetch_all(
        users.select()
        .where(users.c.wellness_journal_opt_out.is_(False))
        .where(users.c.primary_user_id.is_(None))
        .order_by(users.c.id.asc())
        .limit(280)
    )
    for r in rows:
        uid = int(r["id"])
        if not await user_has_wellness_journal_access(uid):
            continue
        n = await database.fetch_val(
            sa.select(sa.func.count())
            .select_from(wellness_daily_snapshots)
            .where(wellness_daily_snapshots.c.user_id == uid)
            .where(wellness_daily_snapshots.c.snapshot_date >= _utc_today() - timedelta(days=14))
        ) or 0
        if int(n) < 1:
            continue
        await generate_and_store_daily_recommendation(uid, send_dm_if_enabled=True)
