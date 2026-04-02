"""
Какой способ оплаты подписок активен для веба и для Telegram / Mini App.

Режим «единый» — один выбор для обоих каналов (поведение как раньше).
Режим «раздельно» — отдельные предпочтения: например CloudPayments на сайте и ЮKassa в боте.
Активация подписки после успешной оплаты общая (вебхуки CloudPayments, ЮKassa, Stars) — канал только выбирает, куда вести пользователя.

CloudPayments и ЮKassa (веб и бот/Mini App — разные карточки в админке) дают рабочую оплату.
Тинькофф и крипто — заготовки; kind будет none и показывается подсказка.
Telegram Stars (XTR) — в карточке провайдера; доступны параллельно, если включены.
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


def _normalize_checkout_mode(raw: str) -> str:
    m = (raw or "unified").strip().lower()
    return m if m in ("unified", "split") else "unified"


async def _load_checkout_json() -> dict[str, Any]:
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == _CHECKOUT_KEY)
        )
        if not row or not row.get("value"):
            return {}
        data = json.loads(row["value"])
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("_load_checkout_json failed", exc_info=True)
        return {}


def _norm_pref(p: str, valid: frozenset[str]) -> str:
    x = (p or "auto").strip().lower()
    return x if x in valid else "auto"


def _compute_subscription_kind(
    pref: str,
    *,
    cp_ok: bool,
    yk_browser: bool,
    yk_telegram_redirect: bool,
    yk_bot_invoice: bool,
) -> str:
    """Та же логика, что раньше для одного pref: cloudpayments | yookassa | none."""
    yk_redirect_any = bool(yk_browser or yk_telegram_redirect)

    def pick_auto() -> str:
        if cp_ok:
            return "cloudpayments"
        if yk_redirect_any or yk_bot_invoice:
            return "yookassa"
        return "none"

    p = (pref or "auto").strip().lower()
    if p == "auto":
        return pick_auto()
    if p == "cloudpayments":
        return "cloudpayments" if cp_ok else "none"
    if p == "yookassa_bot":
        return "yookassa" if (yk_redirect_any or yk_bot_invoice) else "none"
    if p in ("tinkoff", "crypto"):
        return "none"
    if p == "yookassa":
        return "yookassa" if yk_browser else "none"
    return pick_auto()


def _blocked_hint_for_pref(
    pref: str,
    *,
    cp_ok: bool,
    yk_browser: bool,
    yk_telegram_redirect: bool,
    yk_bot_invoice: bool,
    prefix: str = "",
) -> str | None:
    yk_redirect_any = bool(yk_browser or yk_telegram_redirect)
    p = (pref or "auto").strip().lower()
    msg: str | None = None
    if p == "tinkoff":
        msg = (
            "В админке выбран способ «Тинькофф Касса», но оплата подписок на сайте и в приложении "
            "для него ещё не подключена. Временно включите CloudPayments или настройте ЮKassa (веб и/или бот)."
        )
    elif p == "crypto":
        msg = (
            "Выбран способ «Криптовалюта», но приём подписок через него на сайте ещё не реализован. "
            "Используйте CloudPayments или ЮKassa либо Telegram Stars в боте."
        )
    elif p == "yookassa" and not yk_browser:
        msg = (
            "Выбрана ЮKassa (веб-сайт), но карточка выключена или не заполнены shopId и секрет для браузера."
        )
    elif p == "cloudpayments" and not cp_ok:
        msg = "Выбран CloudPayments, но он выключен или не заполнен Public ID в настройках провайдера."
    elif p == "yookassa_bot" and not (yk_redirect_any or yk_bot_invoice):
        msg = (
            "Выбрана ЮKassa (бот и Mini App), но провайдер выключен или не заданы shopId и секрет для редиректа "
            "и/или provider token для счёта в боте."
        )
    if not msg:
        return None
    return (prefix + msg) if prefix else msg


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


async def get_subscription_checkout_config() -> dict[str, Any]:
    """
    Режим unified — один выбор primary для веб и Telegram (как раньше).
    split — отдельно web_provider и telegram_provider (бот / Mini App).
    """
    valid = subscription_checkout_valid_preferences()
    data = await _load_checkout_json()
    primary = _norm_pref(str(data.get("primary_provider") or "auto"), valid)
    mode = _normalize_checkout_mode(str(data.get("checkout_mode") or "unified"))
    web_p = _norm_pref(str(data.get("web_provider") or primary), valid)
    tg_p = _norm_pref(str(data.get("telegram_provider") or primary), valid)
    if mode == "unified":
        web_p = primary
        tg_p = primary
    return {
        "checkout_mode": mode,
        "primary_provider": primary,
        "web_provider": web_p,
        "telegram_provider": tg_p,
    }


async def get_subscription_checkout_preference() -> str:
    """Для совместимости: основной провайдер (в режиме split — то, что в primary, для старых вызовов)."""
    cfg = await get_subscription_checkout_config()
    return str(cfg.get("primary_provider") or "auto")


async def save_subscription_checkout_bundle(
    checkout_mode: str,
    primary_provider: str,
    web_provider: str,
    telegram_provider: str,
) -> None:
    valid = subscription_checkout_valid_preferences()
    mode = _normalize_checkout_mode(checkout_mode)
    primary = _norm_pref(primary_provider, valid)
    web_p = _norm_pref(web_provider, valid)
    tg_p = _norm_pref(telegram_provider, valid)
    if mode == "unified":
        web_p = primary
        tg_p = primary
    payload = {
        "checkout_mode": mode,
        "primary_provider": primary,
        "web_provider": web_p,
        "telegram_provider": tg_p,
    }
    raw = json.dumps(payload, ensure_ascii=False)
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


async def save_subscription_checkout_preference(primary_provider: str) -> None:
    """Совместимость: только единый режим с одним провайдером."""
    p = (primary_provider or "auto").strip().lower()
    await save_subscription_checkout_bundle("unified", p, p, p)


async def resolve_active_subscription_checkout() -> dict[str, Any]:
    """
    kind / kind_web — для сайта и страниц в браузере.
    kind_telegram — для бота и Mini App (счёт в Telegram, сценарии «иди на сайт» при CloudPayments).
    Активация подписки после оплаты не зависит от канала: те же вебхуки и обработчики.
    """
    cfg = await get_subscription_checkout_config()
    mode = str(cfg.get("checkout_mode") or "unified")
    pref_web = str(cfg.get("web_provider") or "auto")
    pref_tg = str(cfg.get("telegram_provider") or "auto")
    primary = str(cfg.get("primary_provider") or "auto")

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

    kw = {
        "cp_ok": cp_ok,
        "yk_browser": yk_browser,
        "yk_telegram_redirect": yk_telegram_redirect,
        "yk_bot_invoice": yk_bot,
    }
    kind_web = _compute_subscription_kind(pref_web, **kw)
    kind_telegram = _compute_subscription_kind(pref_tg, **kw)

    tsm = await telegram_stars_subscription_meta()
    stars_on = bool(tsm.get("available_for_subscriptions"))

    hint_web = _blocked_hint_for_pref(pref_web, **kw, prefix="Веб: " if mode == "split" else "")
    hint_tg = _blocked_hint_for_pref(pref_tg, **kw, prefix="Telegram / Mini App: " if mode == "split" else "")
    blocked_hint: str | None = None
    if mode == "split":
        parts = [x for x in (hint_web, hint_tg) if x]
        blocked_hint = "\n\n".join(parts) if parts else None
    else:
        blocked_hint = _blocked_hint_for_pref(primary, **kw, prefix="")

    return {
        "checkout_mode": mode,
        "preference": primary,
        "preference_web": pref_web,
        "preference_telegram": pref_tg,
        "kind": kind_web,
        "kind_web": kind_web,
        "kind_telegram": kind_telegram,
        "cloudpayments_enabled": kind_web == "cloudpayments",
        "cloudpayments_public_id": (cp.get("public_id") or "").strip() if cp_ok else "",
        "yookassa_web_pay_enabled": yk_web,
        "yookassa_bot_invoice_enabled": yk_bot,
        "yookassa_browser_redirect_ready": yk_browser,
        "yookassa_telegram_redirect_ready": yk_telegram_redirect,
        "yookassa_site_checkout_available": kind_web == "yookassa" and yk_web,
        "yookassa_bot_only": kind_web == "yookassa" and not yk_web and yk_bot,
        "offering_id_by_plan": offering_id_by_plan,
        "offerings": offerings,
        "telegram_stars_subscriptions_enabled": stars_on,
        "telegram_stars_per_rub": float(tsm.get("stars_per_rub") or 0.55),
        "telegram_stars_subscribe_url": telegram_stars_subscribe_deeplink() if stars_on else "",
        "checkout_blocked_hint": blocked_hint,
        "checkout_blocked_hint_web": hint_web,
        "checkout_blocked_hint_telegram": hint_tg,
    }
