import json
import os
import uuid
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import quote
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from web.templates_utils import Jinja2Templates
from auth.session import create_access_token, get_user_from_request
from auth.telegram_auth import verify_telegram_miniapp
from auth.ui_prefs import attach_screen_rim_prefs
from db.database import database
from db.models import (
    users, yookassa_return_tokens, messages, orders, posts, post_likes, community_posts, community_likes, community_comments,
    community_folders, community_follows, community_saved, community_reposts, profile_likes, community_profiles,
    direct_messages,
    dashboard_blocks, user_block_overrides, community_groups, community_group_members, community_group_messages,
    community_group_message_likes,
    community_group_join_requests,
    community_group_member_permissions,
    community_group_member_bans,
    community_group_typing_status,
    admin_permissions,
    homepage_blocks,
)
from services.referral_service import get_referral_stats
from services.payment_plans_catalog import (
    get_effective_plans,
    is_catalog_paid_checkout_plan,
    visible_plan_keys_from,
    cloudpayments_checkout_payload,
    plan_billing_captions_for_keys,
)
from services.payment_provider_settings import get_provider_settings
from services.yookassa_bot_offerings import (
    find_offering_id_for_plan,
    get_merged_bot_offerings,
    offering_by_id,
    yookassa_redirect_api_ready,
)
from services.yookassa_pay_channel import detect_yookassa_pay_channel
from services.subscription_checkout import resolve_active_subscription_checkout
from services.yookassa_credentials import resolve_yookassa_shop_credentials
from services.yookassa_hosted_payment import (
    create_yookassa_redirect_payment,
    resolve_yookassa_redirect_receipt_vat,
)
from services.yookassa_pay_service import (
    apply_yookassa_payment_succeeded,
    fetch_yookassa_payment_with_fallback,
)
from services.subscription_service import (
    activate_subscription,
    check_subscription,
    web_default_home_path,
    can_ask_question,
    increment_question_count,
    record_subscription_event,
    fetch_subscription_history_display,
    notify_subscription_manual_free,
    FREE_AI_LIMIT_MESSAGE,
    FREE_AI_UPGRADE_INLINE,
)
from ai.openai_client import chat_with_ai
from services.plan_access import plan_allowed_block_keys, is_platform_operator, can_use_community_group_chats
from services.legacy_dm_chat_sync import sync_direct_messages_pair
from services.in_app_notifications import (
    create_notification,
    should_send_telegram,
    should_send_telegram_for_event,
    count_unread_events,
    mark_events_notifications_read,
)
from services.messenger_unread import count_chat_unread, count_standalone_direct_unread
from services.event_notify import (
    extract_mentioned_numeric_ids,
    send_event_telegram_html,
    user_exists,
)
from services.community_group_queries import fetch_community_group_row
from services.legal import legal_acceptance_redirect
from services.notify_admin import notify_admin_telegram
from services.ops_alerts import notify_plan_upgrade_request
from services.community_post_publish import publish_community_post
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
import secrets
import traceback as _traceback
import logging
from decimal import Decimal

from config import settings

_logger = logging.getLogger(__name__)

# После оплаты на ЮKassa: если вебхук не пришёл, активируем подписку по возврату на сайт (см. subscriptions_page).
_YK_PENDING_CHECKOUT_COOKIE = "yk_pending_checkout"

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


async def _persist_yookassa_return_token(*, token: str, payment_id: str, user_id: int) -> None:
    """Связка токена в URL возврата с payment_id (в TG Mini App cookie часто не сохраняется)."""
    try:
        await database.execute(
            yookassa_return_tokens.insert().values(
                token=(token or "")[:128],
                payment_id=(payment_id or "")[:128],
                user_id=int(user_id),
                expires_at=datetime.utcnow() + timedelta(hours=4),
            )
        )
    except Exception as e:
        _logger.warning("yookassa return token persist failed: %s", e)


async def _create_yookassa_redirect_for_channel(
    *,
    channel: str,
    amount_rub: float,
    description: str,
    return_url: str,
    metadata: dict[str, str],
    customer_email: str | None,
    customer_phone: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Браузер → payment_provider:yookassa (второй магазин).
    Telegram / Mini App → payment_provider:yookassa_bot (первый магазин).
    """
    yk = await get_provider_settings("yookassa")
    yb = await get_provider_settings("yookassa_bot")
    if (channel or "").strip().lower() == "telegram_embedded":
        if not yookassa_redirect_api_ready(yb):
            return None, "tg_shop_not_configured", None
        sid, sec = resolve_yookassa_shop_credentials(yb)
        receipt_vat = resolve_yookassa_redirect_receipt_vat("yookassa_bot", yb)
    else:
        if not yookassa_redirect_api_ready(yk):
            return None, "web_shop_not_configured", None
        sid, sec = resolve_yookassa_shop_credentials(yk)
        receipt_vat = resolve_yookassa_redirect_receipt_vat("yookassa", yk)
    return await create_yookassa_redirect_payment(
        shop_id=sid,
        secret_key=sec,
        amount_rub=amount_rub,
        description=description,
        return_url=return_url,
        metadata=metadata,
        customer_email=customer_email,
        customer_phone=customer_phone,
        receipt_vat_code=receipt_vat,
    )


async def compute_visible_blocks(user_id: int, plan: str) -> list[str]:
    """Return list of block_keys visible for this user, respecting global settings and per-user overrides."""
    blocks_raw = await database.fetch_all(
        dashboard_blocks.select().order_by(dashboard_blocks.c.position, dashboard_blocks.c.id)
    )
    overrides_raw = await database.fetch_all(
        user_block_overrides.select().where(user_block_overrides.c.user_id == user_id)
    )
    overrides = {r["block_key"]: r for r in overrides_raw}

    TIER_ORDER = ("free", "start", "pro", "maxi")
    eff = await get_effective_plans()
    pk = (plan or "free").lower()
    tier = str((eff.get(pk) or {}).get("access_tier") or pk).lower()
    if tier in TIER_ORDER:
        plan_idx = TIER_ORDER.index(tier)
    else:
        plan_idx = 1 if pk != "free" else 0

    visible = []
    for b in blocks_raw:
        key = b["block_key"]
        ov = overrides.get(key)

        # Per-user override: if explicitly set, respect it
        if ov and ov["is_visible"] is not None:
            if ov["is_visible"]:
                visible.append(key)
            continue

        # Global visibility
        if not b["is_visible"]:
            continue

        # Access level check
        al = b["access_level"] or "all"
        if al == "all":
            visible.append(key)
        elif al == "auth":
            visible.append(key)
        elif al == "start" and plan_idx >= 1:
            visible.append(key)
        elif al == "pro" and plan_idx >= 2:
            visible.append(key)
        elif al == "maxi" and plan_idx >= 3:
            visible.append(key)

    try:
        from services.internal_exchange_settings import is_internal_exchange_enabled

        if not await is_internal_exchange_enabled():
            visible = [k for k in visible if k != "internal_exchange"]
    except Exception:
        pass

    return visible


def build_dashboard_secs(visible_block_keys: list[str]) -> list[str]:
    keys = set(visible_block_keys)
    # Единый каркас: лента, группы; профиль — только /community/profile/{id} (одна страница на пользователя)
    out = ["feed", "groups", "search"]
    if "messages" in keys:
        out.append("messages")
    if "ai_chat" in keys:
        out.append("ai")
    if "knowledge_base" in keys:
        out.append("knowledge")
    if "pro_pin_info" in keys:
        out.append("proextras")
    if "shop" in keys:
        out.extend(["shop", "orders"])
    if "tariffs" in keys:
        out.append("plan")
    if "referral" in keys:
        out.append("referral")
    if "seller_marketplace" in keys:
        out.append("seller")
    out.extend(["link", "language", "profile"])
    return out


async def require_auth(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return None
    return user


async def _fetch_homepage_blocks_for_pricing() -> dict:
    try:
        blocks_raw = await database.fetch_all(
            homepage_blocks.select()
            .where(homepage_blocks.c.is_visible == True)
            .order_by(homepage_blocks.c.position, homepage_blocks.c.id)
        )
        blocks = {r["block_name"]: dict(r) for r in blocks_raw}
        for b in blocks.values():
            if b.get("custom_title"):
                b["title"] = b["custom_title"]
        return blocks
    except Exception:
        return {}


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login?next=/subscriptions")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    blocks = await _fetch_homepage_blocks_for_pricing()
    plans_eff = await get_effective_plans()
    plan_keys_visible = visible_plan_keys_from(plans_eff)
    uid = int(user.get("primary_user_id") or user["id"])
    subscription_checkout = await resolve_active_subscription_checkout()
    ck = subscription_checkout.get("kind") or "none"
    cp_enabled = ck == "cloudpayments"
    cp_public = (subscription_checkout.get("cloudpayments_public_id") or "").strip() if cp_enabled else ""
    yk_web = bool(subscription_checkout.get("yookassa_site_checkout_available"))
    offering_id_by_plan = subscription_checkout.get("offering_id_by_plan") or {}
    bot_u = (getattr(settings, "TELEGRAM_BOT_USERNAME", None) or "neuro_fungi_bot").strip().lstrip("@")
    telegram_bot_url = f"https://t.me/{bot_u}"
    cp_plan_payload = cloudpayments_checkout_payload(plans_eff, plan_keys_visible)
    plan_billing_captions = plan_billing_captions_for_keys(plans_eff, plan_keys_visible)

    response = templates.TemplateResponse(
        "subscriptions.html",
        {
            "request": request,
            "user": user,
            "blocks": blocks,
            "error": None,
            "plans": plans_eff,
            "plan_keys_visible": plan_keys_visible,
            "cloudpayments_enabled": cp_enabled,
            "cloudpayments_public_id": cp_public,
            "payment_user_id": uid,
            "yookassa_web_pay_enabled": yk_web,
            "offering_id_by_plan": offering_id_by_plan,
            "subscription_checkout": subscription_checkout,
            "telegram_bot_url": telegram_bot_url,
            "cp_plan_payload": cp_plan_payload,
            "plan_billing_captions": plan_billing_captions,
        },
    )

    # Возврат с ЮKassa: вебхук мог не дойти; payment_id — из cookie или из yk_rt (Mini App / WebView без cookie).
    if user and request.query_params.get("paid") == "1":
        uid_pay = int(user.get("primary_user_id") or user["id"])
        pid = (request.cookies.get(_YK_PENDING_CHECKOUT_COOKIE) or "").strip()
        yk_rt = (request.query_params.get("yk_rt") or "").strip()
        if not pid and yk_rt:
            row = await database.fetch_one(
                yookassa_return_tokens.select()
                .where(yookassa_return_tokens.c.token == yk_rt[:128])
                .where(yookassa_return_tokens.c.user_id == uid_pay)
                .where(yookassa_return_tokens.c.expires_at > datetime.utcnow())
            )
            if row:
                pid = (row.get("payment_id") or "").strip()
        if pid:
            pay = await fetch_yookassa_payment_with_fallback(pid)
            if pay:
                stp = (pay.get("status") or "").strip().lower()
                if stp == "succeeded":
                    ok, msg = await apply_yookassa_payment_succeeded(pay)
                    if ok or msg == "duplicate":
                        _logger.info("yookassa paid=1 fallback ok payment_id=%s msg=%s", pid, msg)
                    else:
                        _logger.warning("yookassa paid=1 fallback payment_id=%s msg=%s", pid, msg)
                    response.delete_cookie(_YK_PENDING_CHECKOUT_COOKIE, path="/")
                    if yk_rt:
                        try:
                            await database.execute(
                                yookassa_return_tokens.delete().where(
                                    yookassa_return_tokens.c.token == yk_rt[:128]
                                )
                            )
                        except Exception:
                            pass
                elif stp in ("canceled", "cancelled", "rejected"):
                    response.delete_cookie(_YK_PENDING_CHECKOUT_COOKIE, path="/")

    return response


@router.get("/pay/subscription")
async def pay_subscription_yookassa(request: Request, offering_id: str = "", plan: str = ""):
    """Старт оплаты подписки: CloudPayments или ЮKassa по активным настройкам канала."""
    user = await require_auth(request)
    oid = (offering_id or plan or "").strip().lower()
    if not user:
        init_raw = (request.query_params.get("tg_init_data") or "").strip()
        if init_raw:
            try:
                udata = verify_telegram_miniapp(init_raw)
                tg_id = int(udata.get("id"))
                row = await database.fetch_one(
                    users.select().where(
                        sa.or_(users.c.tg_id == tg_id, users.c.linked_tg_id == tg_id)
                    )
                )
                if row:
                    root_id = int(row.get("primary_user_id") or row["id"])
                    token = create_access_token(root_id)
                    nxt = f"/pay/subscription?plan={quote(oid, safe='')}&pay_ctx=tg"
                    resp = RedirectResponse(nxt, status_code=302)
                    resp.set_cookie(
                        "access_token",
                        token,
                        httponly=True,
                        max_age=30 * 24 * 3600,
                    )
                    return resp
            except Exception:
                _logger.warning("pay/subscription tg_init_data auth failed", exc_info=True)
    if not user:
        nxt = "/pay/subscription?plan=" + quote(oid, safe="")
        return RedirectResponse("/login?next=" + quote(nxt, safe=""), status_code=302)
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    if not oid:
        return RedirectResponse("/subscriptions?pay_error=no_offering", status_code=302)
    checkout = await resolve_active_subscription_checkout()
    channel = detect_yookassa_pay_channel(request)
    kind = checkout.get("kind_telegram") if channel == "telegram_embedded" else checkout.get("kind")
    kind = (kind or "none").strip().lower()
    if kind == "none":
        return RedirectResponse("/subscriptions?pay_error=no_payment", status_code=302)
    offerings = await get_merged_bot_offerings()
    off = offering_by_id(offerings, oid)
    if not off or not off.get("enabled"):
        return RedirectResponse("/subscriptions?pay_error=bad_offering", status_code=302)
    try:
        price = float(off.get("price_rub") or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return RedirectResponse("/subscriptions?pay_error=price", status_code=302)
    if kind == "cloudpayments":
        cpid = (checkout.get("cloudpayments_public_id") or "").strip()
        if not cpid:
            return RedirectResponse("/subscriptions?pay_error=no_payment", status_code=302)
        uid = int(user.get("primary_user_id") or user["id"])
        return templates.TemplateResponse(
            "pay_cloudpayments.html",
            {
                "request": request,
                "user": user,
                "pay_plan": oid,
                "pay_plan_name": (off.get("display_name") or oid),
                "pay_amount": int(price),
                "payment_user_id": uid,
                "cloudpayments_public_id": cpid,
                "cloudpayments_restricted_payment_methods": checkout.get(
                    "cloudpayments_restricted_payment_methods"
                )
                or [],
            },
        )
    if kind != "yookassa":
        return RedirectResponse("/subscriptions?pay_error=no_payment", status_code=302)
    yk = await get_provider_settings("yookassa")
    yb = await get_provider_settings("yookassa_bot")
    if channel == "telegram_embedded":
        if not yookassa_redirect_api_ready(yb):
            return RedirectResponse("/subscriptions?pay_error=tg_shop", status_code=302)
    elif not yookassa_redirect_api_ready(yk):
        return RedirectResponse("/subscriptions?pay_error=web_shop", status_code=302)
    uid = int(user.get("primary_user_id") or user["id"])
    site = (settings.SITE_URL or "").rstrip("/")
    if not site.startswith("http"):
        return RedirectResponse("/subscriptions?pay_error=no_site_url", status_code=302)
    yk_rt = secrets.token_urlsafe(32)
    return_url = f"{site}/subscriptions?paid=1&yk_rt={quote(yk_rt, safe='')}"
    disp = (off.get("display_name") or oid)[:80]
    dur = (off.get("duration_label") or "")[:40]
    desc = f"Подписка «{disp}» {dur}".strip()[:128]
    cust_email = (user.get("email") or "").strip() or None
    cust_phone = (user.get("phone") or "").strip() or None
    url, err, payment_id = await _create_yookassa_redirect_for_channel(
        channel=channel,
        amount_rub=price,
        description=desc,
        return_url=return_url,
        metadata={
            "user_id": str(uid),
            "offering_id": oid,
            "plan": oid,
        },
        customer_email=cust_email,
        customer_phone=cust_phone,
    )
    if not url:
        _logger.warning("yookassa create redirect failed: %s", err)
        return RedirectResponse(
            "/subscriptions?pay_error=create&pay_error_detail=" + quote((err or "unknown")[:260], safe=""),
            status_code=302,
        )
    out = RedirectResponse(url, status_code=302)
    if payment_id:
        out.set_cookie(
            _YK_PENDING_CHECKOUT_COOKIE,
            payment_id,
            max_age=3600,
            httponly=True,
            samesite="lax",
            secure=(settings.SITE_URL or "").lower().startswith("https"),
            path="/",
        )
        await _persist_yookassa_return_token(token=yk_rt, payment_id=payment_id, user_id=uid)
    return out


@router.get("/pay/gift")
async def pay_gift_subscription(request: Request, plan: str = "", recipient_id: str = ""):
    """Оплата подарка подписки — тот же активный провайдер, что и для своей подписки."""
    user = await require_auth(request)
    if not user:
        nxt = f"/pay/gift?plan={quote((plan or '').strip(), safe='')}&recipient_id={quote(str(recipient_id or '').strip(), safe='')}"
        return RedirectResponse("/login?next=" + quote(nxt, safe=""), status_code=302)
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    plan_key = (plan or "").strip().lower()
    try:
        rid = int(str(recipient_id).strip())
    except (TypeError, ValueError):
        return RedirectResponse("/subscriptions?gift_error=invalid", status_code=302)
    giver_id = int(user.get("primary_user_id") or user["id"])
    plans_eff = await get_effective_plans()
    if not is_catalog_paid_checkout_plan(plans_eff, plan_key):
        return RedirectResponse("/subscriptions?gift_error=invalid", status_code=302)
    if rid == giver_id:
        return RedirectResponse("/subscriptions?gift_error=self", status_code=302)

    checkout = await resolve_active_subscription_checkout()
    kind = checkout.get("kind") or "none"
    price = float((plans_eff.get(plan_key) or {}).get("price") or 0)
    pname = (plans_eff.get(plan_key) or {}).get("name") or plan_key

    if kind == "none":
        return RedirectResponse("/subscriptions?gift_error=no_payment", status_code=302)

    if kind == "cloudpayments":
        cpid = (checkout.get("cloudpayments_public_id") or "").strip()
        if not cpid or price <= 0:
            return RedirectResponse("/subscriptions?gift_error=no_payment", status_code=302)
        return templates.TemplateResponse(
            "gift_pay_cloudpayments.html",
            {
                "request": request,
                "user": user,
                "gift_plan": plan_key,
                "gift_plan_name": pname,
                "gift_amount": int(price),
                "gift_recipient_id": rid,
                "gift_giver_id": giver_id,
                "cloudpayments_public_id": cpid,
                "cloudpayments_restricted_payment_methods": checkout.get(
                    "cloudpayments_restricted_payment_methods"
                )
                or [],
            },
        )

    if kind == "yookassa":
        if not checkout.get("yookassa_site_checkout_available"):
            return RedirectResponse("/subscriptions?gift_error=use_bot", status_code=302)
        channel = detect_yookassa_pay_channel(request)
        yk = await get_provider_settings("yookassa")
        yb = await get_provider_settings("yookassa_bot")
        if channel == "telegram_embedded":
            if not yookassa_redirect_api_ready(yb):
                return RedirectResponse("/subscriptions?gift_error=tg_shop", status_code=302)
        elif not yookassa_redirect_api_ready(yk):
            return RedirectResponse("/subscriptions?gift_error=web_shop", status_code=302)
        offerings = await get_merged_bot_offerings()
        oid = find_offering_id_for_plan(offerings, plan_key)
        off = offering_by_id(offerings, oid) if oid else None
        if not off or not off.get("enabled"):
            return RedirectResponse("/subscriptions?gift_error=bad_offering", status_code=302)
        try:
            price_rub = float(off.get("price_rub") or 0)
        except (TypeError, ValueError):
            price_rub = 0.0
        if price_rub <= 0:
            return RedirectResponse("/subscriptions?gift_error=price", status_code=302)
        site = (settings.SITE_URL or "").rstrip("/")
        if not site.startswith("http"):
            return RedirectResponse("/subscriptions?gift_error=no_site_url", status_code=302)
        yk_rt = secrets.token_urlsafe(32)
        return_url = f"{site}/subscriptions?paid=1&gifted=1&yk_rt={quote(yk_rt, safe='')}"
        disp = (off.get("display_name") or oid)[:80]
        dur = (off.get("duration_label") or "")[:40]
        desc = f"Подарок подписки «{disp}» {dur}".strip()[:128]
        cust_email = (user.get("email") or "").strip() or None
        cust_phone = (user.get("phone") or "").strip() or None
        url, err, payment_id = await _create_yookassa_redirect_for_channel(
            channel=channel,
            amount_rub=price_rub,
            description=desc,
            return_url=return_url,
            metadata={
                "user_id": str(giver_id),
                "gift": "1",
                "giver_id": str(giver_id),
                "recipient_id": str(rid),
                "offering_id": str(oid),
                "plan": str(plan_key),
            },
            customer_email=cust_email,
            customer_phone=cust_phone,
        )
        if not url:
            _logger.warning("yookassa gift create redirect failed: %s", err)
            return RedirectResponse(
                "/subscriptions?gift_error=create&gift_error_detail=" + quote((err or "unknown")[:260], safe=""),
                status_code=302,
            )
        out = RedirectResponse(url, status_code=302)
        if payment_id:
            out.set_cookie(
                _YK_PENDING_CHECKOUT_COOKIE,
                payment_id,
                max_age=3600,
                httponly=True,
                samesite="lax",
                secure=(settings.SITE_URL or "").lower().startswith("https"),
                path="/",
            )
            await _persist_yookassa_return_token(token=yk_rt, payment_id=payment_id, user_id=giver_id)
        return out

    return RedirectResponse("/subscriptions?gift_error=no_payment", status_code=302)


@router.post("/subscriptions/connect")
async def subscriptions_connect(request: Request, plan: str = Form(...)):
    """Только бесплатный тариф без оплаты; платные — через эквайринг."""
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login?next=/subscriptions", status_code=302)
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    uid = int(user.get("primary_user_id") or user["id"])
    plan_key = (plan or "").strip().lower()
    plans_eff = await get_effective_plans()
    if plan_key not in plans_eff:
        return RedirectResponse("/subscriptions", status_code=302)
    if plan_key != "free":
        return RedirectResponse("/subscriptions?need_payment=1", status_code=302)
    if plan_key == "free":
        urow = await database.fetch_one(users.select().where(users.c.id == uid))
        prev = (urow.get("subscription_plan") or "free").lower() if urow else "free"
        _now = datetime.utcnow()
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(
                subscription_plan="free",
                subscription_end=None,
                subscription_admin_granted=False,
                subscription_paid_lifetime=False,
                needs_tariff_choice=False,
            )
        )
        await record_subscription_event(uid, "free", "free", 0.0, _now, None, None)
        if prev != "free":
            try:
                await notify_subscription_manual_free(uid, prev)
            except Exception:
                pass
    return RedirectResponse("/subscriptions?connected=1", status_code=302)


@router.get("/subscriptions/history", response_class=HTMLResponse)
async def subscriptions_history_page(request: Request):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login?next=/subscriptions/history")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    uid = int(user.get("primary_user_id") or user["id"])
    history = await fetch_subscription_history_display(uid)
    return templates.TemplateResponse(
        "subscription_history.html",
        {"request": request, "user": user, "history": history},
    )


@router.get("/subscriptions/users-search")
async def subscriptions_users_search(request: Request, q: str = ""):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    # Полноширинная «@» (U+FF20) как обычная, чтобы поиск с собакой работал везде
    raw = (q or "").strip().replace("\uff20", "@")
    uid = int(user.get("primary_user_id") or user["id"])
    out: list[dict] = []
    inner = raw.lstrip("@").strip()

    # Только одна или несколько «@» без текста — показать список участников
    if not inner and raw.startswith("@") and set(raw) <= {"@"}:
        rows = await database.fetch_all(
            users.select()
            .where(users.c.id != uid)
            .order_by(users.c.id.desc())
            .limit(30)
        )
        for row in rows:
            nm = (row.get("name") or "").strip() or f"Участник #{row['id']}"
            out.append({"id": int(row["id"]), "name": nm})
        return JSONResponse({"users": out})

    if not inner:
        return JSONResponse({"users": []})

    # ID: только цифры после @ — точное совпадение + префикс по строковому id (12 → 12, 120…)
    if inner.isdigit() and int(inner) >= 0:
        n = int(inner)
        pattern = f"{inner}%"
        rows = await database.fetch_all(
            users.select()
            .where(users.c.id != uid)
            .where(sa.or_(users.c.id == n, sa.cast(users.c.id, sa.String).like(pattern)))
            .order_by(
                sa.case((users.c.id == n, 0), else_=1),
                users.c.id,
            )
            .limit(30)
        )
        for row in rows:
            nm = (row.get("name") or "").strip() or f"Участник #{row['id']}"
            out.append({"id": int(row["id"]), "name": nm})
        return JSONResponse({"users": out})

    if len(inner) < 2:
        return JSONResponse({"users": []})
    like = f"%{inner[:80]}%"
    rows = await database.fetch_all(
        users.select()
        .where(users.c.id != uid)
        .where(sa.or_(users.c.name.ilike(like), users.c.email.ilike(like)))
        .limit(30)
    )
    for row in rows:
        nm = (row.get("name") or "").strip() or f"Участник #{row['id']}"
        out.append({"id": int(row["id"]), "name": nm})
    return JSONResponse({"users": out})


@router.post("/subscriptions/gift")
async def subscriptions_gift_submit(
    request: Request,
    plan: str = Form(...),
    recipient_user_id: int = Form(...),
):
    """Редирект на оплату подарка (бесплатная выдача отключена)."""
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login?next=/subscriptions", status_code=302)
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    plan_key = (plan or "").strip().lower()
    try:
        rid = int(recipient_user_id)
    except (TypeError, ValueError):
        return RedirectResponse("/subscriptions?gift_error=invalid", status_code=302)
    return RedirectResponse(
        f"/pay/gift?plan={quote(plan_key, safe='')}&recipient_id={quote(str(rid), safe='')}",
        status_code=302,
    )


@router.get("/onboarding/tariff", response_class=HTMLResponse)
async def onboarding_tariff_page(request: Request):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login?next=/subscriptions")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    uid = int(user.get("primary_user_id") or user["id"])
    if user.get("role") == "admin":
        return RedirectResponse(f"/community/profile/{uid}", status_code=302)
    return RedirectResponse("/subscriptions")


@router.post("/onboarding/tariff")
async def onboarding_tariff_submit(request: Request, choice: str = Form(...)):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    uid = user.get("primary_user_id") or user["id"]
    choice = (choice or "").strip().lower()
    plans_eff = await get_effective_plans()
    if choice not in plans_eff:
        return RedirectResponse("/subscriptions")
    if choice == "free":
        urow = await database.fetch_one(users.select().where(users.c.id == int(uid)))
        prev = (urow.get("subscription_plan") or "free").lower() if urow else "free"
        _now = datetime.utcnow()
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(
                subscription_plan="free",
                subscription_end=None,
                needs_tariff_choice=False,
                subscription_admin_granted=False,
                subscription_paid_lifetime=False,
            )
        )
        await record_subscription_event(int(uid), "free", "free", 0.0, _now, None, None)
        if prev != "free":
            try:
                await notify_subscription_manual_free(int(uid), prev)
            except Exception:
                pass
    else:
        ok = await activate_subscription(
            int(uid), choice, months=1, credit_referrer_bonus=False
        )
        if not ok:
            return RedirectResponse("/subscriptions")
        await database.execute(
            users.update().where(users.c.id == uid).values(needs_tariff_choice=False)
        )
    dest = await web_default_home_path(int(uid))
    return RedirectResponse(dest, status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/dashboard/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")

    uid = int(user.get("primary_user_id") or user["id"])
    dest = await web_default_home_path(uid)

    # If secondary account slips through session, re-issue token for primary
    if user.get("primary_user_id"):
        primary = await database.fetch_one(
            users.select().where(users.c.id == user["primary_user_id"])
        )
        if primary:
            from auth.session import create_access_token
            token = create_access_token(primary["id"])
            dest = await web_default_home_path(int(primary["id"]))
            response = RedirectResponse(dest, status_code=302)
            response.set_cookie("access_token", token, httponly=True, samesite="lax", max_age=60*60*24*30)
            return response

    return RedirectResponse(dest, status_code=302)


@router.get("/dashboard-lite", response_class=HTMLResponse)
async def dashboard_lite(request: Request):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")
    uid = int(user.get("primary_user_id") or user["id"])
    dest = await web_default_home_path(uid)
    return RedirectResponse(dest, status_code=302)


@router.get("/community/ai/free-status")
@router.get("/community/ai/quota")
async def community_ai_free_status(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)

    uid = user.get("primary_user_id") or user["id"]
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return JSONResponse({"ok": False, "error": "user not found"}, status_code=404)
    u = dict(row)

    plan = await check_subscription(uid)
    eff = await get_effective_plans()
    free_limit = int((eff.get("free") or {}).get("questions_per_day") or 5)
    used = int(u.get("daily_questions") or 0)

    remaining = max(0, free_limit - used) if plan == "free" else -1

    last_rows = await database.fetch_all(
        messages.select()
        .where(messages.c.user_id == uid)
        .where(messages.c.role == "user")
        .order_by(messages.c.created_at.desc())
        .limit(5)
    )
    last_rows = list(reversed(last_rows))

    last_user_messages = []
    for m in last_rows:
        txt = (m.get("content") or "").strip().replace("\n", " ")
        if len(txt) > 140:
            txt = txt[:137] + "..."
        c_at = m.get("created_at")
        last_user_messages.append(
            {
                "text": txt or "—",
                "created_at": c_at.strftime("%d.%m %H:%M") if c_at else "",
            }
        )

    return JSONResponse(
        {
            "ok": True,
            "plan": plan,
            "limit": free_limit,
            "free_limit": free_limit,
            "used": used if plan == "free" else None,
            "remaining": remaining,
            "last_messages": last_user_messages,
            "menu_hint": (
                FREE_AI_LIMIT_MESSAGE
                if plan == "free" and remaining <= 0
                else ""
            ),
        }
    )


@router.post("/api/chat")
async def api_chat(request: Request):
    try:
        user = await get_user_from_request(request)
        body = await request.json()
        user_message = body.get("message", "").strip()

        if not user_message:
            return JSONResponse({"error": "Empty message"}, status_code=400)

        if user:
            # Use primary account's ID for history/limits (covers Mini App + linked accounts)
            effective_user_id = user.get("primary_user_id") or user["id"]

            perm_row = await database.fetch_one(
                admin_permissions.select().where(admin_permissions.c.user_id == effective_user_id)
            )
            is_unlimited = (
                (user.get("role") or "").lower() in ("admin", "moderator")
                or (bool(perm_row.get("can_ai_unlimited")) if perm_row else False)
            )
            if not is_unlimited:
                allowed = await can_ask_question(effective_user_id)
                if not allowed:
                    return JSONResponse(
                        {
                            "error": "limit",
                            "message": FREE_AI_LIMIT_MESSAGE,
                        },
                        status_code=429,
                    )
            plan_before = await check_subscription(effective_user_id)
            answer = await chat_with_ai(user_message=user_message, user_id=effective_user_id)
            await increment_question_count(effective_user_id)
            if user and not is_unlimited and plan_before == "free":
                eff = await get_effective_plans()
                cap = int((eff.get("free") or {}).get("questions_per_day") or 5)
                rowq = await database.fetch_one(users.select().where(users.c.id == effective_user_id))
                used_after = int((rowq or {}).get("daily_questions") or 0)
                rem_after = max(0, cap - used_after)
                if rem_after == 0:
                    answer = (answer or "").rstrip() + FREE_AI_UPGRADE_INLINE
                return JSONResponse(
                    {
                        "answer": answer,
                        "free_ai_remaining": rem_after,
                        "free_ai_limit": cap,
                    }
                )
        else:
            session_key = request.cookies.get("guest_session")
            if not session_key:
                session_key = secrets.token_hex(16)
            count_rows = await database.fetch_all(
                messages.select().where(messages.c.session_key == session_key)
            )
            if len(count_rows) >= 6:  # 3 user + 3 assistant
                return JSONResponse({"error": "limit", "message": "Зарегистрируйтесь для продолжения диалога."}, status_code=429)
            answer = await chat_with_ai(user_message=user_message, session_key=session_key)

        return JSONResponse({"answer": answer})

    except Exception as _e:
        _tb = _traceback.format_exc()
        print("=== /api/chat EXCEPTION ===")
        print(_tb)
        print("===========================")
        return JSONResponse({"error": "ai_error", "message": "Ошибка AI сервиса. Попробуйте позже.", "debug": str(_e)}, status_code=500)


_POST_IMAGE_ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_POST_IMAGE_MAX = 8 * 1024 * 1024


async def _save_community_uploaded_image(image) -> str | None:
    """Save one uploaded image from multipart form; returns public URL or None."""
    if image is None or not getattr(image, "filename", None):
        return None
    ct = getattr(image, "content_type", None) or ""
    if ct not in _POST_IMAGE_ALLOWED:
        return None
    data = await image.read()
    if len(data) > _POST_IMAGE_MAX:
        return None
    ext = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else "jpg"
    filename = f"{uuid.uuid4().hex}.{ext}"
    base = "/data" if os.path.exists("/data") else "./media"
    save_path = os.path.join(base, "community", filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(data)
    return f"/media/community/{filename}"


async def _community_user_brief(user_id: int) -> dict | None:
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return None
    r = dict(row)
    if r.get("primary_user_id"):
        p = await database.fetch_one(users.select().where(users.c.id == r["primary_user_id"]))
        if p:
            r = dict(p)
    return {
        "id": int(r["id"]),
        "name": (r.get("name") or "").strip() or "Участник",
        "avatar": (r.get("avatar") or "") or "",
    }

_GROUP_CHAT_IMG_MAX = 6 * 1024 * 1024
# До ~1 мин голоса (webm/opus/mp4) с запасом под битрейт
_GROUP_CHAT_AUDIO_MAX = 6 * 1024 * 1024
_GROUP_CHAT_AUDIO_TYPES = frozenset({
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/aac",
    "application/octet-stream",
})


def _group_chat_audio_upload_ok(content_type: Optional[str], filename: Optional[str]) -> bool:
    ct = (content_type or "").strip().lower()
    if ct in _GROUP_CHAT_AUDIO_TYPES:
        return True
    if ct.startswith("audio/"):
        return True
    fn = (filename or "").lower()
    return any(
        fn.endswith(suf)
        for suf in (
            ".webm",
            ".ogg",
            ".opus",
            ".mp3",
            ".m4a",
            ".mp4",
            ".wav",
            ".mpeg",
            ".aac",
        )
    )


def _compress_group_chat_image(raw: bytes) -> bytes:
    from io import BytesIO

    from PIL import Image

    im = Image.open(BytesIO(raw))
    if im.mode in ("RGBA", "P"):
        im = im.convert("RGB")
    im.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue()


def _reputation(post_count: int) -> dict:
    if post_count >= 100:
        return {"level": "Легенда", "emoji": "👑", "color": "text-yellow-400"}
    if post_count >= 51:
        return {"level": "Мастер", "emoji": "🔥", "color": "text-orange-400"}
    if post_count >= 21:
        return {"level": "Адепт", "emoji": "⚡", "color": "text-blue-400"}
    if post_count >= 6:
        return {"level": "Участник", "emoji": "🍄", "color": "text-gold"}
    return {"level": "Зерно", "emoji": "🌱", "color": "text-green-400"}


@router.post("/community/post")
async def create_post(request: Request):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")

    form = await request.form()
    content = (form.get("content") or "").strip()
    title_raw = (form.get("title") or "").strip()
    folder_id = form.get("folder_id") or ""

    if len(content) < 2:
        uid = int(user.get("primary_user_id") or user["id"])
        return RedirectResponse(await web_default_home_path(uid))

    imgs = form.getlist("images")
    if not imgs:
        one = form.get("image")
        if one is not None and getattr(one, "filename", None):
            imgs = [one]
    urls: list[str] = []
    for uf in imgs[:5]:
        u = await _save_community_uploaded_image(uf)
        if u:
            urls.append(u)
    images_json = json.dumps(urls) if urls else None
    image_url = urls[0] if urls else None

    fid = int(folder_id) if str(folder_id).strip().isdigit() else None
    effective_uid = user.get("primary_user_id") or user["id"]
    tit = title_raw[:200] or None
    body_text = content
    author_name = (user.get("name") or "").strip() or "Участник"
    post_id = await publish_community_post(
        user_id=int(effective_uid),
        author_name=author_name,
        content=body_text,
        title=tit,
        image_url=image_url,
        images_json=images_json,
        folder_id=fid,
        from_telegram=False,
    )
    return JSONResponse({"ok": True, "id": post_id})


@router.post("/community/post/{post_id}/edit")
async def edit_community_post(
    request: Request,
    post_id: int,
    content: str = Form(""),
    title: str = Form(""),
    image: UploadFile = File(None),
):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not await _can_manage_community_post(user, post):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    tit = (title or "").strip()[:200] or None
    # Allow editing title/photo even when text is not changed.
    # If empty text is sent, keep previous content to avoid accidental wipes.
    new_content = (content or "").strip()
    if len(new_content) < 2:
        new_content = (post["content"] or "").strip()
    vals = {"content": new_content, "title": tit}
    has_new_image = bool(image and image.filename)
    if has_new_image and image.content_type in _POST_IMAGE_ALLOWED:
        data = await image.read()
        if len(data) <= _POST_IMAGE_MAX:
            ext = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else "jpg"
            filename = f"{uuid.uuid4().hex}.{ext}"
            base = "/data" if os.path.exists("/data") else "./media"
            save_path = os.path.join(base, "community", filename)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)
            nu = f"/media/community/{filename}"
            vals["image_url"] = nu
            vals["images_json"] = json.dumps([nu])
    if len((vals.get("content") or "").strip()) < 2 and not tit and not has_new_image:
        return JSONResponse({"error": "too short"}, status_code=400)
    await database.execute(
        community_posts.update().where(community_posts.c.id == post_id).values(**vals)
    )
    return JSONResponse({"ok": True})


@router.post("/community/post/{post_id}/share-dm")
async def share_community_post_dm(request: Request, post_id: int):
    """Отправить ссылку на пост в личку подписчику. Один раз на пост: учёт в reposts_count, повтор запрещён."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        recipient_id = int(body.get("recipient_id") or 0)
    except (TypeError, ValueError):
        recipient_id = 0
    if recipient_id <= 0 or recipient_id == uid:
        return JSONResponse({"error": "bad recipient"}, status_code=400)
    fol = await database.fetch_one(
        community_follows.select()
        .where(community_follows.c.follower_id == uid)
        .where(community_follows.c.following_id == recipient_id)
        .limit(1)
    )
    if not fol:
        return JSONResponse(
            {
                "error": "Можно отправить только тем, на кого вы подписаны в сообществе",
            },
            status_code=403,
        )
    recipient = await database.fetch_one(users.select().where(users.c.id == recipient_id))
    if not recipient:
        return JSONResponse({"error": "recipient not found"}, status_code=404)
    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)

    async def _current_reposts_count() -> int:
        row = await database.fetch_one(
            sa.select(community_posts.c.reposts_count).where(community_posts.c.id == post_id)
        )
        return int(row["reposts_count"] or 0) if row else 0

    existing_repost = await database.fetch_one(
        community_reposts.select()
        .where(community_reposts.c.post_id == post_id)
        .where(community_reposts.c.user_id == uid)
    )
    if existing_repost:
        return JSONResponse(
            {
                "ok": False,
                "error": "Вы уже переслали этот пост",
                "reposts_count": await _current_reposts_count(),
            },
            status_code=409,
        )

    repost_inserted = False
    try:
        await database.execute(
            community_reposts.insert().values(post_id=post_id, user_id=uid)
        )
        repost_inserted = True
        await database.execute(
            community_posts.update()
            .where(community_posts.c.id == post_id)
            .values(reposts_count=community_posts.c.reposts_count + 1)
        )
    except IntegrityError:
        rc = await _current_reposts_count()
        return JSONResponse(
            {
                "ok": False,
                "error": "Вы уже переслали этот пост",
                "reposts_count": rc,
            },
            status_code=409,
        )
    except Exception as e:
        _logger.exception("share dm repost row: %s", e)
        return JSONResponse({"error": "db"}, status_code=500)

    base = settings.SITE_URL.rstrip("/")
    link = f"{base}/community/post/{post_id}"
    ttitle = (post.get("title") or "").strip()
    line = f"🔗 Пост: {ttitle}\n{link}" if ttitle else f"🔗 Пост в сообществе\n{link}"
    try:
        shr = await database.fetch_one_write(
            sa.text(
                "INSERT INTO direct_messages (sender_id, recipient_id, text, is_read, is_system) "
                "VALUES (:s, :r, :t, false, false) RETURNING id"
            ).bindparams(s=uid, r=recipient_id, t=line)
        )
    except Exception as e:
        _logger.exception("share dm: %s", e)
        if repost_inserted:
            await database.execute(
                community_reposts.delete()
                .where(community_reposts.c.post_id == post_id)
                .where(community_reposts.c.user_id == uid)
            )
            await database.execute(
                community_posts.update()
                .where(community_posts.c.id == post_id)
                .values(
                    reposts_count=sa.case(
                        (community_posts.c.reposts_count > 0, community_posts.c.reposts_count - 1),
                        else_=0,
                    )
                )
            )
        return JSONResponse({"error": "db"}, status_code=500)
    try:
        if shr and shr.get("id"):
            mid = int(shr["id"])
            await create_notification(
                recipient_id=int(recipient_id),
                actor_id=int(uid),
                ntype="message",
                title="Сообщение",
                body=line[:400],
                link_url=f"/chats?open_user={uid}",
                source_kind="direct_message",
                source_id=mid,
            )
            await send_event_telegram_html(
                int(recipient_id),
                "message",
                "Сообщение в личку",
                line[:350],
                f"/chats?open_user={uid}",
            )
            await sync_direct_messages_pair(uid, recipient_id, broadcast_legacy_dm_id=mid)
            try:
                from services.neurofungi_ai_busy_dm_reply import maybe_send_neurofungi_ai_busy_dm_reply

                await maybe_send_neurofungi_ai_busy_dm_reply(
                    human_sender_id=int(uid),
                    bot_recipient_id=int(recipient_id),
                )
            except Exception:
                pass
    except Exception:
        pass
    rc = await _current_reposts_count()
    return JSONResponse({"ok": True, "link": link, "reposts_count": rc})


@router.get("/community/me/following-share")
async def community_me_following_share(request: Request):
    """Список аккаунтов, на которые подписан текущий пользователь — для пересылки поста в ЛС."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    rows = await database.fetch_all(
        community_follows.select()
        .where(community_follows.c.follower_id == uid)
        .order_by(community_follows.c.created_at.desc())
        .limit(500)
    )
    out = []
    seen = set()
    for row in rows:
        oid = int(row["following_id"])
        if oid in seen:
            continue
        urow = await database.fetch_one(users.select().where(users.c.id == oid))
        if not urow:
            continue
        r = dict(urow)
        if r.get("primary_user_id"):
            p = await database.fetch_one(users.select().where(users.c.id == r["primary_user_id"]))
            if p:
                r = dict(p)
        seen.add(int(r["id"]))
        out.append(
            {
                "id": int(r["id"]),
                "name": (r.get("name") or "").strip() or "Участник",
                "avatar": r.get("avatar"),
            }
        )
    return JSONResponse({"ok": True, "users": out})


@router.get("/community/users/search")
async def community_users_search(request: Request, q: str = ""):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    needle = (q or "").strip()
    qy = users.select().where(users.c.id != uid)
    if needle:
        qy = qy.where(users.c.name.ilike(f"%{needle}%"))
    rows = await database.fetch_all(qy.order_by(users.c.id.desc()).limit(30))
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "name": (r["name"] or "Участник"),
            "avatar": (r.get("avatar") or ""),
        })
    return JSONResponse({"users": result})


@router.get("/community/users/share-candidates")
async def community_share_candidates(request: Request, q: str = ""):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    needle = (q or "").strip()
    qy = users.select().where(users.c.id != uid).where(users.c.primary_user_id == None)
    if needle:
        qy = qy.where(users.c.name.ilike(f"%{needle}%"))
        qy = qy.order_by(users.c.followers_count.desc().nullslast(), users.c.id.desc()).limit(40)
    else:
        qy = qy.order_by(users.c.followers_count.desc().nullslast(), users.c.id.desc()).limit(6)
    rows = await database.fetch_all(qy)
    return JSONResponse({
        "users": [
            {
                "id": r["id"],
                "name": (r.get("name") or "Участник"),
                "avatar": (r.get("avatar") or ""),
                "followers_count": int(r.get("followers_count") or 0),
            }
            for r in rows
        ]
    })


@router.get("/community/users/mention-suggest")
async def community_users_mention_suggest(request: Request, q: str = "", digits: str = ""):
    """
    Подсказки для @упоминания в полях ввода:
    пустой хвост — список участников; только цифры — сужение по префиксу id (+ точное совпадение первым);
    буквы/смешанный ввод — поиск по имени и email.
    Параметр digits оставлен для совместимости со старым клиентом.
    """
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = int(user.get("primary_user_id") or user["id"])
    raw = ((q or digits) or "").strip().replace("\uff20", "@").lstrip("@").strip()
    if len(raw) > 80:
        raw = raw[:80]

    qy = (
        users.select()
        .where(users.c.id != uid)
        .where(sa.or_(users.c.is_banned == False, users.c.is_banned.is_(None)))
    )
    lim = 30

    if not raw:
        qy = qy.order_by(users.c.followers_count.desc().nullslast(), users.c.id.desc()).limit(lim)
    elif raw.isdigit():
        rd = raw[:12]
        n = int(rd)
        id_txt = sa.cast(users.c.id, sa.String)
        qy = qy.where(sa.or_(users.c.id == n, id_txt.like(f"{rd}%")))
        qy = qy.order_by(
            sa.case((users.c.id == n, 0), else_=1),
            users.c.followers_count.desc().nullslast(),
            users.c.id.asc(),
        ).limit(lim)
    else:
        like = f"%{raw}%"
        qy = qy.where(sa.or_(users.c.name.ilike(like), users.c.email.ilike(like)))
        qy = qy.order_by(users.c.followers_count.desc().nullslast(), users.c.id.asc()).limit(lim)

    rows = await database.fetch_all(qy)
    return JSONResponse(
        {
            "users": [
                {
                    "id": int(r["id"]),
                    "name": (r.get("name") or "").strip() or "Участник",
                    "avatar": (r.get("avatar") or "") or "",
                    "followers_count": int(r.get("followers_count") or 0),
                }
                for r in rows
            ]
        }
    )


@router.post("/community/like/{post_id}")
async def like_post(request: Request, post_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    uid = user.get("primary_user_id") or user["id"]
    post_row = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post_row:
        return JSONResponse({"error": "not found"}, status_code=404)
    author_id = post_row.get("user_id")
    existing = await database.fetch_one(
        community_likes.select()
        .where(community_likes.c.post_id == post_id)
        .where(community_likes.c.user_id == uid)
    )
    if existing:
        await database.execute(
            community_likes.delete()
            .where(community_likes.c.post_id == post_id)
            .where(community_likes.c.user_id == uid)
        )
        await database.execute(
            community_posts.update().where(community_posts.c.id == post_id)
            .values(likes_count=sa.case(
                (community_posts.c.likes_count > 0, community_posts.c.likes_count - 1),
                else_=0
            ))
        )
        cnt_row = await database.fetch_one(
            sa.select(community_posts.c.likes_count).where(community_posts.c.id == post_id)
        )
        lc = int(cnt_row["likes_count"] or 0) if cnt_row else 0
        return JSONResponse({"liked": False, "count": lc})
    else:
        try:
            seen = author_id is not None and author_id == uid
            lk = await database.fetch_one_write(
                community_likes.insert()
                .values(
                    post_id=post_id,
                    user_id=uid,
                    seen_by_post_owner=seen,
                )
                .returning(community_likes.c.id)
            )
            like_row_id = int(lk["id"]) if lk else None
            await database.execute(
                community_posts.update().where(community_posts.c.id == post_id)
                .values(likes_count=community_posts.c.likes_count + 1)
            )
            if (
                author_id
                and author_id != uid
                and like_row_id
                and not seen
            ):
                liker_name = user.get("name") or "Участник"
                await create_notification(
                    recipient_id=int(author_id),
                    actor_id=int(uid),
                    ntype="post_like",
                    title="Лайк поста",
                    body=f"{liker_name} оценил(а) ваш пост",
                    link_url=f"/community/post/{post_id}",
                    source_kind="community_like",
                    source_id=like_row_id,
                )
                await send_event_telegram_html(
                    int(author_id),
                    "post_like",
                    "Лайк поста",
                    f"{liker_name} оценил(а) ваш пост",
                    f"/community/post/{post_id}",
                )
        except Exception:
            pass
        cnt_row = await database.fetch_one(
            sa.select(community_posts.c.likes_count).where(community_posts.c.id == post_id)
        )
        lc = int(cnt_row["likes_count"] or 0) if cnt_row else 0
        return JSONResponse({"liked": True, "count": lc})


@router.get("/community/post/{post_id}/likers-json")
async def community_post_likers_json(request: Request, post_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)
    rows = await database.fetch_all(
        community_likes.select()
        .where(community_likes.c.post_id == post_id)
        .order_by(community_likes.c.id.desc())
    )
    out: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        uid = int(row["user_id"])
        if uid in seen:
            continue
        seen.add(uid)
        u = await _community_user_brief(uid)
        if u:
            out.append(u)
    return JSONResponse({"ok": True, "users": out})


@router.get("/community/post/{post_id}/reposters-json")
async def community_post_reposters_json(request: Request, post_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)
    rows = await database.fetch_all(
        community_reposts.select()
        .where(community_reposts.c.post_id == post_id)
        .order_by(community_reposts.c.id.desc())
    )
    out: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        uid = int(row["user_id"])
        if uid in seen:
            continue
        seen.add(uid)
        u = await _community_user_brief(uid)
        if u:
            out.append(u)
    return JSONResponse({"ok": True, "users": out})


@router.post("/community/post/{post_id}/repost")
async def toggle_community_post_repost(request: Request, post_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)
    existing = await database.fetch_one(
        community_reposts.select()
        .where(community_reposts.c.post_id == post_id)
        .where(community_reposts.c.user_id == uid)
    )
    if existing:
        await database.execute(
            community_reposts.delete()
            .where(community_reposts.c.post_id == post_id)
            .where(community_reposts.c.user_id == uid)
        )
        await database.execute(
            community_posts.update()
            .where(community_posts.c.id == post_id)
            .values(
                reposts_count=sa.case(
                    (community_posts.c.reposts_count > 0, community_posts.c.reposts_count - 1),
                    else_=0,
                )
            )
        )
    else:
        try:
            await database.execute(
                community_reposts.insert().values(post_id=post_id, user_id=uid)
            )
            await database.execute(
                community_posts.update()
                .where(community_posts.c.id == post_id)
                .values(reposts_count=community_posts.c.reposts_count + 1)
            )
        except Exception:
            pass
    cnt_row = await database.fetch_one(
        sa.select(community_posts.c.reposts_count).where(community_posts.c.id == post_id)
    )
    rc = int(cnt_row["reposts_count"] or 0) if cnt_row else 0
    still = await database.fetch_one(
        community_reposts.select()
        .where(community_reposts.c.post_id == post_id)
        .where(community_reposts.c.user_id == uid)
    )
    return JSONResponse({"reposted": still is not None, "count": rc})


@router.post("/community/comment/{post_id}")
async def add_comment(
    request: Request,
    post_id: int,
    content: str = Form(...),
    reply_to_comment_id: str = Form(""),
):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    if len(content.strip()) < 1:
        return JSONResponse({"error": "empty"}, status_code=400)

    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)

    uid = user.get("primary_user_id") or user["id"]
    reply_parent_id: int | None = None
    if (reply_to_comment_id or "").strip().isdigit():
        reply_parent_id = int(reply_to_comment_id.strip())
    parent_row = None
    if reply_parent_id:
        parent_row = await database.fetch_one(
            community_comments.select()
            .where(community_comments.c.id == reply_parent_id)
            .where(community_comments.c.post_id == post_id)
        )
        if not parent_row:
            reply_parent_id = None

    c_seen = post["user_id"] is not None and post["user_id"] == uid
    crow = await database.fetch_one_write(
        community_comments.insert()
        .values(
            post_id=post_id,
            user_id=uid,
            content=content.strip(),
            seen_by_post_owner=c_seen,
        )
        .returning(community_comments.c.id)
    )
    comment_id = int(crow["id"]) if crow else None
    await database.execute(
        community_posts.update().where(community_posts.c.id == post_id)
        .values(comments_count=community_posts.c.comments_count + 1)
    )
    owner_id = post.get("user_id")
    actor_nm = user.get("name") or "Участник"
    stripped = content.strip()
    if owner_id and owner_id != uid and comment_id and not c_seen:
        await create_notification(
            recipient_id=int(owner_id),
            actor_id=int(uid),
            ntype="comment",
            title="Комментарий",
            body=f"{actor_nm}: {stripped[:400]}",
            link_url=f"/community/post/{post_id}",
            source_kind="community_comment",
            source_id=comment_id,
        )
        await send_event_telegram_html(
            int(owner_id),
            "comment",
            "Комментарий к посту",
            f"{actor_nm}: {stripped[:350]}",
            f"/community/post/{post_id}",
        )
    if (
        comment_id
        and parent_row
        and parent_row.get("user_id")
        and int(parent_row["user_id"]) != int(uid)
    ):
        puid = int(parent_row["user_id"])
        await create_notification(
            recipient_id=puid,
            actor_id=int(uid),
            ntype="comment_reply",
            title="Ответ на ваш комментарий",
            body=f"{actor_nm}: {stripped[:400]}",
            link_url=f"/community/post/{post_id}",
            source_kind="comment_reply",
            source_id=comment_id,
        )
        await send_event_telegram_html(
            puid,
            "comment_reply",
            "Ответ на комментарий",
            f"{actor_nm}: {stripped[:350]}",
            f"/community/post/{post_id}",
        )
    puid_reply: int | None = None
    if (
        parent_row
        and parent_row.get("user_id")
        and int(parent_row["user_id"]) != int(uid)
        and reply_parent_id
    ):
        puid_reply = int(parent_row["user_id"])
    if comment_id:
        for mid in extract_mentioned_numeric_ids(stripped):
            if mid == int(uid):
                continue
            if owner_id and mid == int(owner_id) and owner_id != uid and not c_seen:
                continue
            if puid_reply is not None and mid == puid_reply:
                continue
            if not await user_exists(mid):
                continue
            await create_notification(
                recipient_id=mid,
                actor_id=int(uid),
                ntype="mention",
                title="Вас упомянули в комментарии",
                body=f"{actor_nm}: {stripped[:380]}",
                link_url=f"/community/post/{post_id}",
                source_kind="mention_comment",
                source_id=comment_id,
            )
            await send_event_telegram_html(
                mid,
                "mention",
                "Упоминание в комментарии",
                f"{actor_nm}: {stripped[:350]}",
                f"/community/post/{post_id}",
            )
    rep_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_posts).where(community_posts.c.user_id == uid)
    ) or 0
    rep = _reputation(rep_count)
    return JSONResponse({
        "ok": True,
        "comment": {
            "id": comment_id,
            "content": content.strip(),
            "author_name": user.get("name") or "Участник",
            "author_avatar": user.get("avatar"),
            "author_level": rep["level"],
            "author_emoji": rep["emoji"],
        }
    })


@router.get("/community/comments/{post_id}")
async def get_comments(request: Request, post_id: int):
    rows = await database.fetch_all(
        community_comments.select()
        .where(community_comments.c.post_id == post_id)
        .order_by(community_comments.c.created_at.asc())
    )
    result = []
    for c in rows:
        author = None
        if c["user_id"]:
            author = await database.fetch_one(users.select().where(users.c.id == c["user_id"]))
        rep_count = 0
        if author:
            rep_count = await database.fetch_val(
                sa.select(sa.func.count()).select_from(community_posts)
                .where(community_posts.c.user_id == author["id"])
            ) or 0
        rep = _reputation(rep_count)
        result.append({
            "id": c["id"],
            "content": c["content"],
            "created_at": c["created_at"].strftime("%d.%m.%Y %H:%M") if c["created_at"] else "",
            "author_name": (author["name"] if author and author["name"] else "Участник"),
            "author_avatar": author["avatar"] if author else None,
            "author_level": rep["level"],
            "author_emoji": rep["emoji"],
        })
    return JSONResponse({"comments": result})


@router.post("/community/folder")
async def create_folder(request: Request, name: str = Form(...)):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    if not name.strip():
        return JSONResponse({"error": "empty name"}, status_code=400)
    uid = user.get("primary_user_id") or user["id"]
    fid = await database.execute(
        community_folders.insert().values(user_id=uid, name=name.strip())
    )
    return JSONResponse({"ok": True, "id": fid, "name": name.strip()})


def _effective_user_id(user: dict) -> int:
    """Аккаунт для действий в БД: привязанный primary или текущий id."""
    pu = user.get("primary_user_id")
    if pu is not None and str(pu).strip() != "":
        try:
            return int(pu)
        except (TypeError, ValueError):
            pass
    return int(user["id"])


async def _reject_if_group_chats_forbidden(user: dict | None) -> JSONResponse | None:
    if not user:
        return JSONResponse({"error": "auth required", "ok": False}, status_code=401)
    uid = _effective_user_id(user)
    plan = await check_subscription(uid)
    if can_use_community_group_chats(user, plan):
        return None
    return JSONResponse(
        {
            "ok": False,
            "error": "Групповые чаты доступны с тарифа «Старт». Оформите подписку в разделе «Подписка».",
            "redirect": "/subscriptions",
        },
        status_code=403,
    )


async def _account_family_ids(root_user_id: int) -> set[int]:
    ids: set[int] = set()
    try:
        rows = await database.fetch_all(
            users.select().with_only_columns(users.c.id, users.c.primary_user_id).where(
                sa.or_(users.c.id == root_user_id, users.c.primary_user_id == root_user_id)
            )
        )
        for r in rows:
            try:
                ids.add(int(r["id"]))
            except Exception:
                continue
    except Exception:
        pass
    ids.add(int(root_user_id))
    return ids


async def _can_manage_community_post(user: dict, post_row) -> bool:
    if not user or not post_row:
        return False
    if user.get("role") == "admin":
        return True
    uid = _effective_user_id(user)
    try:
        owner_id = int(post_row["user_id"] or 0)
    except Exception:
        owner_id = 0
    if owner_id <= 0:
        return False
    if owner_id == uid:
        return True
    family_ids = await _account_family_ids(uid)
    return owner_id in family_ids


def _normalize_profile_url(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return s[:2000]


def _looks_like_wallet_address(w: str | None) -> bool:
    s = (w or "").strip()
    if len(s) != 42 or not s.startswith("0x"):
        return False
    try:
        int(s[2:], 16)
    except ValueError:
        return False
    return True


@router.get("/profile/wallet-recipients")
async def profile_wallet_recipients(request: Request):
    """Семья и подписки с привязанным адресом кошелька — для подготовки перевода SHEVELEV в приложении Decimal."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = _effective_user_id(user)
    out: list[dict] = []
    seen: set[int] = set()

    family_ids = await _account_family_ids(uid)
    fam_rows = await database.fetch_all(
        users.select().where(users.c.id.in_(family_ids)).where(users.c.wallet_address.isnot(None))
    )
    for r in fam_rows:
        wid = int(r["id"])
        if wid == uid or wid in seen:
            continue
        waddr = (r.get("wallet_address") or "").strip()
        if not _looks_like_wallet_address(waddr):
            continue
        seen.add(wid)
        nm = (r.get("name") or "").strip() or f"Участник #{wid}"
        out.append({"id": wid, "name": nm, "wallet_address": waddr})

    follow_rows = await database.fetch_all(
        sa.select(users.c.id, users.c.name, users.c.wallet_address)
        .select_from(
            users.join(
                community_follows,
                sa.and_(
                    community_follows.c.following_id == users.c.id,
                    community_follows.c.follower_id == uid,
                ),
            )
        )
        .where(users.c.wallet_address.isnot(None))
    )
    for r in follow_rows:
        wid = int(r["id"])
        if wid == uid or wid in seen:
            continue
        waddr = (r.get("wallet_address") or "").strip()
        if not _looks_like_wallet_address(waddr):
            continue
        seen.add(wid)
        nm = (r.get("name") or "").strip() or f"Участник #{wid}"
        out.append({"id": wid, "name": nm, "wallet_address": waddr})

    out.sort(key=lambda x: (x["name"].lower(), x["id"]))
    return JSONResponse({"ok": True, "recipients": out})


@router.post("/profile/wallet")
async def update_wallet(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        wallet = body.get("wallet_address") or body.get("wallet") or ""
    else:
        form = await request.form()
        wallet = form.get("wallet_address") or form.get("wallet") or ""
    uid = _effective_user_id(user)
    await database.execute(
        users.update().where(users.c.id == uid).values(wallet_address=wallet.strip() or None)
    )
    return JSONResponse({"ok": True})


@router.post("/profile/token-visibility")
async def profile_token_visibility(request: Request):
    """Какие кэшированные балансы токенов видят другие на публичном профиле."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    show_del = bool(body.get("show_del_to_public", True))
    show_shev = bool(body.get("show_shev_to_public", True))
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(show_del_to_public=show_del, show_shev_to_public=show_shev)
    )
    return JSONResponse({"ok": True})


@router.post("/profile/token-lamp")
async def profile_token_lamp(request: Request):
    """Персональный переключатель лампы подсветки токенов/аватара (сохраняется в профиле)."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    enabled = bool(body.get("token_lamp_enabled", True))
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(token_lamp_enabled=enabled)
    )
    return JSONResponse({"ok": True, "token_lamp_enabled": enabled})


@router.post("/profile/plan-upgrade-request")
async def profile_plan_upgrade_request(
    request: Request,
    requested_plan: str = Form(...),
    note: str = Form(""),
):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    rp = (requested_plan or "").strip().lower()
    plans_eff = await get_effective_plans()
    if not is_catalog_paid_checkout_plan(plans_eff, rp):
        return JSONResponse({"error": "Недопустимый тариф"}, status_code=400)
    nm = (note or "").strip()[:2000]
    urow = await database.fetch_one(users.select().where(users.c.id == uid))
    uname = (urow.get("name") if urow else "") or "—"
    uemail = (urow.get("email") if urow else "") or "—"
    utg = urow.get("tg_id") if urow else None
    try:
        await database.execute(
            sa.text(
                "INSERT INTO plan_upgrade_requests (user_id, requested_plan, note) VALUES (:u,:p,:n)"
            ).bindparams(u=uid, p=rp, n=nm or None)
        )
    except Exception:
        pass
    cur = ((urow.get("subscription_plan") or "free") if urow else "free").lower()
    txt = (
        "📋 Запрос смены тарифа (NEUROFUNGI AI)\n"
        f"Пользователь: {uname} (id {uid})\n"
        f"Email: {uemail}\n"
        f"Telegram id: {utg or '—'}\n"
        f"Текущий план: {cur}\n"
        f"Запрошен: {rp}\n"
        f"Комментарий: {nm or '—'}"
    )
    await notify_admin_telegram(txt)
    try:
        await notify_plan_upgrade_request(user_id=uid, requested_plan=rp, note=nm)
    except Exception:
        pass
    return JSONResponse({"ok": True})


@router.post("/profile/shevelev-transfer-notify")
async def shevelev_transfer_notify(request: Request):
    """После отправки SHEVELEV в MetaMask — уведомить получателя в ЛС; в Telegram, если не онлайн."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    to_addr = (body.get("to") or "").strip()
    tx_hash = (body.get("tx_hash") or "").strip()
    amount = (body.get("amount") or "").strip()[:64]
    if not tx_hash or len(tx_hash) < 8 or not to_addr.startswith("0x") or len(to_addr) < 42:
        return JSONResponse({"error": "bad params"}, status_code=400)
    uid = user.get("primary_user_id") or user["id"]
    sender_row = await database.fetch_one(users.select().where(users.c.id == uid))
    sender_name = (sender_row.get("name") if sender_row else None) or "Участник"
    to_norm = to_addr.lower()
    recipient = await database.fetch_one(
        sa.text(
            "SELECT * FROM users WHERE id != :sid AND wallet_address IS NOT NULL "
            "AND LOWER(TRIM(wallet_address)) = :w LIMIT 1"
        ).bindparams(sid=uid, w=to_norm)
    )
    if not recipient:
        return JSONResponse({"ok": True, "notified": False})
    rid = recipient["id"]
    short_tx = tx_hash if len(tx_hash) <= 28 else tx_hash[:22] + "…"
    msg = (
        f"Вам отправлен перевод SHEVELEV: {amount or '—'} (сеть Decimal Smart Chain).\n"
        f"От: {sender_name}.\n"
        f"Хэш транзакции: {short_tx}\n"
        f"Проверьте подтверждение в блокчейне; баланс в кабинете обновляется после синхронизации."
    )
    try:
        wrow = await database.fetch_one_write(
            sa.text(
                "INSERT INTO direct_messages (sender_id, recipient_id, text, is_read, is_system) "
                "VALUES (:s, :r, :t, false, false) RETURNING id"
            ).bindparams(s=uid, r=rid, t=msg)
        )
    except Exception:
        return JSONResponse({"error": "dm"}, status_code=500)
    try:
        if wrow and wrow.get("id"):
            mid = int(wrow["id"])
            await create_notification(
                recipient_id=int(rid),
                actor_id=int(uid),
                ntype="message",
                title="Перевод SHEVELEV",
                body=msg[:400],
                link_url=f"/chats?open_user={uid}",
                source_kind="direct_message",
                source_id=mid,
            )
            await sync_direct_messages_pair(uid, int(rid), broadcast_legacy_dm_id=mid)
    except Exception:
        pass
    tg_id = recipient.get("tg_id") or recipient.get("linked_tg_id")
    last_seen = recipient.get("last_seen_at")
    online = False
    if last_seen:
        try:
            online = datetime.utcnow() - last_seen < timedelta(minutes=3)
        except Exception:
            online = False
    if tg_id and not online and await should_send_telegram(int(rid)):
        from services.notify_user_stub import notify_user

        tg_line = "💰 " + msg.replace("\n", " ")
        await notify_user(int(tg_id), tg_line[:3900])
    return JSONResponse({"ok": True, "notified": True})


@router.post("/profile/wallet/sync-decimal")
async def sync_decimal_del_balance(request: Request):
    """Синхронизация нативного баланса DEL с RPC Decimal Smart Chain (сервер)."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    from services.decimal_chain import fetch_native_del_balance

    uid = _effective_user_id(user)
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    w = (row.get("wallet_address") or "").strip() if row else ""
    if not w.startswith("0x"):
        return JSONResponse({"error": "Укажите адрес кошелька (0x…)"}, status_code=400)
    bal = await fetch_native_del_balance(w)
    if bal is None:
        return JSONResponse({"error": "Не удалось запросить сеть Decimal"}, status_code=502)
    fmt = f"{bal:.12f}".rstrip("0").rstrip(".") or "0"
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(decimal_del_balance=fmt, decimal_balance_cached_at=datetime.utcnow())
    )
    return JSONResponse({"ok": True, "del": bal, "formatted": fmt})


@router.post("/profile/wallet/sync-shevelev")
async def sync_shevelev_balance(request: Request):
    """Серверная синхронизация баланса SHEVELEV (ERC-20 на Decimal) — виден всем в профиле."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    from config import shevelev_token_address
    from services.decimal_chain import fetch_erc20_balance

    uid = _effective_user_id(user)
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    w = (row.get("wallet_address") or "").strip() if row else ""
    tok = shevelev_token_address()
    if not tok:
        return JSONResponse(
            {"error": "Адрес контракта SHEVELEV не задан: переменная SHEVELEV_TOKEN_ADDRESS или файл deployment/shevelev_token_address.txt"},
            status_code=400,
        )
    if not w.startswith("0x"):
        return JSONResponse({"error": "Укажите адрес кошелька (0x…)"}, status_code=400)
    bal = await fetch_erc20_balance(tok, w)
    if bal is None:
        return JSONResponse({"error": "Не удалось прочитать баланс токена"}, status_code=502)
    fmt = f"{bal:.10f}".rstrip("0").rstrip(".") or "0"
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(shevelev_balance_cached=fmt, shevelev_balance_cached_at=datetime.utcnow())
    )
    return JSONResponse({"ok": True, "shevelev": bal, "formatted": fmt})


@router.post("/profile/wallet/sync-balances")
async def sync_decimal_balances_combined(request: Request):
    """Один запрос: нативный DEL + SHEVELEV (ERC-20) с RPC → кэш в БД для профиля."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    from config import shevelev_token_address
    from services.decimal_chain import fetch_erc20_balance, fetch_native_del_balance

    uid = _effective_user_id(user)
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    w = (row.get("wallet_address") or "").strip() if row else ""
    if not w.startswith("0x"):
        return JSONResponse({"error": "Укажите адрес кошелька (0x…) в настройках"}, status_code=400)

    del_bal = await fetch_native_del_balance(w)
    if del_bal is None:
        return JSONResponse({"error": "Не удалось запросить сеть Decimal (DEL)"}, status_code=502)
    del_fmt = f"{del_bal:.12f}".rstrip("0").rstrip(".") or "0"

    tok = shevelev_token_address()
    shev_fmt = None
    shev_val = None
    shev_err = None
    if tok:
        shev_val = await fetch_erc20_balance(tok, w)
        if shev_val is None:
            shev_err = "Не удалось прочитать баланс SHEVELEV по RPC (проверьте сеть и адрес контракта)."
            await database.execute(
                users.update()
                .where(users.c.id == uid)
                .values(decimal_del_balance=del_fmt, decimal_balance_cached_at=datetime.utcnow())
            )
        else:
            shev_fmt = f"{shev_val:.10f}".rstrip("0").rstrip(".") or "0"
            await database.execute(
                users.update()
                .where(users.c.id == uid)
                .values(
                    decimal_del_balance=del_fmt,
                    decimal_balance_cached_at=datetime.utcnow(),
                    shevelev_balance_cached=shev_fmt,
                    shevelev_balance_cached_at=datetime.utcnow(),
                )
            )
    else:
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(decimal_del_balance=del_fmt, decimal_balance_cached_at=datetime.utcnow())
        )

    return JSONResponse(
        {
            "ok": True,
            "del": del_bal,
            "del_formatted": del_fmt,
            "shevelev": shev_val,
            "shevelev_formatted": shev_fmt,
            "shevelev_error": shev_err,
        }
    )


@router.post("/dashboard/language")
async def update_language(request: Request, language: str = Form(...)):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")
    uid = _effective_user_id(user)
    await database.execute(
        users.update().where(users.c.id == uid).values(language=language)
    )
    dest = await web_default_home_path(uid)
    return RedirectResponse(dest, status_code=302)


_AVATAR_ALLOWED = {"image/jpeg", "image/png", "image/webp"}
_AVATAR_MAX_SIZE = 3 * 1024 * 1024  # 3 MB


@router.post("/profile/upload-avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    if file.content_type not in _AVATAR_ALLOWED:
        return JSONResponse({"error": "Допустимые форматы: JPEG, PNG, WebP"}, status_code=400)

    data = await file.read()
    if len(data) > _AVATAR_MAX_SIZE:
        return JSONResponse({"error": "Файл слишком большой (макс. 3 МБ)"}, status_code=400)

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    uid = _effective_user_id(user)
    filename = f"{uid}.{ext}"

    base = "/data" if os.path.exists("/data") else "./media"
    save_path = os.path.join(base, "avatars", filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    with open(save_path, "wb") as f:
        f.write(data)

    url = f"/media/avatars/{filename}"
    await database.execute(
        users.update().where(users.c.id == uid).values(avatar=url)
    )
    return JSONResponse({"ok": True, "url": url})


@router.post("/profile/bio")
async def update_bio(request: Request, bio: str = Form("")):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = _effective_user_id(user)
    await database.execute(
        users.update().where(users.c.id == uid).values(bio=bio.strip()[:300] or None)
    )
    return JSONResponse({"ok": True})


@router.post("/profile/me")
async def update_profile_me(
    request: Request,
    name: str = Form(""),
    bio: str = Form(""),
    link_label: str = Form(""),
    link_url: str = Form(""),
):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = _effective_user_id(user)
    vals = {
        "bio": bio.strip()[:300] or None,
        "profile_link_label": link_label.strip()[:120] or None,
        "profile_link_url": _normalize_profile_url(link_url),
    }
    if name.strip():
        vals["name"] = name.strip()[:255]
    await database.execute(users.update().where(users.c.id == uid).values(**vals))
    return JSONResponse({"ok": True})


@router.post("/community/follow/{target_id}")
async def follow_user(request: Request, target_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    if uid == target_id:
        return JSONResponse({"error": "cannot follow yourself"}, status_code=400)
    existing = await database.fetch_one(
        community_follows.select()
        .where(community_follows.c.follower_id == uid)
        .where(community_follows.c.following_id == target_id)
    )
    if existing:
        # Unfollow
        await database.execute(
            community_follows.delete()
            .where(community_follows.c.follower_id == uid)
            .where(community_follows.c.following_id == target_id)
        )
        await database.execute(
            users.update().where(users.c.id == uid)
            .values(following_count=sa.case(
                (users.c.following_count > 0, users.c.following_count - 1), else_=0
            ))
        )
        await database.execute(
            users.update().where(users.c.id == target_id)
            .values(followers_count=sa.case(
                (users.c.followers_count > 0, users.c.followers_count - 1), else_=0
            ))
        )
        return JSONResponse({"following": False})
    else:
        try:
            fr = await database.fetch_one_write(
                community_follows.insert()
                .values(follower_id=uid, following_id=target_id)
                .returning(community_follows.c.id)
            )
            fid = int(fr["id"]) if fr else None
            await database.execute(
                users.update().where(users.c.id == uid)
                .values(following_count=users.c.following_count + 1)
            )
            await database.execute(
                users.update().where(users.c.id == target_id)
                .values(followers_count=users.c.followers_count + 1)
            )
            if fid:
                actor_name = user.get("name") or "Участник"
                await create_notification(
                    recipient_id=int(target_id),
                    actor_id=int(uid),
                    ntype="follower",
                    title="Новый подписчик",
                    body=f"{actor_name} подписался(ась) на вас",
                    link_url=f"/community/profile/{uid}",
                    source_kind="community_follow",
                    source_id=fid,
                )
                await send_event_telegram_html(
                    int(target_id),
                    "follower",
                    "Новый подписчик",
                    f"{actor_name} подписался(ась) на вас",
                    f"/community/profile/{uid}",
                )
        except Exception:
            pass
        return JSONResponse({"following": True})


@router.post("/community/save/{post_id}")
async def save_post(request: Request, post_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    existing = await database.fetch_one(
        community_saved.select()
        .where(community_saved.c.user_id == uid)
        .where(community_saved.c.post_id == post_id)
    )
    if existing:
        await database.execute(
            community_saved.delete()
            .where(community_saved.c.user_id == uid)
            .where(community_saved.c.post_id == post_id)
        )
        await database.execute(
            community_posts.update().where(community_posts.c.id == post_id)
            .values(saves_count=sa.case(
                (community_posts.c.saves_count > 0, community_posts.c.saves_count - 1), else_=0
            ))
        )
        return JSONResponse({"saved": False})
    else:
        try:
            await database.execute(
                community_saved.insert().values(user_id=uid, post_id=post_id)
            )
            await database.execute(
                community_posts.update().where(community_posts.c.id == post_id)
                .values(saves_count=community_posts.c.saves_count + 1)
            )
        except Exception:
            pass
        return JSONResponse({"saved": True})


@router.post("/community/message/{recipient_id}")
async def send_dm(request: Request, recipient_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)
    uid = user.get("primary_user_id") or user["id"]
    if uid == recipient_id:
        return JSONResponse({"error": "cannot message yourself"}, status_code=400)
    row = None
    try:
        row = await database.fetch_one_write(
            sa.text(
                "INSERT INTO direct_messages (sender_id, recipient_id, text, is_read, is_system) "
                "VALUES (:s, :r, :t, false, false) RETURNING id"
            ).bindparams(s=uid, r=recipient_id, t=text)
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    msg_id = row["id"] if row else None
    actor_name = user.get("name") or "Участник"
    if msg_id:
        await create_notification(
            recipient_id=int(recipient_id),
            actor_id=int(uid),
            ntype="message",
            title="Личное сообщение",
            body=f"{actor_name}: {text[:400]}",
            link_url=f"/chats?open_user={uid}",
            source_kind="direct_message",
            source_id=int(msg_id),
        )
    recipient = await database.fetch_one(users.select().where(users.c.id == recipient_id))
    if recipient and await should_send_telegram_for_event(int(recipient_id), "message"):
        tg_id = recipient.get("tg_id") or recipient.get("linked_tg_id")
        if tg_id:
            from services.notify_user_stub import notify_user_dm_with_read_button

            read_path = f"/chats?open_user={uid}"
            await notify_user_dm_with_read_button(tg_id, actor_name, text, read_path)
    try:
        if msg_id:
            await sync_direct_messages_pair(uid, recipient_id, broadcast_legacy_dm_id=int(msg_id))
    except Exception:
        pass
    try:
        from services.neurofungi_ai_busy_dm_reply import maybe_send_neurofungi_ai_busy_dm_reply

        await maybe_send_neurofungi_ai_busy_dm_reply(
            human_sender_id=int(uid),
            bot_recipient_id=int(recipient_id),
        )
    except Exception:
        pass
    return JSONResponse({"ok": True, "id": msg_id})


def _parse_inbox_after_id(raw: Optional[str]) -> int:
    try:
        v = int((raw or "0").strip() or "0")
        return max(0, v)
    except (ValueError, TypeError):
        return 0


@router.get("/community/messages/inbox-toast")
async def inbox_dm_toast(request: Request):
    """Для онлайн-получателя: один «тост» о новом непрочитанном ЛС (клиент шлёт after_id из sessionStorage)."""
    aid = _parse_inbox_after_id(request.query_params.get("after_id"))
    user = await require_auth(request)
    if not user:
        return JSONResponse({"toast": None})
    uid = user.get("primary_user_id") or user["id"]
    try:
        row = await database.fetch_one(
            sa.text(
                """
                SELECT dm.id, dm.sender_id, dm.text, u.name
                FROM direct_messages dm
                JOIN users u ON u.id = dm.sender_id
                WHERE dm.recipient_id = :uid AND dm.is_system = false AND dm.is_read = false
                ORDER BY dm.id DESC
                LIMIT 1
                """
            ),
            {"uid": uid},
        )
    except Exception:
        return JSONResponse({"toast": None})
    if not row:
        return JSONResponse({"toast": None})
    mid = int(row["id"] or 0)
    if mid <= aid:
        return JSONResponse({"toast": None})
    return JSONResponse(
        {
            "toast": {
                "id": mid,
                "sender_id": int(row["sender_id"] or 0),
                "name": (row.get("name") or "Участник"),
                "snippet": ((row.get("text") or "")[:160]),
                "url": f"/messages/{int(row['sender_id'] or 0)}",
            }
        }
    )


@router.get("/community/messages/{other_id}")
async def get_dm_thread(request: Request, other_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT id, sender_id, text, created_at FROM direct_messages
            WHERE (sender_id = :uid AND recipient_id = :oid)
               OR (sender_id = :oid AND recipient_id = :uid AND is_system = false)
            ORDER BY created_at ASC
            LIMIT 100
            """
        ),
        {"uid": uid, "oid": other_id},
    )
    await database.execute(
        sa.text(
            "UPDATE direct_messages SET is_read = true "
            "WHERE sender_id = :oid AND recipient_id = :uid AND is_read = false AND is_system = false"
        ),
        {"oid": other_id, "uid": uid},
    )
    try:
        await sync_direct_messages_pair(uid, other_id)
    except Exception:
        pass
    result = [
        {
            "id": r["id"],
            "sender_id": r["sender_id"],
            "text": r["text"],
            "is_mine": r["sender_id"] == uid,
            "created_at": r["created_at"].strftime("%H:%M") if r["created_at"] else "",
        }
        for r in rows
    ]
    return JSONResponse({"messages": result})


@router.get("/community/unread-count")
async def unread_count(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"count": 0})
    uid = user.get("primary_user_id") or user["id"]
    count = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(direct_messages)
        .where(direct_messages.c.recipient_id == uid)
        .where(direct_messages.c.is_read.is_(False))
        .where(direct_messages.c.is_system.is_(False))
    ) or 0
    return JSONResponse({"count": count})


@router.get("/community/conversations")
async def get_conversations(request: Request):
    """Get list of DM conversations for the current user (direct_messages, как /messages)."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    from web.routes.public import _get_conversations

    raw = await _get_conversations(uid)
    convos = []
    for c in raw:
        oid = int(c.get("other_id") or 0)
        if oid == 0:
            continue
        lt = c.get("last_time") or ""
        last_at = ""
        if lt:
            try:
                last_at = datetime.fromisoformat(lt.replace("Z", "+00:00")).strftime("%H:%M")
            except Exception:
                last_at = lt[11:16] if len(lt) >= 16 else ""
        convos.append({
            "user_id": oid,
            "name": c.get("name") or "Участник",
            "avatar": c.get("avatar"),
            "last_text": (c.get("last_text") or "")[:80],
            "unread": int(c.get("unread") or 0),
            "last_at": last_at,
        })
    return JSONResponse({"conversations": convos})


@router.delete("/community/comment/{comment_id}")
async def delete_comment(request: Request, comment_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    row = await database.fetch_one(
        community_comments.select().where(community_comments.c.id == comment_id)
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    if row["user_id"] != uid:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        community_comments.delete().where(community_comments.c.id == comment_id)
    )
    await database.execute(
        community_posts.update().where(community_posts.c.id == row["post_id"])
        .values(comments_count=sa.case(
            (community_posts.c.comments_count > 0, community_posts.c.comments_count - 1),
            else_=0
        ))
    )
    return JSONResponse({"ok": True})


@router.post("/community/profile/{target_id}/like")
async def like_profile(request: Request, target_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    if uid == target_id:
        return JSONResponse({"error": "cannot like yourself"}, status_code=400)
    existing = await database.fetch_one(
        profile_likes.select()
        .where(profile_likes.c.user_id == uid)
        .where(profile_likes.c.liked_user_id == target_id)
    )
    if existing:
        await database.execute(
            profile_likes.delete()
            .where(profile_likes.c.user_id == uid)
            .where(profile_likes.c.liked_user_id == target_id)
        )
        count = await database.fetch_val(
            sa.select(sa.func.count()).select_from(profile_likes)
            .where(profile_likes.c.liked_user_id == target_id)
        ) or 0
        return JSONResponse({"liked": False, "count": count})
    else:
        try:
            plr = await database.fetch_one_write(
                profile_likes.insert()
                .values(
                    user_id=uid, liked_user_id=target_id, seen_by_owner=False
                )
                .returning(profile_likes.c.id)
            )
            plid = int(plr["id"]) if plr else None
            if plid:
                liker_name = user.get("name") or "Участник"
                await create_notification(
                    recipient_id=int(target_id),
                    actor_id=int(uid),
                    ntype="profile_like",
                    title="Лайк профиля",
                    body=f"{liker_name} оценил(а) ваш профиль",
                    link_url=f"/community/profile/{uid}",
                    source_kind="profile_like",
                    source_id=plid,
                )
                await send_event_telegram_html(
                    int(target_id),
                    "profile_like",
                    "Лайк профиля",
                    f"{liker_name} оценил(а) ваш профиль",
                    f"/community/profile/{uid}",
                )
        except Exception:
            pass
        count = await database.fetch_val(
            sa.select(sa.func.count()).select_from(profile_likes)
            .where(profile_likes.c.liked_user_id == target_id)
        ) or 0
        return JSONResponse({"liked": True, "count": count})


async def _user_in_community_group(group_id: int, uid: int) -> bool:
    row = await database.fetch_one(
        community_group_members.select()
        .where(community_group_members.c.group_id == group_id)
        .where(community_group_members.c.user_id == uid)
    )
    return row is not None


async def _group_user_ban_status(group_id: int, uid: int) -> dict | None:
    row = await database.fetch_one(
        community_group_member_bans.select()
        .where(community_group_member_bans.c.group_id == group_id)
        .where(community_group_member_bans.c.user_id == uid)
    )
    if not row:
        return None
    is_perm = bool(row.get("is_permanent"))
    banned_until = row.get("banned_until")
    if not is_perm and banned_until:
        try:
            now = datetime.utcnow()
            bu = banned_until.replace(tzinfo=None) if getattr(banned_until, "tzinfo", None) else banned_until
            if bu <= now:
                await database.execute(
                    community_group_member_bans.delete()
                    .where(community_group_member_bans.c.group_id == group_id)
                    .where(community_group_member_bans.c.user_id == uid)
                )
                return None
        except Exception:
            pass
    return dict(row)


async def _group_member_permissions(group_id: int, uid: int) -> dict:
    row = await database.fetch_one(
        community_group_member_permissions.select()
        .where(community_group_member_permissions.c.group_id == group_id)
        .where(community_group_member_permissions.c.user_id == uid)
    )
    if not row:
        return {"can_send_messages": True, "can_send_photo": True, "can_send_audio": True}
    return {
        "can_send_messages": bool(row.get("can_send_messages", True)),
        "can_send_photo": bool(row.get("can_send_photo", True)),
        "can_send_audio": bool(row.get("can_send_audio", True)),
    }


async def _cleanup_group_messages_by_policy(group_id: int) -> None:
    g = await fetch_community_group_row(group_id)
    if not g:
        return
    if not bool(g.get("auto_delete_enabled")):
        return
    days = g.get("message_retention_days")
    try:
        d = int(days) if days is not None else 0
    except (TypeError, ValueError):
        d = 0
    if d <= 0:
        return
    cutoff = datetime.utcnow() - timedelta(days=d)
    await database.execute(
        community_group_messages.delete()
        .where(community_group_messages.c.group_id == group_id)
        .where(community_group_messages.c.created_at < cutoff)
    )


async def _ensure_community_group_member(group_id: int, uid: int) -> bool:
    """Если пользователь — создатель или группа open, но строки в members нет — добавить (после сбоя INSERT)."""
    if await _group_user_ban_status(group_id, uid):
        return False
    if await _user_in_community_group(group_id, uid):
        return True
    g = await fetch_community_group_row(group_id)
    if not g:
        return False
    cb = g.get("created_by")
    mode = (g.get("join_mode") or "open").lower()
    if cb is not None and int(cb) == int(uid):
        try:
            await database.execute(
                sa.text(
                    "INSERT INTO community_group_members (group_id, user_id) VALUES (:gid, :uid) "
                    "ON CONFLICT (group_id, user_id) DO NOTHING"
                ).bindparams(gid=group_id, uid=uid)
            )
        except Exception as e:
            _logger.warning("ensure member (creator): %s", e)
        return await _user_in_community_group(group_id, uid)
    if mode == "open":
        try:
            await database.execute(
                sa.text(
                    "INSERT INTO community_group_members (group_id, user_id) VALUES (:gid, :uid) "
                    "ON CONFLICT (group_id, user_id) DO NOTHING"
                ).bindparams(gid=group_id, uid=uid)
            )
        except Exception as e:
            _logger.warning("ensure member (open): %s", e)
        return await _user_in_community_group(group_id, uid)
    return False


def _can_manage_community_group(g: dict, user: dict, uid: int) -> bool:
    """Настройки группы в кабинете: только операторы платформы (полный CRUD в админке «Группы»)."""
    return is_platform_operator(user)


def _can_edit_group_image(g: dict, user: dict, uid: int) -> bool:
    """Аватар группы: оператор или создатель группы."""
    if is_platform_operator(user):
        return True
    cb = g.get("created_by")
    if cb is None:
        return False
    try:
        return int(cb) == int(uid)
    except (TypeError, ValueError):
        return False


def _group_rows_to_dicts(rows) -> list[dict]:
    """Normalize rows for JSON/API and for Jinja |tojson (no raw datetime/Decimal)."""
    out = []
    for r in rows:
        d = dict(r)
        d["is_member"] = bool(d.get("is_member"))
        d["pending_join"] = bool(d.get("pending_join"))
        for k in ("msg_count", "member_count"):
            if k in d and d[k] is not None:
                try:
                    d[k] = int(d[k])
                except (TypeError, ValueError):
                    pass
        if not d.get("image_url"):
            d["image_url"] = None
        if "unread_count" in d and d["unread_count"] is not None:
            try:
                d["unread_count"] = int(d["unread_count"])
            except (TypeError, ValueError):
                d["unread_count"] = 0
        for k, v in list(d.items()):
            if isinstance(v, datetime):
                d[k] = v.isoformat()
            elif isinstance(v, date):
                d[k] = v.isoformat()
            elif isinstance(v, Decimal):
                d[k] = float(v)
        out.append(d)
    return out


async def fetch_community_groups_for_user(uid: int) -> list[dict]:
    """Список групп для кабинета/API. Полный запрос; при ошибке — по цепочке упрощённых запросов."""
    q_rich = """
            SELECT g.id, g.name, g.description, g.created_at, g.created_by, g.image_url,
              COALESCE(g.join_mode, 'approval') AS join_mode,
              g.message_retention_days,
              g.slow_mode_seconds,
              COALESCE(g.show_history_to_new_members, true) AS show_history_to_new_members,
              (SELECT COUNT(*)::bigint FROM community_group_members m WHERE m.group_id = g.id) AS member_count,
              EXISTS(SELECT 1 FROM community_group_members m2 WHERE m2.group_id = g.id AND m2.user_id = :uid) AS is_member,
              (SELECT COUNT(*)::bigint FROM community_group_messages gm WHERE gm.group_id = g.id) AS msg_count,
              EXISTS(
                SELECT 1 FROM community_group_join_requests r
                WHERE r.group_id = g.id AND r.user_id = :uid AND r.status = 'pending'
              ) AS pending_join,
              (SELECT LEFT(gm.text::text, 500) FROM community_group_messages gm WHERE gm.group_id = g.id ORDER BY gm.created_at DESC LIMIT 1) AS last_message_text,
              (SELECT gm.created_at FROM community_group_messages gm WHERE gm.group_id = g.id ORDER BY gm.created_at DESC LIMIT 1) AS last_message_at,
              (SELECT CASE WHEN EXISTS (SELECT 1 FROM community_group_members mx WHERE mx.group_id = g.id AND mx.user_id = :uid) THEN (
                SELECT COUNT(*)::bigint FROM community_group_messages gm
                WHERE gm.group_id = g.id
                AND gm.created_at > COALESCE(
                  (SELECT m.last_read_at FROM community_group_members m WHERE m.group_id = g.id AND m.user_id = :uid),
                  (SELECT m2.joined_at FROM community_group_members m2 WHERE m2.group_id = g.id AND m2.user_id = :uid)
                )
              ) ELSE 0::bigint END) AS unread_count
            FROM community_groups g
            ORDER BY
              (SELECT gm.created_at FROM community_group_messages gm WHERE gm.group_id = g.id ORDER BY gm.created_at DESC LIMIT 1) DESC NULLS LAST,
              g.created_at DESC
            LIMIT 80
        """
    q_full = """
            SELECT g.id, g.name, g.description, g.created_at, g.created_by, g.image_url,
              COALESCE(g.join_mode, 'approval') AS join_mode,
              g.message_retention_days,
              g.slow_mode_seconds,
              COALESCE(g.show_history_to_new_members, true) AS show_history_to_new_members,
              (SELECT COUNT(*)::bigint FROM community_group_members m WHERE m.group_id = g.id) AS member_count,
              EXISTS(SELECT 1 FROM community_group_members m2 WHERE m2.group_id = g.id AND m2.user_id = :uid) AS is_member,
              (SELECT COUNT(*)::bigint FROM community_group_messages gm WHERE gm.group_id = g.id) AS msg_count,
              EXISTS(
                SELECT 1 FROM community_group_join_requests r
                WHERE r.group_id = g.id AND r.user_id = :uid AND r.status = 'pending'
              ) AS pending_join
            FROM community_groups g
            ORDER BY msg_count DESC NULLS LAST, g.created_at DESC
            LIMIT 80
        """
    q_simple = """
            SELECT g.id, g.name, g.description, g.created_at, g.created_by, g.image_url,
              COALESCE(g.join_mode, 'approval') AS join_mode,
              g.message_retention_days,
              g.slow_mode_seconds,
              COALESCE(g.show_history_to_new_members, true) AS show_history_to_new_members,
              (SELECT COUNT(*)::bigint FROM community_group_members m WHERE m.group_id = g.id) AS member_count,
              EXISTS(SELECT 1 FROM community_group_members m2 WHERE m2.group_id = g.id AND m2.user_id = :uid) AS is_member,
              (SELECT COUNT(*)::bigint FROM community_group_messages gm WHERE gm.group_id = g.id) AS msg_count,
              false AS pending_join
            FROM community_groups g
            ORDER BY msg_count DESC NULLS LAST, g.created_at DESC
            LIMIT 80
        """
    q_minimal = """
            SELECT g.id, g.name, g.description, g.created_at, g.created_by,
              NULL::text AS image_url,
              COALESCE(g.join_mode, 'approval') AS join_mode,
              g.message_retention_days,
              NULL::integer AS slow_mode_seconds,
              true AS show_history_to_new_members,
              (SELECT COUNT(*)::bigint FROM community_group_members m WHERE m.group_id = g.id) AS member_count,
              EXISTS(SELECT 1 FROM community_group_members m2 WHERE m2.group_id = g.id AND m2.user_id = :uid) AS is_member,
              0::bigint AS msg_count,
              false AS pending_join
            FROM community_groups g
            ORDER BY g.created_at DESC
            LIMIT 80
        """
    _no_img = "g.created_by, g.image_url"
    _no_img_rep = "g.created_by, NULL::text AS image_url"
    _slow_col = "g.slow_mode_seconds,"
    _slow_null = "NULL::integer AS slow_mode_seconds,"
    _show_hist_col = "COALESCE(g.show_history_to_new_members, true) AS show_history_to_new_members,"
    _show_hist_true = "true AS show_history_to_new_members,"
    for q in (
        # Compatibility-first order: try schema-safe variants before richer SQL.
        q_rich.replace(_slow_col, _slow_null).replace(_show_hist_col, _show_hist_true),
        q_rich.replace(_no_img, _no_img_rep).replace(_slow_col, _slow_null).replace(_show_hist_col, _show_hist_true),
        q_full.replace(_slow_col, _slow_null).replace(_show_hist_col, _show_hist_true),
        q_full.replace(_no_img, _no_img_rep).replace(_slow_col, _slow_null).replace(_show_hist_col, _show_hist_true),
        q_simple.replace(_slow_col, _slow_null).replace(_show_hist_col, _show_hist_true),
        q_simple.replace(_no_img, _no_img_rep).replace(_slow_col, _slow_null).replace(_show_hist_col, _show_hist_true),
        q_rich.replace(_slow_col, _slow_null),
        q_rich.replace(_show_hist_col, _show_hist_true),
        q_rich.replace(_no_img, _no_img_rep).replace(_slow_col, _slow_null),
        q_rich.replace(_no_img, _no_img_rep).replace(_show_hist_col, _show_hist_true),
        q_full.replace(_slow_col, _slow_null),
        q_full.replace(_show_hist_col, _show_hist_true),
        q_full.replace(_no_img, _no_img_rep).replace(_slow_col, _slow_null),
        q_full.replace(_no_img, _no_img_rep).replace(_show_hist_col, _show_hist_true),
        q_simple.replace(_slow_col, _slow_null),
        q_simple.replace(_show_hist_col, _show_hist_true),
        q_simple.replace(_no_img, _no_img_rep).replace(_slow_col, _slow_null),
        q_simple.replace(_no_img, _no_img_rep).replace(_show_hist_col, _show_hist_true),
        q_rich,
        q_rich.replace(_no_img, _no_img_rep),
        q_full,
        q_full.replace(_no_img, _no_img_rep),
        q_simple,
        q_simple.replace(_no_img, _no_img_rep),
        q_minimal,
    ):
        try:
            rows = await database.fetch_all(sa.text(q).bindparams(uid=uid))
            return _group_rows_to_dicts(rows)
        except Exception as e:
            _logger.warning("community groups query failed, next fallback: %s", e)
    _logger.exception("community groups: all fallbacks failed")
    return []


@router.get("/community/chats")
async def community_chats_legacy_redirect(request: Request):
    """Старая отдельная страница групп убрана из продукта — ведём в ленту сообщества."""
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login?next=/community")
    return RedirectResponse("/community", status_code=302)


@router.get("/community/groups")
async def community_groups_list_api(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    out = await fetch_community_groups_for_user(uid)
    return JSONResponse({"groups": out})


@router.post("/community/groups/create")
async def community_group_create(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required", "ok": False}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    if not is_platform_operator(user):
        return JSONResponse(
            {
                "ok": False,
                "error": "Создание чатов доступно только администратору в админке",
            },
            status_code=403,
        )
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        try:
            body = await request.json()
        except Exception:
            body = {}
        nm = (body.get("name") or "").strip()
        desc_raw = body.get("description")
        desc = (desc_raw or "").strip()[:2000] or None
    else:
        form = await request.form()
        nm = (form.get("name") or "").strip()
        desc = (form.get("description") or "").strip()[:2000] or None
    if len(nm) < 2:
        return JSONResponse({"ok": False, "error": "Слишком короткое название"}, status_code=400)
    if len(nm) > 120:
        return JSONResponse({"ok": False, "error": "Слишком длинное название"}, status_code=400)
    dup = await database.fetch_one(
        sa.text(
            "SELECT id FROM community_groups WHERE LOWER(TRIM(name)) = LOWER(TRIM(:n)) LIMIT 1"
        ).bindparams(n=nm)
    )
    if dup:
        return JSONResponse(
            {"ok": False, "error": "Группа с таким названием уже существует"},
            status_code=400,
        )
    row = None
    try:
        row = await database.fetch_one_write(
            sa.text(
                "INSERT INTO community_groups (name, description, created_by, join_mode) "
                "VALUES (:n, :d, :c, 'open') RETURNING id"
            ).bindparams(n=nm, d=desc, c=uid)
        )
    except Exception as e:
        _logger.warning("community_groups insert with join_mode failed: %s", e)
        try:
            row = await database.fetch_one_write(
                sa.text(
                    "INSERT INTO community_groups (name, description, created_by) "
                    "VALUES (:n, :d, :c) RETURNING id"
                ).bindparams(n=nm, d=desc, c=uid)
            )
        except Exception as e2:
            _logger.exception("community_groups insert failed")
            return JSONResponse(
                {"ok": False, "error": "Не удалось сохранить группу: " + str(e2)[:180]},
                status_code=500,
            )
    gid = None
    if row:
        rid = row.get("id")
        if rid is None:
            rid = next((row[k] for k in row if str(k).lower() == "id"), None)
        try:
            gid = int(rid) if rid is not None else None
        except (TypeError, ValueError):
            gid = None
    if not gid:
        return JSONResponse({"ok": False, "error": "Группа не создана"}, status_code=500)
    try:
        await database.execute(
            sa.text(
                "INSERT INTO community_group_members (group_id, user_id) VALUES (:gid, :uid) "
                "ON CONFLICT (group_id, user_id) DO NOTHING"
            ).bindparams(gid=gid, uid=uid)
        )
    except Exception as e:
        _logger.warning("community_group_members insert: %s", e)
    if not await _user_in_community_group(gid, uid):
        _logger.error("creator not in members after create gid=%s uid=%s", gid, uid)
    return JSONResponse({"ok": True, "id": gid})


@router.post("/community/groups/{group_id}/join")
async def community_group_join(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    g = await fetch_community_group_row(group_id)
    if not g:
        return JSONResponse({"error": "not found"}, status_code=404)
    ban = await _group_user_ban_status(group_id, uid)
    if ban:
        if ban.get("is_permanent"):
            return JSONResponse({"error": "Вы заблокированы в этом чате навсегда"}, status_code=403)
        bu = ban.get("banned_until")
        if bu:
            return JSONResponse({"error": f"Вы временно заблокированы до {bu:%d.%m.%Y %H:%M}"}, status_code=403)
    mode = (g.get("join_mode") or "approval").lower()
    if await _user_in_community_group(group_id, uid):
        return JSONResponse({"ok": True, "member": True})
    if mode == "open":
        try:
            await database.execute(
                community_group_members.insert().values(group_id=group_id, user_id=uid)
            )
        except Exception:
            pass
        return JSONResponse({"ok": True, "member": True})
    # approval: заявка владельцу
    existing = await database.fetch_one(
        community_group_join_requests.select()
        .where(community_group_join_requests.c.group_id == group_id)
        .where(community_group_join_requests.c.user_id == uid)
    )
    if existing and existing.get("status") == "pending":
        return JSONResponse({"ok": True, "pending": True})
    if existing and existing.get("status") == "rejected":
        await database.execute(
            community_group_join_requests.update()
            .where(community_group_join_requests.c.id == existing["id"])
            .values(status="pending")
        )
        return JSONResponse({"ok": True, "pending": True})
    try:
        await database.execute(
            community_group_join_requests.insert().values(group_id=group_id, user_id=uid, status="pending")
        )
    except Exception:
        pass
    return JSONResponse({"ok": True, "pending": True})


@router.post("/community/groups/{group_id}/leave")
async def community_group_leave(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    await database.execute(
        community_group_members.delete()
        .where(community_group_members.c.group_id == group_id)
        .where(community_group_members.c.user_id == uid)
    )
    return JSONResponse({"ok": True})


@router.post("/community/groups/{group_id}/notifications")
async def community_group_notifications_toggle(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    enabled = bool(body.get("enabled", True))
    await database.execute(
        community_group_members.update()
        .where(community_group_members.c.group_id == group_id)
        .where(community_group_members.c.user_id == uid)
        .values(notifications_enabled=enabled)
    )
    return JSONResponse({"ok": True, "enabled": enabled})


@router.get("/community/groups/{group_id}/participants")
async def community_group_participants(request: Request, group_id: int, q: str = ""):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    sq = (q or "").strip()
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT u.id, u.name, u.avatar
            FROM community_group_members m
            JOIN users u ON u.id = m.user_id
            WHERE m.group_id = :gid
              AND (:q = '' OR LOWER(COALESCE(u.name,'')) LIKE LOWER(:likeq))
            ORDER BY LOWER(COALESCE(u.name,'')) ASC
            LIMIT 100
            """
        ).bindparams(gid=group_id, q=sq, likeq=f"%{sq}%")
    )
    return JSONResponse({"ok": True, "participants": [dict(r) for r in rows]})


@router.get("/community/groups/{group_id}/join-requests")
async def community_group_join_requests_list(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    g = await fetch_community_group_row(group_id)
    if not g:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _can_manage_community_group(g, user, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    rows = await database.fetch_all(
        sa.text("""
            SELECT r.id, r.user_id, r.status, r.created_at, u.name AS user_name
            FROM community_group_join_requests r
            JOIN users u ON u.id = r.user_id
            WHERE r.group_id = :gid AND r.status = 'pending'
            ORDER BY r.created_at ASC
        """).bindparams(gid=group_id)
    )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].strftime("%d.%m.%Y %H:%M")
        out.append(d)
    return JSONResponse({"requests": out})


@router.post("/community/groups/{group_id}/join-requests/{request_id}/approve")
async def community_group_join_approve(request: Request, group_id: int, request_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    g = await fetch_community_group_row(group_id)
    if not g:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _can_manage_community_group(g, user, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    row = await database.fetch_one(
        community_group_join_requests.select()
        .where(community_group_join_requests.c.id == request_id)
        .where(community_group_join_requests.c.group_id == group_id)
    )
    if not row or row.get("status") != "pending":
        return JSONResponse({"error": "not found"}, status_code=404)
    new_uid = row["user_id"]
    await database.execute(
        community_group_join_requests.update()
        .where(community_group_join_requests.c.id == request_id)
        .values(status="approved")
    )
    try:
        await database.execute(
            community_group_members.insert().values(group_id=group_id, user_id=new_uid)
        )
    except Exception:
        pass
    return JSONResponse({"ok": True})


@router.post("/community/groups/{group_id}/join-requests/{request_id}/reject")
async def community_group_join_reject(request: Request, group_id: int, request_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    g = await fetch_community_group_row(group_id)
    if not g:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _can_manage_community_group(g, user, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        community_group_join_requests.update()
        .where(community_group_join_requests.c.id == request_id)
        .where(community_group_join_requests.c.group_id == group_id)
        .values(status="rejected")
    )
    return JSONResponse({"ok": True})


@router.post("/community/groups/{group_id}/settings")
async def community_group_settings(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    g = await fetch_community_group_row(group_id)
    if not g:
        _logger.warning("community_group_settings: group not found id=%s uid=%s", group_id, uid)
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    is_op = _can_manage_community_group(g, user, uid)
    if not is_op:
        body = {k: body[k] for k in body if k == "image_url"}
    vals = {}
    if is_op:
        if "message_retention_days" in body:
            v = body.get("message_retention_days")
            if v is None or v == "":
                vals["message_retention_days"] = None
            else:
                try:
                    n = int(v)
                    if n < 1:
                        n = 1
                    if n > 36500:
                        n = 36500
                    vals["message_retention_days"] = n
                except (TypeError, ValueError):
                    pass
        if "join_mode" in body:
            jm = (body.get("join_mode") or "").strip().lower()
            if jm in ("open", "approval"):
                vals["join_mode"] = jm
        if "slow_mode_seconds" in body:
            v = body.get("slow_mode_seconds")
            if v is None or v == "":
                vals["slow_mode_seconds"] = None
            else:
                try:
                    n = int(v)
                    if n < 0:
                        n = 0
                    if n > 86400:
                        n = 86400
                    vals["slow_mode_seconds"] = n if n > 0 else None
                except (TypeError, ValueError):
                    pass
        if "show_history_to_new_members" in body:
            vals["show_history_to_new_members"] = bool(body.get("show_history_to_new_members"))
    if "image_url" in body:
        if not _can_edit_group_image(g, user, uid):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        url = (body.get("image_url") or "").strip()
        if url == "":
            vals["image_url"] = None
        elif len(url) <= 2000 and (
            url.startswith("http://")
            or url.startswith("https://")
            or url.startswith("/media/")
            or url.startswith("/static/")
        ):
            vals["image_url"] = url
    if vals:
        try:
            await database.execute(
                community_groups.update().where(community_groups.c.id == group_id).values(**vals)
            )
        except Exception as e:
            _logger.exception("community_group_settings update failed")
            return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)
    return JSONResponse({"ok": True})


@router.post("/community/groups/{group_id}/mark-read")
async def community_group_mark_read(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    try:
        await database.execute(
            sa.text(
                "UPDATE community_group_members "
                "SET last_read_at = NOW(), chat_last_seen_at = NOW(), addressed_last_read_at = NOW() "
                "WHERE group_id = :gid AND user_id = :uid"
            ).bindparams(gid=group_id, uid=uid)
        )
    except Exception as e:
        _logger.warning("community_group mark-read: %s", e)
        return JSONResponse({"ok": False, "error": "db"}, status_code=500)
    return JSONResponse({"ok": True})


_GROUP_IMG_ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_GROUP_IMG_MAX = 6 * 1024 * 1024


@router.post("/community/groups/{group_id}/upload-image")
async def community_group_upload_image(request: Request, group_id: int, file: UploadFile = File(...)):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    g = await fetch_community_group_row(group_id)
    if not g:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if not _can_edit_group_image(g, user, uid):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    ct = (file.content_type or "").lower()
    if ct not in _GROUP_IMG_ALLOWED:
        return JSONResponse({"ok": False, "error": "Нужен JPEG, PNG, WebP или GIF"}, status_code=400)
    data = await file.read()
    if len(data) > _GROUP_IMG_MAX:
        return JSONResponse({"ok": False, "error": "Файл слишком большой (макс. 6 МБ)"}, status_code=400)
    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = "jpg"
    filename = f"g{group_id}_{uuid.uuid4().hex}.{ext}"
    base = "/data" if os.path.exists("/data") else "./media"
    save_path = os.path.join(base, "community", "groups", filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(data)
    image_url = f"/media/community/groups/{filename}"
    try:
        await database.execute(
            community_groups.update().where(community_groups.c.id == group_id).values(image_url=image_url)
        )
    except Exception as e:
        _logger.exception("community_group image update failed")
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)
    return JSONResponse({"ok": True, "image_url": image_url})


async def _group_slow_mode_block(group_id: int, uid: int, g_row) -> Optional[JSONResponse]:
    sm = g_row.get("slow_mode_seconds") if g_row is not None else None
    try:
        sm_int = int(sm) if sm is not None else 0
    except (TypeError, ValueError):
        sm_int = 0
    if sm_int <= 0:
        return None
    last = await database.fetch_one(
        sa.text(
            "SELECT created_at FROM community_group_messages "
            "WHERE group_id = :gid AND sender_id = :uid "
            "ORDER BY created_at DESC LIMIT 1"
        ).bindparams(gid=group_id, uid=uid)
    )
    if last and last.get("created_at"):
        ca = last["created_at"]
        if isinstance(ca, datetime):
            now = datetime.utcnow()
            ca_naive = ca.replace(tzinfo=None) if getattr(ca, "tzinfo", None) else ca
            elapsed = (now - ca_naive).total_seconds()
            if elapsed < sm_int:
                wait = max(1, int(sm_int - elapsed))
                return JSONResponse(
                    {"error": "slow_mode", "wait_sec": wait, "ok": False},
                    status_code=429,
                )
    return None


@router.get("/community/groups/{group_id}/messages")
async def community_group_messages_get(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    await _cleanup_group_messages_by_policy(group_id)
    g_row = await fetch_community_group_row(group_id)
    mem_row = await database.fetch_one(
        community_group_members.select()
        .where(community_group_members.c.group_id == group_id)
        .where(community_group_members.c.user_id == uid)
    )
    if mem_row:
        try:
            await database.execute(
                community_group_members.update()
                .where(community_group_members.c.group_id == group_id)
                .where(community_group_members.c.user_id == uid)
                .values(chat_last_seen_at=datetime.utcnow())
            )
        except Exception:
            pass
    show_hist = True
    if g_row and g_row.get("show_history_to_new_members") is not None:
        show_hist = bool(g_row["show_history_to_new_members"])
    cutoff = None
    if not show_hist and mem_row and mem_row.get("joined_at"):
        cutoff = mem_row["joined_at"]
    q = (
        community_group_messages.select()
        .where(community_group_messages.c.group_id == group_id)
    )
    if cutoff is not None:
        q = q.where(community_group_messages.c.created_at >= cutoff)
    rows = await database.fetch_all(
        q.order_by(community_group_messages.c.created_at.asc()).limit(200)
    )
    is_admin = user.get("role") == "admin"
    is_group_owner = False
    if g_row and g_row.get("created_by") is not None:
        try:
            is_group_owner = int(g_row["created_by"]) == int(uid)
        except (TypeError, ValueError):
            is_group_owner = False
    can_mod = is_admin or is_platform_operator(user) or is_group_owner
    ids = [r["id"] for r in rows]
    snd_ids = {r["sender_id"] for r in rows if r.get("sender_id")}
    name_by_id: dict = {}
    avatar_by_id: dict = {}
    if snd_ids:
        urows = await database.fetch_all(users.select().where(users.c.id.in_(snd_ids)))
        for u in urows:
            name_by_id[u["id"]] = u["name"] or "Участник"
            avatar_by_id[u["id"]] = u.get("avatar")

    likes_map: dict = {}
    like_users_map: dict[int, list[dict]] = {}
    liked_set: set = set()
    if ids:
        try:
            lc = await database.fetch_all(
                sa.select(
                    community_group_message_likes.c.message_id,
                    sa.func.count().label("c"),
                )
                .where(community_group_message_likes.c.message_id.in_(ids))
                .group_by(community_group_message_likes.c.message_id)
            )
            for row in lc:
                likes_map[row["message_id"]] = int(row["c"])
            lk = await database.fetch_all(
                sa.select(community_group_message_likes.c.message_id)
                .where(community_group_message_likes.c.message_id.in_(ids))
                .where(community_group_message_likes.c.user_id == uid)
            )
            liked_set = {x["message_id"] for x in lk}
            lusers = await database.fetch_all(
                sa.select(
                    community_group_message_likes.c.message_id,
                    users.c.id.label("user_id"),
                    users.c.name,
                    users.c.avatar,
                )
                .select_from(
                    community_group_message_likes.join(
                        users, users.c.id == community_group_message_likes.c.user_id
                    )
                )
                .where(community_group_message_likes.c.message_id.in_(ids))
                .order_by(community_group_message_likes.c.created_at.desc())
            )
            for ru in lusers:
                mid = int(ru["message_id"])
                like_users_map.setdefault(mid, [])
                if len(like_users_map[mid]) < 6:
                    like_users_map[mid].append({
                        "id": int(ru["user_id"]),
                        "name": ru.get("name") or "Участник",
                        "avatar": ru.get("avatar"),
                    })
        except Exception:
            _logger.exception("group message likes fetch")

    reply_ids = {r["reply_to_id"] for r in rows if r.get("reply_to_id")}
    parent_by_id: dict = {}
    if reply_ids:
        parents = await database.fetch_all(
            community_group_messages.select().where(community_group_messages.c.id.in_(reply_ids))
        )
        for pr in parents:
            parent_by_id[pr["id"]] = pr
    parent_uids = {parent_by_id[rid]["sender_id"] for rid in reply_ids if rid in parent_by_id and parent_by_id[rid].get("sender_id")}
    parent_names: dict = {}
    if parent_uids:
        pu = await database.fetch_all(users.select().where(users.c.id.in_(parent_uids)))
        for u in pu:
            parent_names[u["id"]] = u["name"] or "Участник"

    out = []
    for r in rows:
        snd = r["sender_id"]
        uname = name_by_id.get(snd, "Участник") if snd else "Участник"
        reply_to = None
        rid = r.get("reply_to_id")
        if rid and rid in parent_by_id:
            pr = parent_by_id[rid]
            ps = pr.get("sender_id")
            pn = parent_names.get(ps, "Участник") if ps else "Участник"
            pv = (pr.get("text") or "").strip().replace("\n", " ")[:140]
            if len((pr.get("text") or "")) > 140:
                pv += "…"
            if not pv:
                if pr.get("image_url"):
                    pv = "📷"
                elif pr.get("audio_url"):
                    pv = "🎤"
                else:
                    pv = "…"
            reply_to = {"id": pr["id"], "sender_name": pn, "preview": pv}
        out.append({
            "id": r["id"],
            "sender_id": snd,
            "sender_name": uname,
            "sender_avatar": avatar_by_id.get(snd),
            "text": r.get("text") or "",
            "image_url": r.get("image_url"),
            "audio_url": r.get("audio_url"),
            "reply_to": reply_to,
            "is_mine": snd == uid,
            "can_delete": (snd == uid) or can_mod,
            "likes_count": likes_map.get(r["id"], 0),
            "liked": r["id"] in liked_set,
            "liked_users": like_users_map.get(r["id"], []),
            "addressed_user_id": r.get("addressed_user_id"),
            "created_at": r["created_at"].strftime("%d.%m %H:%M") if r["created_at"] else "",
        })
    addressed_unread = await database.fetch_val(
        sa.text(
            """
            SELECT COUNT(*)::bigint
            FROM community_group_messages gm
            JOIN community_group_members m ON m.group_id = gm.group_id AND m.user_id = :uid
            WHERE gm.group_id = :gid
              AND gm.addressed_user_id = :uid
              AND gm.created_at > COALESCE(m.addressed_last_read_at, m.joined_at)
            """
        ).bindparams(gid=group_id, uid=uid)
    ) or 0
    typing_rows = await database.fetch_all(
        sa.text(
            """
            SELECT u.name
            FROM community_group_typing_status t
            JOIN users u ON u.id = t.user_id
            WHERE t.group_id = :gid
              AND t.user_id != :uid
              AND t.updated_at > (NOW() - INTERVAL '6 seconds')
            ORDER BY t.updated_at DESC
            LIMIT 3
            """
        ).bindparams(gid=group_id, uid=uid)
    )
    typing_names = [str(r.get("name") or "Участник") for r in typing_rows]
    return JSONResponse({
        "messages": out,
        "group": {
            "pinned_message_text": (g_row or {}).get("pinned_message_text") if g_row else None,
            "allow_photo": bool((g_row or {}).get("allow_photo", True)),
            "allow_audio": bool((g_row or {}).get("allow_audio", True)),
            "notifications_enabled": bool((mem_row or {}).get("notifications_enabled", True)),
            "addressed_unread_count": int(addressed_unread),
            "typing_users": typing_names,
        },
    })


@router.post("/community/groups/{group_id}/typing")
async def community_group_typing_ping(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    try:
        await database.execute(
            sa.text(
                """
                INSERT INTO community_group_typing_status (group_id, user_id, updated_at)
                VALUES (:gid, :uid, NOW())
                ON CONFLICT (group_id, user_id)
                DO UPDATE SET updated_at = EXCLUDED.updated_at
                """
            ).bindparams(gid=group_id, uid=uid)
        )
    except Exception:
        pass
    return JSONResponse({"ok": True})


@router.post("/community/groups/{group_id}/message")
async def community_group_message_post(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    ban = await _group_user_ban_status(group_id, uid)
    if ban:
        return JSONResponse({"error": "forbidden by moderation"}, status_code=403)
    perms = await _group_member_permissions(group_id, uid)
    if not perms.get("can_send_messages", True):
        return JSONResponse({"error": "sending disabled for member"}, status_code=403)

    ct = (request.headers.get("content-type") or "").lower()
    text = ""
    reply_to = None
    addressed_user_id = None
    image_url = None
    audio_url = None

    if "multipart/form-data" in ct:
        form = await request.form()
        text = (form.get("text") or "").strip()
        rid_raw = form.get("reply_to_id")
        addr_raw = form.get("addressed_user_id")
        if rid_raw not in (None, ""):
            try:
                rti = int(rid_raw)
                if rti > 0:
                    reply_to = rti
            except (TypeError, ValueError):
                pass
        if addr_raw not in (None, ""):
            try:
                au = int(addr_raw)
                if au > 0:
                    addressed_user_id = au
            except (TypeError, ValueError):
                pass
        img_f = form.get("image")
        aud_f = form.get("audio")
        g_settings = await fetch_community_group_row(group_id)
        if img_f and getattr(img_f, "read", None):
            if g_settings and g_settings.get("allow_photo") is False:
                return JSONResponse({"error": "photo disabled in chat"}, status_code=403)
            if not perms.get("can_send_photo", True):
                return JSONResponse({"error": "photo disabled for member"}, status_code=403)
            ict = (getattr(img_f, "content_type", None) or "").lower()
            if ict not in _POST_IMAGE_ALLOWED:
                return JSONResponse({"error": "Нужен JPEG, PNG, WebP или GIF"}, status_code=400)
            raw = await img_f.read()
            if len(raw) > _GROUP_CHAT_IMG_MAX:
                return JSONResponse({"error": "Фото слишком большое"}, status_code=400)
            try:
                jpeg = _compress_group_chat_image(raw)
            except Exception:
                return JSONResponse({"error": "Не удалось обработать фото"}, status_code=400)
            fn = f"m{uuid.uuid4().hex}.jpg"
            base = "/data" if os.path.exists("/data") else "./media"
            path = os.path.join(base, "community", "groups", "msg", fn)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(jpeg)
            image_url = f"/media/community/groups/msg/{fn}"
        if aud_f and getattr(aud_f, "read", None):
            if g_settings and g_settings.get("allow_audio") is False:
                return JSONResponse({"error": "audio disabled in chat"}, status_code=403)
            if not perms.get("can_send_audio", True):
                return JSONResponse({"error": "audio disabled for member"}, status_code=403)
            fnm = getattr(aud_f, "filename", None) or ""
            act = (getattr(aud_f, "content_type", None) or "").lower()
            if not _group_chat_audio_upload_ok(act, fnm):
                return JSONResponse({"error": "Неподдерживаемый формат аудио"}, status_code=400)
            raw = await aud_f.read()
            if len(raw) > _GROUP_CHAT_AUDIO_MAX:
                return JSONResponse({"error": "Аудио слишком большое (макс. ~1 мин)"}, status_code=400)
            ext = "webm"
            if "." in fnm:
                e = fnm.rsplit(".", 1)[-1].lower()[:8]
                if e in ("webm", "ogg", "mp3", "m4a", "wav", "mpeg", "mp4", "aac", "opus"):
                    ext = e
            fn = f"a{uuid.uuid4().hex}.{ext}"
            base = "/data" if os.path.exists("/data") else "./media"
            path = os.path.join(base, "community", "groups", "msg", fn)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
            audio_url = f"/media/community/groups/msg/{fn}"
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        text = (body.get("text") or "").strip()
        rid = body.get("reply_to_id")
        aud = body.get("addressed_user_id")
        if rid is not None:
            try:
                rti = int(rid)
                if rti > 0:
                    reply_to = rti
            except (TypeError, ValueError):
                pass
        if aud is not None:
            try:
                au = int(aud)
                if au > 0:
                    addressed_user_id = au
            except (TypeError, ValueError):
                pass

    if len(text) > 8000:
        return JSONResponse({"error": "bad text"}, status_code=400)
    if not text and not image_url and not audio_url:
        return JSONResponse({"error": "empty"}, status_code=400)

    if reply_to:
        pr = await database.fetch_one(
            community_group_messages.select()
            .where(community_group_messages.c.id == reply_to)
            .where(community_group_messages.c.group_id == group_id)
        )
        if not pr:
            return JSONResponse({"error": "bad reply"}, status_code=400)
    if addressed_user_id is not None:
        target_member = await database.fetch_one(
            community_group_members.select()
            .where(community_group_members.c.group_id == group_id)
            .where(community_group_members.c.user_id == addressed_user_id)
        )
        if not target_member:
            return JSONResponse({"error": "target is not member of chat"}, status_code=400)

    g_row = await fetch_community_group_row(group_id)
    sm_block = await _group_slow_mode_block(group_id, uid, g_row)
    if sm_block is not None:
        return sm_block

    msg_row = await database.fetch_one_write(
        community_group_messages.insert()
        .values(
            group_id=group_id,
            sender_id=uid,
            text=text or "",
            reply_to_id=reply_to,
            addressed_user_id=addressed_user_id,
            image_url=image_url,
            audio_url=audio_url,
        )
        .returning(community_group_messages.c.id)
    )
    new_msg_id = int(msg_row["id"]) if msg_row else None
    if new_msg_id:
        try:
            gname = (g_row or {}).get("name") or f"Группа #{group_id}"
            srow = await database.fetch_one(users.select().where(users.c.id == uid))
            sname = (srow.get("name") if srow else None) or "Участник"
            snippet = (text or "").strip()
            if not snippet and image_url:
                snippet = "[фото]"
            elif not snippet and audio_url:
                snippet = "[аудио]"
            mem_rows = await database.fetch_all(
                community_group_members.select().where(community_group_members.c.group_id == group_id)
            )
            member_ids = {int(m["user_id"]) for m in mem_rows}
            for mem in mem_rows:
                rid = int(mem["user_id"])
                if rid == uid:
                    continue
                addr_note = ""
                if addressed_user_id and rid == int(addressed_user_id):
                    addr_note = " · адресно вам"
                body_line = f"«{gname}» — {sname}: {(snippet or 'сообщение')[:300]}{addr_note}"
                await create_notification(
                    recipient_id=rid,
                    actor_id=uid,
                    ntype="group_post",
                    title="Сообщение в группе",
                    body=body_line,
                    link_url="/dashboard/user",
                    source_kind="community_group_message",
                    source_id=new_msg_id,
                )
                if not mem.get("notifications_enabled", True):
                    continue
                online_in_chat = False
                if mem.get("chat_last_seen_at"):
                    try:
                        ls = mem["chat_last_seen_at"]
                        ls = ls.replace(tzinfo=None) if getattr(ls, "tzinfo", None) else ls
                        online_in_chat = (datetime.utcnow() - ls) <= timedelta(seconds=90)
                    except Exception:
                        online_in_chat = False
                if not online_in_chat:
                    tg_body = body_line.replace(" · адресно вам", "")
                    await send_event_telegram_html(
                        rid,
                        "group_post",
                        "Сообщение в группе",
                        tg_body,
                        "/dashboard/user",
                    )
            mentioned_sent: set[int] = set()
            for mid in extract_mentioned_numeric_ids(text or ""):
                if mid == uid or mid in mentioned_sent:
                    continue
                if mid not in member_ids:
                    continue
                if not await user_exists(mid):
                    continue
                mentioned_sent.add(mid)
                await create_notification(
                    recipient_id=mid,
                    actor_id=uid,
                    ntype="mention",
                    title="Вас упомянули в группе",
                    body=f"{sname} в «{gname}»: {(snippet or 'сообщение')[:350]}",
                    link_url="/dashboard/user",
                    source_kind="mention_group_message",
                    source_id=new_msg_id,
                )
                await send_event_telegram_html(
                    mid,
                    "mention",
                    "Упоминание в группе",
                    f"{sname}: {(snippet or 'сообщение')[:320]}",
                    "/dashboard/user",
                )
        except Exception:
            pass
    return JSONResponse({"ok": True})


@router.post("/community/groups/{group_id}/messages/{message_id}/like")
async def community_group_message_like_toggle(request: Request, group_id: int, message_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    msg = await database.fetch_one(
        community_group_messages.select()
        .where(community_group_messages.c.id == message_id)
        .where(community_group_messages.c.group_id == group_id)
    )
    if not msg:
        return JSONResponse({"error": "not found"}, status_code=404)
    existing = await database.fetch_one(
        community_group_message_likes.select()
        .where(community_group_message_likes.c.message_id == message_id)
        .where(community_group_message_likes.c.user_id == uid)
    )
    if existing:
        await database.execute(
            community_group_message_likes.delete()
            .where(community_group_message_likes.c.message_id == message_id)
            .where(community_group_message_likes.c.user_id == uid)
        )
        liked = False
    else:
        await database.execute(
            community_group_message_likes.insert().values(message_id=message_id, user_id=uid)
        )
        liked = True
    cnt = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(community_group_message_likes)
        .where(community_group_message_likes.c.message_id == message_id)
    ) or 0
    return JSONResponse({"ok": True, "liked": liked, "likes_count": int(cnt)})


@router.delete("/community/groups/{group_id}/messages/{message_id}")
async def community_group_message_delete(request: Request, group_id: int, message_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rej = await _reject_if_group_chats_forbidden(user)
    if rej:
        return rej
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    msg = await database.fetch_one(
        community_group_messages.select()
        .where(community_group_messages.c.id == message_id)
        .where(community_group_messages.c.group_id == group_id)
    )
    if not msg:
        return JSONResponse({"error": "not found"}, status_code=404)
    g_row = await fetch_community_group_row(group_id)
    is_admin = user.get("role") == "admin"
    is_group_owner = False
    if g_row and g_row.get("created_by") is not None:
        try:
            is_group_owner = int(g_row["created_by"]) == int(uid)
        except (TypeError, ValueError):
            is_group_owner = False
    can_mod = is_admin or is_platform_operator(user) or is_group_owner
    if msg["sender_id"] != uid and not can_mod:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        community_group_messages.delete()
        .where(community_group_messages.c.id == message_id)
        .where(community_group_messages.c.group_id == group_id)
    )
    return JSONResponse({"ok": True})


async def _delete_community_post_and_related(post_id: int) -> None:
    await database.execute(community_likes.delete().where(community_likes.c.post_id == post_id))
    await database.execute(community_comments.delete().where(community_comments.c.post_id == post_id))
    await database.execute(community_saved.delete().where(community_saved.c.post_id == post_id))
    await database.execute(community_posts.delete().where(community_posts.c.id == post_id))


@router.delete("/community/post/{post_id}")
async def delete_community_post_user(request: Request, post_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not await _can_manage_community_post(user, post):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await _delete_community_post_and_related(post_id)
    return JSONResponse({"ok": True})


async def _owner_family_user_ids(uid: int) -> list[int]:
    rows = await database.fetch_all(
        users.select().with_only_columns(users.c.id).where(
            sa.or_(users.c.id == uid, users.c.primary_user_id == uid)
        )
    )
    return sorted({int(r["id"]) for r in rows} | {int(uid)})


@router.get("/community/activity/unread-count")
async def community_activity_unread_count(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse(
            {"likes": 0, "comments": 0, "profile_likes": 0, "messages": 0, "total": 0}
        )
    uid = user.get("primary_user_id") or user["id"]
    n_events = n_msg = 0
    try:
        n_events = await count_unread_events(int(uid))
        # ЛС: новый мессенджер + legacy без дублей (одно сообщение не считается дважды)
        n_msg = await count_standalone_direct_unread(int(uid)) + await count_chat_unread(int(uid))
    except Exception:
        pass
    activity_total = int(n_events)
    tot = activity_total + int(n_msg)
    return JSONResponse(
        {
            "likes": 0,
            "comments": 0,
            "profile_likes": 0,
            "messages": int(n_msg),
            "notifications": 0,
            "activity_total": activity_total,
            "total": tot,
        }
    )


@router.post("/community/activity/mark-read")
async def community_activity_mark_read(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    fam = await _owner_family_user_ids(uid)
    subq = sa.select(community_posts.c.id).where(community_posts.c.user_id.in_(fam))
    try:
        await database.execute(
            community_likes.update()
            .where(community_likes.c.post_id.in_(subq))
            .values(seen_by_post_owner=True)
        )
        await database.execute(
            community_comments.update()
            .where(community_comments.c.post_id.in_(subq))
            .values(seen_by_post_owner=True)
        )
        await database.execute(
            profile_likes.update()
            .where(profile_likes.c.liked_user_id == uid)
            .values(seen_by_owner=True)
        )
        await mark_events_notifications_read(int(uid))
    except Exception:
        pass
    return JSONResponse({"ok": True})


@router.get("/community/activity/feed")
async def community_activity_feed(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    fam = await _owner_family_user_ids(uid)
    subq_posts = sa.select(community_posts.c.id).where(community_posts.c.user_id.in_(fam))
    items: list[dict] = []

    try:
        like_rows = await database.fetch_all(
            sa.select(
                community_likes.c.id,
                community_likes.c.post_id,
                community_likes.c.user_id,
                community_likes.c.created_at,
                users.c.name,
                users.c.avatar,
            )
            .select_from(
                community_likes.join(users, users.c.id == community_likes.c.user_id).join(
                    community_posts, community_posts.c.id == community_likes.c.post_id
                )
            )
            .where(community_likes.c.post_id.in_(subq_posts))
            .where(community_likes.c.seen_by_post_owner.is_(False))
            .where(community_likes.c.user_id != uid)
            .order_by(community_likes.c.id.desc())
            .limit(25)
        )
        for r in like_rows:
            items.append(
                {
                    "type": "post_like",
                    "id": r["id"],
                    "post_id": r["post_id"],
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else "",
                    "actor": {
                        "id": r["user_id"],
                        "name": r.get("name") or "Участник",
                        "avatar": r.get("avatar"),
                    },
                }
            )

        c_rows = await database.fetch_all(
            sa.select(
                community_comments.c.id,
                community_comments.c.post_id,
                community_comments.c.user_id,
                community_comments.c.content,
                community_comments.c.created_at,
                users.c.name,
                users.c.avatar,
            )
            .select_from(
                community_comments.join(users, users.c.id == community_comments.c.user_id)
            )
            .where(community_comments.c.post_id.in_(subq_posts))
            .where(community_comments.c.seen_by_post_owner.is_(False))
            .where(community_comments.c.user_id != uid)
            .order_by(community_comments.c.id.desc())
            .limit(25)
        )
        for r in c_rows:
            txt = (r.get("content") or "").strip()
            if len(txt) > 140:
                txt = txt[:137] + "…"
            items.append(
                {
                    "type": "comment",
                    "id": r["id"],
                    "post_id": r["post_id"],
                    "snippet": txt,
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else "",
                    "actor": {
                        "id": r["user_id"],
                        "name": r.get("name") or "Участник",
                        "avatar": r.get("avatar"),
                    },
                }
            )

        pl_rows = await database.fetch_all(
            sa.select(
                profile_likes.c.id,
                profile_likes.c.user_id,
                profile_likes.c.created_at,
                users.c.name,
                users.c.avatar,
            )
            .select_from(profile_likes.join(users, users.c.id == profile_likes.c.user_id))
            .where(profile_likes.c.liked_user_id == uid)
            .where(profile_likes.c.seen_by_owner.is_(False))
            .order_by(profile_likes.c.id.desc())
            .limit(20)
        )
        for r in pl_rows:
            items.append(
                {
                    "type": "profile_like",
                    "id": r["id"],
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else "",
                    "actor": {
                        "id": r["user_id"],
                        "name": r.get("name") or "Участник",
                        "avatar": r.get("avatar"),
                    },
                }
            )

        dm_rows = await database.fetch_all(
            sa.select(
                direct_messages.c.id,
                direct_messages.c.sender_id,
                direct_messages.c.text,
                direct_messages.c.created_at,
                users.c.name,
                users.c.avatar,
            )
            .select_from(direct_messages.join(users, users.c.id == direct_messages.c.sender_id))
            .where(direct_messages.c.recipient_id == uid)
            .where(direct_messages.c.is_read.is_(False))
            .where(direct_messages.c.is_system.is_(False))
            .order_by(direct_messages.c.id.desc())
            .limit(15)
        )
        for r in dm_rows:
            tx = (r.get("text") or "").strip()
            if len(tx) > 120:
                tx = tx[:117] + "…"
            items.append(
                {
                    "type": "message",
                    "id": r["id"],
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else "",
                    "text_preview": tx,
                    "actor": {
                        "id": r["sender_id"],
                        "name": r.get("name") or "Участник",
                        "avatar": r.get("avatar"),
                    },
                }
            )
    except Exception:
        pass

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return JSONResponse({"ok": True, "items": items[:40]})
