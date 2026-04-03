"""Админка: раздел «Оплата» — провайдеры эквайринга и настройки тарифов."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from urllib.parse import quote

from config import settings
from web.templates_utils import Jinja2Templates
from services.payment_plans_catalog import (
    DEFAULT_PLANS,
    DRAWER_MENU_ITEM_SPECS,
    PLAN_ID_RE,
    PLAN_ORDER_KEY,
    extract_plan_order,
    get_effective_plans,
    load_subscription_overrides_raw,
    save_subscription_overrides_raw,
    visible_plan_keys_from,
)
from services.payment_provider_settings import (
    CLOUDPAYMENTS_WIDGET_METHOD_CHOICES,
    PAYMENT_PROVIDERS,
    get_provider_settings,
    merge_secrets,
    normalize_cloudpayments_restricted_payment_methods,
    save_provider_settings,
)
from services.yookassa_bot_offerings import get_merged_bot_offerings
from services.subscription_checkout import (
    get_subscription_checkout_config,
    save_subscription_checkout_bundle,
    subscription_checkout_select_rows,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


def _lazy_admin():
    from web.routes.admin import get_user_permissions, require_permission

    return require_permission, get_user_permissions


def _normalize_provider_key(raw: str) -> str:
    return raw.strip().lower().replace("-", "_")


def _provider_meta(pid: str) -> dict[str, str] | None:
    key = _normalize_provider_key(pid)
    for p in PAYMENT_PROVIDERS:
        if p["id"] == key:
            return p
    return None


def _merge_subscription_overrides(prev_sub: dict[str, Any], sub_raw: dict[str, Any]) -> dict[str, Any]:
    merged_sub = {**prev_sub}
    if PLAN_ORDER_KEY in sub_raw and isinstance(sub_raw[PLAN_ORDER_KEY], list):
        merged_sub[PLAN_ORDER_KEY] = list(sub_raw[PLAN_ORDER_KEY])
    for pk, v in sub_raw.items():
        if pk == PLAN_ORDER_KEY:
            continue
        if not isinstance(v, dict):
            merged_sub[pk] = v
            continue
        if isinstance(prev_sub.get(pk), dict):
            merged = {**prev_sub[pk], **v}
            if "drawer_features" in v and v.get("drawer_features") is None:
                merged.pop("drawer_features", None)
            merged_sub[pk] = merged
        else:
            merged_sub[pk] = v
    return merged_sub


async def _parse_subscription_forms(form: Any) -> dict[str, Any]:
    """Собирает JSON переопределений тарифов из полей form name=plan_<key>_<field> + catalog_plan_order."""
    raw: dict[str, Any] = {}
    prev = await load_subscription_overrides_raw()
    order_csv = (form.get("catalog_plan_order") or "").strip()
    if order_csv:
        order = [x.strip().lower() for x in order_csv.split(",") if x.strip()]
        order = [x for x in order if PLAN_ID_RE.match(x)]
    else:
        order = extract_plan_order(prev)
    new_slug = (form.get("catalog_new_plan_slug") or "").strip().lower()
    if new_slug and PLAN_ID_RE.match(new_slug) and new_slug not in order:
        order.append(new_slug)
    if order:
        raw[PLAN_ORDER_KEY] = order
    try:
        form_keys = set(form.keys())
    except Exception:
        form_keys = set()
    for pk in order:
        if not PLAN_ID_RE.match(pk):
            continue
        name = (form.get(f"plan_{pk}_name") or "").strip()
        desc = (form.get(f"plan_{pk}_description") or "").strip()
        feats = (form.get(f"plan_{pk}_features") or "").strip()
        pr = form.get(f"plan_{pk}_price")
        block: dict[str, Any] = {}
        if name:
            block["name"] = name
        if desc:
            block["description"] = desc
        if feats:
            block["features"] = [ln.strip() for ln in feats.splitlines() if ln.strip()]
        if f"plan_{pk}_clear_drawer" in form_keys and str(form.get(f"plan_{pk}_clear_drawer") or "").strip().lower() in (
            "1",
            "on",
            "true",
            "yes",
        ):
            block["drawer_features"] = None
        elif f"plan_{pk}_drawer_features" in form_keys:
            drawer = (form.get(f"plan_{pk}_drawer_features") or "").strip()
            if drawer:
                block["drawer_features"] = [ln.strip() for ln in drawer.splitlines() if ln.strip()]
        if f"plan_{pk}_show_in_catalog" in form_keys:
            vals = form.getlist(f"plan_{pk}_show_in_catalog")
            block["show_in_catalog"] = "1" in [str(x) for x in vals]
        if pk != "free":
            if f"plan_{pk}_billing_unlimited" in form_keys:
                uvals = form.getlist(f"plan_{pk}_billing_unlimited")
                block["billing_period_unlimited"] = "1" in [str(x) for x in uvals]
            if f"plan_{pk}_billing_unit" in form_keys:
                unit = (form.get(f"plan_{pk}_billing_unit") or "months").strip().lower()
                if unit in ("minutes", "days", "months", "years"):
                    block["billing_period_unit"] = unit
            if f"plan_{pk}_billing_value" in form_keys:
                try:
                    bv = int(str(form.get(f"plan_{pk}_billing_value") or "1").strip())
                    if bv >= 1:
                        block["billing_period_value"] = bv
                except ValueError:
                    pass
        if pk != "free" and pr is not None and str(pr).strip() != "":
            try:
                block["price"] = max(0, int(str(pr).strip()))
            except ValueError:
                pass
        if pk == "free" and pr is not None and str(pr).strip() != "":
            try:
                block["price"] = max(0, int(str(pr).strip()))
            except ValueError:
                pass
        if pk != "free" and f"plan_{pk}_access_tier" in form_keys:
            at = (form.get(f"plan_{pk}_access_tier") or "").strip().lower()
            if at in ("free", "start", "pro", "maxi"):
                block["access_tier"] = at
        if block:
            raw[pk] = block
    return raw


@router.get("/payment", response_class=HTMLResponse)
async def admin_payment_hub(request: Request):
    require_permission, get_user_permissions = _lazy_admin()
    admin = await require_permission(request, "can_payment")
    if not admin:
        return RedirectResponse("/login")
    perms = await get_user_permissions(admin)
    site = (settings.SITE_URL or "").rstrip("/")
    webhook_url = f"{site}/webhooks/cloudpayments" if site else "/webhooks/cloudpayments"
    hub_providers = []
    for p in PAYMENT_PROVIDERS:
        d = dict(p)
        d["href"] = p.get("admin_path") or f"/admin/payment/{p['id']}"
        hub_providers.append(d)
    checkout_cfg = await get_subscription_checkout_config()
    return templates.TemplateResponse(
        "dashboard/admin_payment_hub.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "providers": hub_providers,
            "cloudpayments_webhook_url": webhook_url,
            "subscription_checkout_config": checkout_cfg,
            "subscription_checkout_options": subscription_checkout_select_rows(),
        },
    )


@router.post("/payment/subscription-checkout")
async def admin_subscription_checkout_save(request: Request):
    require_permission, _ = _lazy_admin()
    admin = await require_permission(request, "can_payment")
    if not admin:
        return RedirectResponse("/login")
    form = await request.form()
    cfg = await get_subscription_checkout_config()
    mode = (form.get("checkout_mode") or "unified").strip().lower()
    primary = (form.get("primary_provider") or "auto").strip().lower()
    web_p = (form.get("web_provider") or primary).strip().lower()
    tg_p = (form.get("telegram_provider") or primary).strip().lower()
    if "telegram_enabled" in form:
        tg_enabled = str(form.get("telegram_enabled") or "").strip().lower() in (
            "1",
            "true",
            "on",
            "yes",
        )
    else:
        tg_enabled = bool(cfg.get("telegram_payments_enabled", True))
    await save_subscription_checkout_bundle(
        mode,
        primary,
        web_p,
        tg_p,
        telegram_payments_enabled=tg_enabled,
    )
    return RedirectResponse("/admin/payment?checkout_saved=1", status_code=303)


@router.post("/payment/subscription-checkout-bot")
async def admin_subscription_checkout_bot_save(request: Request):
    require_permission, _ = _lazy_admin()
    admin = await require_permission(request, "can_payment")
    if not admin:
        return RedirectResponse("/login")
    form = await request.form()
    cfg = await get_subscription_checkout_config()
    mode = str(cfg.get("checkout_mode") or "unified")
    primary = str(cfg.get("primary_provider") or "auto")
    web_p = str(cfg.get("web_provider") or primary)
    tg_p = (form.get("bot_provider") or cfg.get("telegram_provider") or primary).strip().lower()
    tg_enabled = str(form.get("bot_payments_enabled") or "").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
    await save_subscription_checkout_bundle(
        mode,
        primary,
        web_p,
        tg_p,
        telegram_payments_enabled=tg_enabled,
    )
    return RedirectResponse("/admin/payment?checkout_saved=1", status_code=303)


@router.get("/payment/subscription-plans", response_class=HTMLResponse)
async def admin_subscription_plans_page(request: Request):
    require_permission, get_user_permissions = _lazy_admin()
    admin = await require_permission(request, "can_payment")
    if not admin:
        return RedirectResponse("/login")
    perms = await get_user_permissions(admin)
    plans = await get_effective_plans()
    raw_over = await load_subscription_overrides_raw()
    pkv = visible_plan_keys_from(plans)
    plan_order_list = list(plans.keys())
    return templates.TemplateResponse(
        "dashboard/admin_subscription_plans.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "plans": plans,
            "defaults": DEFAULT_PLANS,
            "raw_overrides": raw_over,
            "plan_keys_visible": pkv,
            "plan_order_list": plan_order_list,
            "drawer_menu_specs": DRAWER_MENU_ITEM_SPECS,
        },
    )


def _parse_drawer_menu_post(form: Any) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for item_id, _ in DRAWER_MENU_ITEM_SPECS:
        vals = form.getlist(f"dm_{item_id}")
        out[item_id] = "1" in [str(x) for x in vals]
    return out


@router.post("/payment/subscription-plans/drawer/{plan_key}")
async def admin_subscription_plans_drawer_save(request: Request, plan_key: str):
    require_permission, _ = _lazy_admin()
    admin = await require_permission(request, "can_payment")
    if not admin:
        return RedirectResponse("/login")
    pk = (plan_key or "").strip().lower()
    eff = await get_effective_plans()
    if pk not in eff:
        return RedirectResponse("/admin/payment/subscription-plans", status_code=303)
    form = await request.form()
    try:
        menu = _parse_drawer_menu_post(form)
        prev_sub = await load_subscription_overrides_raw()
        block = dict(prev_sub.get(pk) or {})
        block["drawer_menu"] = menu
        prev_sub[pk] = block
        await save_subscription_overrides_raw(prev_sub)
    except Exception:
        logger.exception("admin_subscription_plans_drawer_save failed pk=%s", pk)
    return RedirectResponse(f"/admin/payment/subscription-plans?drawer_saved={quote(pk)}", status_code=303)


@router.post("/payment/subscription-plans")
async def admin_subscription_plans_save(request: Request):
    require_permission, _ = _lazy_admin()
    admin = await require_permission(request, "can_payment")
    if not admin:
        return RedirectResponse("/login")
    form = await request.form()
    try:
        sub_raw = await _parse_subscription_forms(form)
        prev_sub = await load_subscription_overrides_raw()
        merged_sub = _merge_subscription_overrides(prev_sub, sub_raw)
        rm = (form.get("catalog_remove_plan_slug") or "").strip().lower()
        if rm and rm != "free" and PLAN_ID_RE.match(rm):
            po = list(merged_sub.get(PLAN_ORDER_KEY) or extract_plan_order(merged_sub))
            merged_sub[PLAN_ORDER_KEY] = [x for x in po if x != rm]
            merged_sub.pop(rm, None)
        await save_subscription_overrides_raw(merged_sub)
    except Exception:
        logger.exception("admin_subscription_plans_save failed")
    return RedirectResponse("/admin/payment/subscription-plans?saved=1", status_code=303)


@router.get("/payment/{provider_id}", response_class=HTMLResponse)
async def admin_payment_provider_page(request: Request, provider_id: str):
    require_permission, get_user_permissions = _lazy_admin()
    admin = await require_permission(request, "can_payment")
    if not admin:
        return RedirectResponse("/login")
    meta = _provider_meta(provider_id)
    if not meta:
        return RedirectResponse("/admin/payment", status_code=303)
    perms = await get_user_permissions(admin)
    st = await get_provider_settings(meta["id"])
    plans = await get_effective_plans()
    raw_over = await load_subscription_overrides_raw()
    site = (settings.SITE_URL or "").rstrip("/")
    webhook_url = f"{site}/webhooks/cloudpayments" if site else "/webhooks/cloudpayments"
    yk_wh = f"{site}/webhooks/yookassa" if site else "/webhooks/yookassa"
    form_action = (
        "/admin/payment/yookassa-bot"
        if meta["id"] == "yookassa_bot"
        else f"/admin/payment/{provider_id.strip().replace(' ', '')}"
    )
    bot_offerings_preview: list = []
    if meta["id"] == "yookassa_bot":
        try:
            bot_offerings_preview = await get_merged_bot_offerings()
        except Exception:
            logger.exception("get_merged_bot_offerings failed")
            bot_offerings_preview = []

    return templates.TemplateResponse(
        "dashboard/admin_payment_provider.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "provider": meta,
            "provider_cfg": st,
            "plans": plans,
            "defaults": DEFAULT_PLANS,
            "raw_overrides": raw_over,
            "plan_order_list": list(plans.keys()),
            "cloudpayments_webhook_url": webhook_url if meta["id"] == "cloudpayments" else "",
            "cloudpayments_method_choices": (
                CLOUDPAYMENTS_WIDGET_METHOD_CHOICES if meta["id"] == "cloudpayments" else []
            ),
            "cloudpayments_restricted_current": (
                normalize_cloudpayments_restricted_payment_methods(st.get("restricted_payment_methods"))
                if meta["id"] == "cloudpayments"
                else []
            ),
            "yookassa_http_webhook_url": yk_wh if meta["id"] in ("yookassa_bot", "yookassa") else "",
            "form_action": form_action,
            "bot_offerings_preview": bot_offerings_preview if meta["id"] == "yookassa_bot" else [],
            "drawer_menu_specs": DRAWER_MENU_ITEM_SPECS,
        },
    )


@router.post("/payment/{provider_id}", response_class=HTMLResponse)
async def admin_payment_provider_save(request: Request, provider_id: str):
    require_permission, _ = _lazy_admin()
    admin = await require_permission(request, "can_payment")
    if not admin:
        return RedirectResponse("/login")
    meta = _provider_meta(provider_id)
    if not meta:
        return RedirectResponse("/admin/payment", status_code=303)
    pid = meta["id"]
    form = await request.form()
    prev = await get_provider_settings(pid)
    new_st: dict[str, Any] = {}

    if pid == "cloudpayments":
        new_st["enabled"] = str(form.get("enabled") or "").strip().lower() in ("1", "true", "on", "yes")
        new_st["public_id"] = (form.get("public_id") or "").strip()
        new_st["api_secret"] = (form.get("api_secret") or "").strip()
        try:
            raw_restrict = form.getlist("cp_restrict_methods")
        except Exception:
            raw_restrict = []
        new_st["restricted_payment_methods"] = normalize_cloudpayments_restricted_payment_methods(
            raw_restrict
        )
        secrets = ("api_secret",)
    elif pid == "tinkoff":
        new_st["enabled"] = str(form.get("enabled") or "").strip().lower() in ("1", "true", "on", "yes")
        new_st["terminal_key"] = (form.get("terminal_key") or "").strip()
        new_st["password"] = (form.get("password") or "").strip()
        new_st["note"] = (form.get("note") or "").strip()
        secrets = ("password",)
    elif pid == "yookassa":
        new_st["enabled"] = str(form.get("enabled") or "").strip().lower() in ("1", "true", "on", "yes")
        new_st["shop_id"] = (form.get("shop_id") or "").strip()
        new_st["secret_key"] = (form.get("secret_key") or "").strip()
        secrets = ("secret_key",)
    elif pid == "yookassa_bot":
        new_st["enabled"] = str(form.get("enabled") or "").strip().lower() in ("1", "true", "on", "yes")
        new_st["bot_token"] = (form.get("bot_token") or "").strip()
        new_st["provider_token"] = (form.get("provider_token") or "").strip()
        new_st["shop_id"] = (form.get("shop_id") or "").strip()
        new_st["secret_key"] = (form.get("secret_key") or "").strip()
        new_st["instructions"] = (form.get("instructions") or "").strip()
        raw_trv = (form.get("telegram_receipt_vat_code") or "").strip()
        if raw_trv == "":
            new_st["telegram_receipt_vat_code"] = -1
        else:
            try:
                new_st["telegram_receipt_vat_code"] = max(-1, min(10, int(float(raw_trv))))
            except ValueError:
                pass
        secrets = ("bot_token", "provider_token", "secret_key")
    elif pid == "telegram_stars":
        new_st["enabled"] = str(form.get("enabled") or "").strip().lower() in ("1", "true", "on", "yes")
        new_st["offer_subscriptions"] = str(form.get("offer_subscriptions") or "").strip().lower() in (
            "1",
            "true",
            "on",
            "yes",
        )
        raw_spr = (form.get("stars_per_rub") or "").strip().replace(",", ".")
        try:
            v = float(raw_spr) if raw_spr else 0.0
        except ValueError:
            v = 0.0
        if v <= 0:
            v = 0.55
        new_st["stars_per_rub"] = max(0.01, min(5000.0, v))
        new_st["note"] = (form.get("note") or "").strip()
        secrets = ()
    elif pid == "crypto":
        new_st["enabled"] = str(form.get("enabled") or "").strip().lower() in ("1", "true", "on", "yes")
        new_st["api_key"] = (form.get("api_key") or "").strip()
        new_st["payout_currency"] = (form.get("payout_currency") or "").strip()
        new_st["note"] = (form.get("note") or "").strip()
        secrets = ("api_key",)
    else:
        secrets = ()

    merged = {**prev, **new_st}
    merged = merge_secrets(merged, prev, secrets)

    try:
        await save_provider_settings(pid, merged)
    except Exception:
        logger.exception("save_provider_settings failed pid=%s", pid)

    try:
        sub_raw = await _parse_subscription_forms(form)
        prev_sub = await load_subscription_overrides_raw()
        merged_sub = _merge_subscription_overrides(prev_sub, sub_raw)
        rm = (form.get("catalog_remove_plan_slug") or "").strip().lower()
        if rm and rm != "free" and PLAN_ID_RE.match(rm):
            po = list(merged_sub.get(PLAN_ORDER_KEY) or extract_plan_order(merged_sub))
            merged_sub[PLAN_ORDER_KEY] = [x for x in po if x != rm]
            merged_sub.pop(rm, None)
        await save_subscription_overrides_raw(merged_sub)
    except Exception:
        logger.exception("save_subscription_overrides_raw failed")

    redir = (
        "/admin/payment/yookassa-bot?saved=1"
        if pid == "yookassa_bot"
        else f"/admin/payment/{quote(pid)}?saved=1"
    )
    return RedirectResponse(redir, status_code=303)
