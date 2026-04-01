"""
Витрина оплаты ЮKassa (бот / сайт / Mini App): строки строятся только из каталога «Тарифы подписок».
Цена, срок и видимость — из subscription_plans_overrides; отдельная таблица «предложений» в настройках провайдера не используется.
"""
from __future__ import annotations

import re
from typing import Any

from services.payment_plans_catalog import (
    format_catalog_billing_label,
    get_effective_plans,
    is_catalog_paid_checkout_plan,
)

OFFERING_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def catalog_rows_for_yookassa(plans: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Синтетические «предложения»: id = slug тарифа, цена и подпись срока из каталога."""
    out: list[dict[str, Any]] = []
    for pk, p in plans.items():
        if not is_catalog_paid_checkout_plan(plans, pk):
            continue
        pr = int(p.get("price") or 0)
        out.append(
            {
                "id": pk,
                "enabled": True,
                "label": "",
                "price_rub": pr,
                "effective_plan": pk,
                "show_on_site": bool(p.get("show_in_catalog", True)),
                "display_name": p.get("name") or pk,
                "duration_label": format_catalog_billing_label(p),
            }
        )
    return out


async def get_merged_bot_offerings() -> list[dict[str, Any]]:
    plans = await get_effective_plans()
    return catalog_rows_for_yookassa(plans)


async def load_raw_offerings(plans: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Совместимость с ботом: тот же список, что и get_merged (без JSON offerings в БД)."""
    if plans is None:
        plans = await get_effective_plans()
    return catalog_rows_for_yookassa(plans)


def yookassa_web_pay_ready(provider_cfg: dict[str, Any] | None) -> bool:
    """Оплата на сайте через API ЮKassa (без BotFather provider token)."""
    if not provider_cfg or not provider_cfg.get("enabled"):
        return False
    from services.yookassa_credentials import override_yookassa_shop_active

    if override_yookassa_shop_active():
        return True
    return bool((provider_cfg.get("shop_id") or "").strip() and (provider_cfg.get("secret_key") or "").strip())


def find_offering_id_for_plan(offerings: list[dict[str, Any]], plan_key: str) -> str | None:
    """id совпадает со slug тарифа из каталога."""
    pk = (plan_key or "").strip().lower()
    for o in offerings:
        if not o.get("enabled"):
            continue
        oid = str(o.get("id") or "").strip().lower()
        if oid == pk:
            return oid
    return None


def offering_by_id(offerings: list[dict[str, Any]], oid: str) -> dict[str, Any] | None:
    key = (oid or "").strip().lower()
    for r in offerings:
        rid = str(r.get("id") or "").strip().lower()
        if rid == key:
            return r
    return None


def format_duration_human(minutes: int) -> str:
    """Короткая строка для UI: дни, часы, минуты (legacy)."""
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
