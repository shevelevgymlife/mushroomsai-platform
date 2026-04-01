"""
Единая точка: какой способ оплаты подписок активен (сайт и бот).
Приоритет задаётся в админке (Оплата) или «авто»: CloudPayments → ЮKassa.
Telegram Stars для подписок — только в боте (счёт XTR), включается в Оплата → Telegram Stars.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any

from db.database import database
from db.models import platform_settings

from services.payment_plans_catalog import get_effective_plans, is_catalog_paid_checkout_plan
from services.payment_provider_settings import get_provider_settings
from services.yookassa_bot_offerings import (
    find_offering_id_for_plan,
    get_merged_bot_offerings,
    yookassa_web_pay_ready,
)

logger = logging.getLogger(__name__)

_CHECKOUT_KEY = "subscription_checkout"
_VALID_PREFS = frozenset({"auto", "cloudpayments", "yookassa_bot"})


def subscription_stars_amount(price_rub: float, stars_per_rub: float) -> int:
    """Число Stars для счёта XTR: ceil(price_rub * stars_per_rub), минимум 1 при положительной цене."""
    pr = float(price_rub or 0)
    spr = float(stars_per_rub or 0)
    if pr <= 0 or spr <= 0:
        return 0
    return max(1, math.ceil(pr * spr))


async def telegram_stars_subscription_meta() -> dict[str, Any]:
    """Настройки Stars для меню подписки в боте (payment_provider:telegram_stars)."""
    st = await get_provider_settings("telegram_stars")
    enabled = bool(st.get("enabled"))
    offer = bool(st.get("offer_subscriptions"))
    raw = str(st.get("stars_per_rub") or "").strip().replace(",", ".")
    try:
        spr = float(raw) if raw else 0.0
    except ValueError:
        spr = 0.0
    if spr <= 0:
        spr = 0.55
    spr = max(0.01, min(5000.0, spr))
    return {
        "enabled": enabled,
        "offer_subscriptions": offer,
        "stars_per_rub": spr,
        "available_for_subscriptions": enabled and offer,
    }


async def get_subscription_checkout_preference() -> str:
    """auto | cloudpayments | yookassa_bot"""
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == _CHECKOUT_KEY)
        )
        if not row or not row.get("value"):
            return "auto"
        data = json.loads(row["value"])
        p = (data.get("primary_provider") or "auto").strip().lower()
        return p if p in _VALID_PREFS else "auto"
    except Exception:
        logger.debug("get_subscription_checkout_preference failed", exc_info=True)
        return "auto"


async def save_subscription_checkout_preference(primary_provider: str) -> None:
    p = (primary_provider or "auto").strip().lower()
    if p not in _VALID_PREFS:
        p = "auto"
    raw = json.dumps({"primary_provider": p}, ensure_ascii=False)
    exists = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == _CHECKOUT_KEY)
    )
    if exists:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == _CHECKOUT_KEY)
            .values(value=raw)
        )
    else:
        await database.execute(platform_settings.insert().values(key=_CHECKOUT_KEY, value=raw))


async def resolve_active_subscription_checkout() -> dict[str, Any]:
    """
    Возвращает единый режим для UI и бота.

    kind: cloudpayments | yookassa | none
    """
    pref = await get_subscription_checkout_preference()
    cp = await get_provider_settings("cloudpayments")
    cp_ok = bool(cp.get("enabled") and (cp.get("public_id") or "").strip())
    yb = await get_provider_settings("yookassa_bot")
    yk_web = yookassa_web_pay_ready(yb)
    yk_bot = bool(yb.get("enabled") and (yb.get("provider_token") or "").strip())

    offerings: list[dict[str, Any]] = []
    try:
        offerings = await get_merged_bot_offerings()
    except Exception:
        logger.exception("get_merged_bot_offerings in checkout")

    plans = await get_effective_plans()
    offering_id_by_plan: dict[str, str | None] = {}
    for pk, p in plans.items():
        if is_catalog_paid_checkout_plan(plans, pk):
            offering_id_by_plan[pk] = find_offering_id_for_plan(offerings, pk) or pk
        else:
            offering_id_by_plan[pk] = None

    def pick_auto() -> str:
        if cp_ok:
            return "cloudpayments"
        if yk_web or yk_bot:
            return "yookassa"
        return "none"

    def kind_for_pref() -> str:
        if pref == "auto":
            return pick_auto()
        if pref == "cloudpayments":
            return "cloudpayments" if cp_ok else "none"
        if pref == "yookassa_bot":
            return "yookassa" if (yk_web or yk_bot) else "none"
        return pick_auto()

    kind = kind_for_pref()
    tsm = await telegram_stars_subscription_meta()

    return {
        "kind": kind,
        "preference": pref,
        "cloudpayments_enabled": cp_ok,
        "cloudpayments_public_id": (cp.get("public_id") or "").strip() if cp_ok else "",
        "yookassa_web_pay_enabled": yk_web,
        "yookassa_bot_invoice_enabled": yk_bot,
        "yookassa_site_checkout_available": kind == "yookassa" and yk_web,
        "yookassa_bot_only": kind == "yookassa" and not yk_web and yk_bot,
        "offering_id_by_plan": offering_id_by_plan,
        "offerings": offerings,
        "telegram_stars_subscriptions_enabled": bool(tsm.get("available_for_subscriptions")),
        "telegram_stars_per_rub": float(tsm.get("stars_per_rub") or 0.55),
    }
