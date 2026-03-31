"""
Предложения подписки для ЮKassa-бота: цена, срок (минуты), уровень (start/pro/maxi), показ на сайте.
Хранятся в platform_settings payment_provider:yookassa_bot → ключ offerings (JSON-массив).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from services.payment_plans_catalog import get_effective_plans
from services.payment_provider_settings import get_provider_settings

logger = logging.getLogger(__name__)

OFFERING_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
EFFECTIVE_PLAN_KEYS = frozenset({"start", "pro", "maxi"})
DEFAULT_DURATION_MINUTES = 30 * 24 * 60  # 30 дней


def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        x = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def default_offerings_from_catalog(plans: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Три базовых предложения по умолчанию (как раньше — помесячно)."""
    out: list[dict[str, Any]] = []
    for oid in ("start", "pro", "maxi"):
        p = plans.get(oid) or {}
        pr = int(p.get("price") or 0)
        out.append(
            {
                "id": oid,
                "enabled": True,
                "label": "",
                "price_rub": max(0, pr),
                "duration_minutes": DEFAULT_DURATION_MINUTES,
                "effective_plan": oid,
                "show_on_site": True,
            }
        )
    return out


def normalize_offerings_list(raw: Any, plans: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Приводит список предложений к валидному виду; пустой ввод → дефолт из каталога."""
    defaults = default_offerings_from_catalog(plans)
    if not isinstance(raw, list) or not raw:
        return defaults

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("id") or "").strip().lower()
        if not OFFERING_ID_RE.match(oid) or oid in seen:
            continue
        seen.add(oid)

        eff = str(item.get("effective_plan") or oid).strip().lower()
        if eff not in EFFECTIVE_PLAN_KEYS:
            continue

        price_rub = _clamp_int(item.get("price_rub"), 0, 99999999, 0)
        dur = _clamp_int(item.get("duration_minutes"), 1, 525600 * 5, DEFAULT_DURATION_MINUTES)  # до ~5 лет в минутах

        label = str(item.get("label") or "").strip()[:120]

        out.append(
            {
                "id": oid,
                "enabled": bool(item.get("enabled", True)),
                "label": label,
                "price_rub": price_rub,
                "duration_minutes": dur,
                "effective_plan": eff,
                "show_on_site": bool(item.get("show_on_site", True)),
            }
        )

    if not out:
        return defaults

    # гарантируем хотя бы стандартные id при частичной конфигурации — если админ удалил всё кроме кастомных, оставляем как есть
    return out


async def load_raw_offerings() -> list[dict[str, Any]]:
    st = await get_provider_settings("yookassa_bot")
    raw = st.get("offerings")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        raw = []
    plans = await get_effective_plans()
    return normalize_offerings_list(raw, plans)


async def get_merged_bot_offerings() -> list[dict[str, Any]]:
    """Список предложений с подставленными названиями для кнопок (label пустой → имя тарифа из каталога)."""
    plans = await get_effective_plans()
    rows = await load_raw_offerings()
    out = []
    for r in rows:
        eff = r["effective_plan"]
        disp = (r.get("label") or "").strip() or (plans.get(eff) or {}).get("name") or eff
        x = dict(r)
        x["display_name"] = disp
        try:
            dm = int(r.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            dm = DEFAULT_DURATION_MINUTES
        x["duration_label"] = format_duration_human(dm)
        out.append(x)
    return out


def offering_by_id(offerings: list[dict[str, Any]], oid: str) -> dict[str, Any] | None:
    key = (oid or "").strip().lower()
    for r in offerings:
        if r.get("id") == key:
            return r
    return None


def parse_offerings_post(form: Any) -> list[dict[str, Any]]:
    """
    Ожидает поля: offer_id (повтор), offer_enabled, offer_price_rub, offer_duration_minutes,
    offer_effective_plan, offer_label, offer_show_on_site — по одному на строку предложения.
    """
    getlist = getattr(form, "getlist", None)
    ids: list[str] = []
    if getlist:
        ids = [str(x).strip() for x in getlist("offer_id") if str(x).strip()]
    if not ids and hasattr(form, "get"):
        single = (form.get("offer_id") or "").strip()
        if single:
            ids = [single]
    n = len(ids)
    if n == 0:
        return []

    def lst(name: str, default: str = "") -> list[str]:
        if getlist:
            v = [str(x) for x in getlist(name)]
            while len(v) < n:
                v.append(default)
            return v[:n]
        one = form.get(name) if hasattr(form, "get") else None
        return [str(one if one is not None else default)] * n

    enabled = lst("offer_enabled", "0")
    prices = lst("offer_price_rub", "0")
    durs = lst("offer_duration_minutes", str(DEFAULT_DURATION_MINUTES))
    effs = lst("offer_effective_plan", "")
    labels = lst("offer_label", "")
    sites = lst("offer_show_on_site", "0")

    rows = []
    for i in range(n):
        oid = str(ids[i] or "").strip().lower()
        rows.append(
            {
                "id": oid,
                "enabled": str(enabled[i]).strip().lower() in ("1", "true", "on", "yes"),
                "price_rub": prices[i] if i < len(prices) else "0",
                "duration_minutes": durs[i] if i < len(durs) else str(DEFAULT_DURATION_MINUTES),
                "effective_plan": (effs[i] if i < len(effs) else "") or oid,
                "label": labels[i] if i < len(labels) else "",
                "show_on_site": str(sites[i]).strip().lower() in ("1", "true", "on", "yes"),
            }
        )
    return rows


def format_duration_human(minutes: int) -> str:
    """Короткая строка для UI: дни, часы, минуты."""
    m = max(0, int(minutes))
    if m <= 0:
        return "0 мин"
    days, rem = divmod(m, 1440)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} дн")
    if hours:
        parts.append(f"{hours} ч")
    if mins or not parts:
        parts.append(f"{mins} мин")
    return " ".join(parts)
