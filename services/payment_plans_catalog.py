"""
Тарифы по умолчанию + переопределения из platform_settings (ключ subscription_plans_overrides).
Используется для цен, названий, описаний и списков преимуществ на странице подписок и в админке.
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any

from db.database import database
from db.models import platform_settings

logger = logging.getLogger(__name__)

SUBSCRIPTION_OVERRIDES_KEY = "subscription_plans_overrides"

# Единый источник структуры тарифов (совпадает с бывшим PLANS в subscription_service).
DEFAULT_PLANS: dict[str, dict[str, Any]] = {
    "free": {
        "name": "Бесплатный",
        "price": 0,
        "questions_per_day": 5,
        "recipes_per_day": 1,
        "description": "",
        "features": [
            "5 вопросов AI",
            "Личный кабинет",
            "Магазин",
        ],
    },
    "start": {
        "name": "Старт",
        "price": 990,
        "questions_per_day": -1,
        "recipes_per_day": -1,
        "description": "",
        "features": [
            "Безлимитные консультации",
            "История переписки с AI 1 месяц",
            "Приоритетные ответы",
            "Соц. сеть внутри кабинета — общение, чаты, фото, посты",
            "Доступ к маркетплейсу с лучшими магазинами и товарами. Отзывы поставщиков, клиентов, рейтинги",
            "Партнёрство по реферальной программе поставщиков",
        ],
    },
    "pro": {
        "name": "Про",
        "price": 1990,
        "questions_per_day": -1,
        "recipes_per_day": -1,
        "description": "",
        "features": [
            "Всё из Старта",
            "Доступ в закрытый Telegram-канал — партнёрство, кейсы, знания",
            "Приоритетный аккаунт в соц. сети NEUROFUNGI AI + закреп постов в ленте",
        ],
    },
    "maxi": {
        "name": "Макси",
        "price": 4999,
        "questions_per_day": -1,
        "recipes_per_day": -1,
        "description": "",
        "features": [
            "Всё из Про",
            "Доступ к подаче рекламы на маркетплейсе NEUROFUNGI AI + Админка товаров",
        ],
    },
}

PLAN_KEYS = ("free", "start", "pro", "maxi")


def _deep_merge_plan(base: dict[str, Any], over: dict[str, Any] | None) -> dict[str, Any]:
    if not over:
        return copy.deepcopy(base)
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k == "features" and isinstance(v, list):
            lines = [str(x).strip() for x in v if str(x).strip()]
            if lines:
                out["features"] = lines
        elif k == "drawer_features" and isinstance(v, list):
            lines = [str(x).strip() for x in v if str(x).strip()]
            if lines:
                out["drawer_features"] = lines
            else:
                out.pop("drawer_features", None)
        elif k == "show_in_catalog":
            if isinstance(v, bool):
                out["show_in_catalog"] = v
            elif v is not None:
                out["show_in_catalog"] = str(v).strip().lower() in ("1", "true", "yes", "on")
        elif k == "price" and v is not None:
            try:
                out["price"] = max(0, int(v))
            except (TypeError, ValueError):
                pass
        elif k in ("name", "description") and v is not None:
            out[k] = str(v).strip()[:2000]
        elif k in ("questions_per_day", "recipes_per_day") and v is not None:
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                pass
    return out


async def load_subscription_overrides_raw() -> dict[str, Any]:
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == SUBSCRIPTION_OVERRIDES_KEY)
        )
        if not row or not row.get("value"):
            return {}
        return json.loads(row["value"])
    except Exception:
        logger.debug("load_subscription_overrides_raw failed", exc_info=True)
        return {}


async def save_subscription_overrides_raw(data: dict[str, Any]) -> None:
    raw = json.dumps(data, ensure_ascii=False)
    exists = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == SUBSCRIPTION_OVERRIDES_KEY)
    )
    if exists:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == SUBSCRIPTION_OVERRIDES_KEY)
            .values(value=raw)
        )
    else:
        await database.execute(platform_settings.insert().values(key=SUBSCRIPTION_OVERRIDES_KEY, value=raw))


async def get_effective_plans() -> dict[str, dict[str, Any]]:
    """Полные карточки тарифов с учётом админских переопределений."""
    raw = await load_subscription_overrides_raw()
    out: dict[str, dict[str, Any]] = {}
    for pk in PLAN_KEYS:
        base = DEFAULT_PLANS.get(pk) or DEFAULT_PLANS["free"]
        merged = _deep_merge_plan(base, raw.get(pk) if isinstance(raw.get(pk), dict) else None)
        merged.setdefault("show_in_catalog", True)
        out[pk] = merged
    return out


def plan_drawer_lines(plan: dict[str, Any] | None) -> list[str]:
    """Строки для блока подписки в бургере: отдельный список или те же пункты, что в карточке тарифа."""
    if not plan:
        return []
    df = plan.get("drawer_features")
    if isinstance(df, list) and df:
        return [str(x).strip() for x in df if str(x).strip()]
    feats = plan.get("features")
    if isinstance(feats, list):
        return [str(x).strip() for x in feats if str(x).strip()]
    return []


def visible_plan_keys_from(plans: dict[str, dict[str, Any]]) -> list[str]:
    """Ключи тарифов для витрины по уже загруженному словарю `get_effective_plans()`."""
    return [pk for pk in PLAN_KEYS if plans.get(pk, {}).get("show_in_catalog", True)]


async def visible_plan_keys_ordered() -> list[str]:
    """Ключи тарифов, которые показываются в витрине (главная, /subscriptions)."""
    plans = await get_effective_plans()
    return visible_plan_keys_from(plans)


async def plan_display_name(plan_key: str | None) -> str:
    k = (plan_key or "free").lower()
    plans = await get_effective_plans()
    return (plans.get(k) or plans["free"])["name"]


# Синхронный доступ к ключам тарифа (только проверка membership), без цены из БД
def plan_keys_set() -> frozenset[str]:
    return frozenset(PLAN_KEYS)
