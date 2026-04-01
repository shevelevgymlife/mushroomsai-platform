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
    PLAN_KEYS,
    get_effective_plans,
    load_subscription_overrides_raw,
    save_subscription_overrides_raw,
)
from services.payment_provider_settings import (
    PAYMENT_PROVIDERS,
    get_provider_settings,
    merge_secrets,
    save_provider_settings,
)
from services.yookassa_bot_offerings import (
    get_merged_bot_offerings,
    normalize_offerings_list,
    parse_offerings_post,
)
from services.subscription_checkout import (
    get_subscription_checkout_preference,
    save_subscription_checkout_preference,
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


async def _parse_subscription_forms(form: Any) -> dict[str, Any]:
    """Собирает JSON переопределений тарифов из полей form name=plan_<key>_<field>."""
    raw: dict[str, Any] = {}
    for pk in PLAN_KEYS:
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
    checkout_pref = await get_subscription_checkout_preference()
    return templates.TemplateResponse(
        "dashboard/admin_payment_hub.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "providers": hub_providers,
            "cloudpayments_webhook_url": webhook_url,
            "subscription_checkout_pref": checkout_pref,
        },
    )


@router.post("/payment/subscription-checkout")
async def admin_subscription_checkout_save(request: Request):
    require_permission, _ = _lazy_admin()
    admin = await require_permission(request, "can_payment")
    if not admin:
        return RedirectResponse("/login")
    form = await request.form()
    raw = (form.get("primary_provider") or "auto").strip().lower()
    await save_subscription_checkout_preference(raw)
    return RedirectResponse("/admin/payment?checkout_saved=1", status_code=303)


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
    bot_offerings: list = []
    if meta["id"] == "yookassa_bot":
        try:
            bot_offerings = await get_merged_bot_offerings()
        except Exception:
            logger.exception("get_merged_bot_offerings failed")
            bot_offerings = []

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
            "cloudpayments_webhook_url": webhook_url if meta["id"] == "cloudpayments" else "",
            "yookassa_http_webhook_url": yk_wh if meta["id"] == "yookassa_bot" else "",
            "form_action": form_action,
            "bot_offerings": bot_offerings if meta["id"] == "yookassa_bot" else [],
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
        try:
            post_rows = parse_offerings_post(form)
            plans_eff = await get_effective_plans()
            new_st["offerings"] = normalize_offerings_list(post_rows, plans_eff)
        except Exception:
            logger.exception("yookassa_bot offerings parse failed")
        secrets = ("bot_token", "provider_token", "secret_key")
    elif pid == "telegram_stars":
        new_st["enabled"] = str(form.get("enabled") or "").strip().lower() in ("1", "true", "on", "yes")
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
        merged_sub = {**prev_sub}
        for pk, v in sub_raw.items():
            if isinstance(v, dict) and isinstance(prev_sub.get(pk), dict):
                merged_sub[pk] = {**prev_sub[pk], **v}
            else:
                merged_sub[pk] = v
        await save_subscription_overrides_raw(merged_sub)
    except Exception:
        logger.exception("save_subscription_overrides_raw failed")

    redir = (
        "/admin/payment/yookassa-bot?saved=1"
        if pid == "yookassa_bot"
        else f"/admin/payment/{quote(pid)}?saved=1"
    )
    return RedirectResponse(redir, status_code=303)
