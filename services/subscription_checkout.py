"""
Единая точка: какой способ оплаты подписок активен (сайт, Mini App, бот).
В админке можно выбрать любой зарегистрированный провайдер; CloudPayments и ЮKassa (веб и бот/Mini App отдельно)
дают рабочую оплату. Тинькофф и крипто сохраняются как выбор,
но поток подписок на сайте для них пока не подключён — kind будет none и показывается подсказка.
Telegram Stars (XTR) — отдельно в карточке провайдера; на сайте показывается ссылка в бота.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any

from config import settings
from db.database import database
from db.models import platform_settings

from services.payment_plans_catalog import get_effective_plans, is_catalog_paid_checkout_plan
from services.payment_provider_settings import PAYMENT_PROVIDERS, get_provider_settings
from services.yookassa_bot_offerings import (
    find_offering_id_for_plan,
    get_merged_bot_offerings,
    yookassa_redirect_api_ready,
)

logger = logging.getLogger(__name__)

_CHECKOUT_KEY = "subscription_checkout"

# Telegram Stars не бывает «основным» RUB-эквайрингом; включается в своей карточке провайдера.
_STARS_PROVIDER_ID = "telegram_stars"


def subscription_checkout_valid_preferences() -> frozenset[str]:
    ids = {"auto"} | {p["id"] for p in PAYMENT_PROVIDERS if p["id"] != _STARS_PROVIDER_ID}
    return frozenset(ids)


def subscription_checkout_select_rows() -> list[dict[str, str]]:
    """Подписи для выпадающего списка в админке «Основной способ оплаты подписок»."""
    rows: list[dict[str, str]] = [
        {
            "id": "auto",
            "label": "Авто: CloudPayments, если включён; иначе ЮKassa (любой настроенный канал)",
            "suffix": "",
        }
    ]
    for p in PAYMENT_PROVIDERS:
        if p["id"] == _STARS_PROVIDER_ID:
            continue
        suffix = ""
        pid = p["id"]
        if pid == "tinkoff":
            suffix = " — оплата подписок на сайте пока не подключена"
        elif pid == "crypto":
            suffix = " — оплата подписок на сайте пока не подключена"
        elif pid == "yookassa":
            suffix = " — магазин для оплаты с сайта в браузере (отдельные shopId/секрет)"
        rows.append({"id": pid, "label": p["title"], "suffix": suffix})
    return rows


def _telegram_bot_username() -> str:
    return (getattr(settings, "TELEGRAM_BOT_USERNAME", None) or "neuro_fungi_bot").strip().lstrip("@")


def telegram_stars_subscribe_deeplink() -> str:
    """Ссылка в бота: сразу открывается меню подписки (обработчик /start subscribe)."""
    return f"https://t.me/{_telegram_bot_username()}?start=subscribe"


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
    """auto | id из PAYMENT_PROVIDERS (кроме telegram_stars)."""
    valid = subscription_checkout_valid_preferences()
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == _CHECKOUT_KEY)
        )
        if not row or not row.get("value"):
            return "auto"
        data = json.loads(row["value"])
        p = (data.get("primary_provider") or "auto").strip().lower()
        return p if p in valid else "auto"
    except Exception:
        logger.debug("get_subscription_checkout_preference failed", exc_info=True)
        return "auto"


async def save_subscription_checkout_preference(primary_provider: str) -> None:
    valid = subscription_checkout_valid_preferences()
    p = (primary_provider or "auto").strip().lower()
    if p not in valid:
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
    yk_site = await get_provider_settings("yookassa")
    yk_browser = yookassa_redirect_api_ready(yk_site)
    yk_telegram_redirect = yookassa_redirect_api_ready(yb)
    yk_web = yk_browser or yk_telegram_redirect
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
        if pref in ("tinkoff", "crypto"):
            return "none"
        if pref == "yookassa":
            return "yookassa" if yk_browser else "none"
        return pick_auto()

    kind = kind_for_pref()
    tsm = await telegram_stars_subscription_meta()
    stars_on = bool(tsm.get("available_for_subscriptions"))

    blocked_hint: str | None = None
    if pref == "tinkoff":
        blocked_hint = (
            "В админке выбран основной способ «Тинькофф Касса», но оплата подписок на сайте и в приложении "
            "для него ещё не подключена. Временно включите CloudPayments или настройте ЮKassa (веб и/или бот)."
        )
    elif pref == "crypto":
        blocked_hint = (
            "Выбран основной способ «Криптовалюта», но приём подписок через него на сайте ещё не реализован. "
            "Используйте CloudPayments или ЮKassa либо Telegram Stars в боте."
        )
    elif pref == "yookassa" and not yk_browser:
        blocked_hint = (
            "Выбран основной способ «ЮKassa (веб-сайт)», но карточка выключена или не заполнены shopId и секрет."
        )
    elif pref == "cloudpayments" and not cp_ok:
        blocked_hint = "Выбран CloudPayments, но он выключен или не заполнен Public ID в настройках провайдера."
    elif pref == "yookassa_bot" and not (yk_web or yk_bot):
        blocked_hint = (
            "Выбрана ЮKassa (бот и Mini App), но провайдер выключен или не заданы shopId и секрет для редиректа "
            "и/или provider token для счёта в боте."
        )

    return {
        "kind": kind,
        "preference": pref,
        "cloudpayments_enabled": cp_ok,
        "cloudpayments_public_id": (cp.get("public_id") or "").strip() if cp_ok else "",
        "yookassa_web_pay_enabled": yk_web,
        "yookassa_bot_invoice_enabled": yk_bot,
        "yookassa_browser_redirect_ready": yk_browser,
        "yookassa_telegram_redirect_ready": yk_telegram_redirect,
        "yookassa_site_checkout_available": kind == "yookassa" and yk_web,
        "yookassa_bot_only": kind == "yookassa" and not yk_web and yk_bot,
        "offering_id_by_plan": offering_id_by_plan,
        "offerings": offerings,
        "telegram_stars_subscriptions_enabled": stars_on,
        "telegram_stars_per_rub": float(tsm.get("stars_per_rub") or 0.55),
        "telegram_stars_subscribe_url": telegram_stars_subscribe_deeplink() if stars_on else "",
        "checkout_blocked_hint": blocked_hint,
    }
