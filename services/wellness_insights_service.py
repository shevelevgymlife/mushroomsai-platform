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
