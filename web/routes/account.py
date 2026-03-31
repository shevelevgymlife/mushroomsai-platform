import json
import logging
import secrets
import urllib.parse
from web.profile_ui_themes import PROFILE_UI_THEMES, PROFILE_UI_THEME_IDS, MAX_PROFILE_CIRCLES_ACCOUNT
from services.profile_public_cards import merge_profile_public_cards, profile_public_cards_from_form, SOCIAL_KEYS
from services.in_app_notifications import merge_prefs as merge_notification_prefs
from datetime import datetime, timedelta
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from web.templates_utils import Jinja2Templates
from starlette.responses import JSONResponse
from auth.session import get_user_from_request, attach_subscription_effective
from services.legal import legal_acceptance_redirect
from services.subscription_service import claim_start_trial, web_default_home_path, check_subscription
from auth.telegram_auth import verify_telegram_auth
from auth.ui_prefs import DEFAULT_SCREEN_RIM, attach_screen_rim_prefs
from db.database import database
from db.models import (
    users,
    messages,
    posts,
    leads,
    orders,
    subscriptions,
    referrals,
    referral_withdrawals,
    followups,
    page_views,
    feedback,
    community_posts,
    community_comments,
    community_likes,
    community_saved,
    community_follows,
    community_messages,
    profile_likes,
    direct_messages,
    product_reviews,
    shop_product_comments,
    product_questions,
    shop_market_orders,
    community_groups,
    community_folders,
    support_message_deliveries,
    community_profiles,
    admin_permissions,
    training_bot_operators,
    user_block_overrides,
    shop_product_likes,
    shop_cart_items,
    community_group_join_requests,
    community_group_members,
    community_group_member_permissions,
    community_group_member_bans,
    community_group_typing_status,
    community_group_message_likes,
    community_group_messages,
    wellness_journal_entries,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account")
templates = Jinja2Templates(directory="web/templates")


async def _resolve_primary_row(user_id: int) -> Optional[dict]:
    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not row:
        return None
    resolved = dict(row)
    seen: set[int] = set()
    while resolved.get("primary_user_id"):
        pid = int(resolved["primary_user_id"])
        if pid in seen:
            break
        seen.add(pid)
        p = await database.fetch_one(users.select().where(users.c.id == pid))
        if not p:
            break
        resolved = dict(p)
    return resolved


async def find_user_by_telegram_id(tg_id: int) -> Optional[dict]:
    rows = await database.fetch_all(
        users.select().where(
            sa.or_(users.c.tg_id == tg_id, users.c.linked_tg_id == tg_id)
        )
    )
    if not rows:
        return None
    # Prefer already-primary rows if multiple matches exist.
    chosen = None
    for row in rows:
        r = dict(row)
        if not r.get("primary_user_id"):
            chosen = r
            break
    if chosen is None:
        chosen = dict(rows[0])
    return await _resolve_primary_row(int(chosen["id"]))


async def find_user_by_google_id(google_id: str) -> Optional[dict]:
    rows = await database.fetch_all(
        users.select().where(
            sa.or_(users.c.google_id == google_id, users.c.linked_google_id == google_id)
        )
    )
    if not rows:
        return None
    chosen = None
    for row in rows:
        r = dict(row)
        if not r.get("primary_user_id"):
            chosen = r
            break
    if chosen is None:
        chosen = dict(rows[0])
    return await _resolve_primary_row(int(chosen["id"]))


async def _repoint_user_fk(table, column, primary_id: int, secondary_id: int) -> None:
    await database.execute(
        table.update().where(column == secondary_id).values(**{column.key: primary_id})
    )


async def _dedupe_user_scope(table_name: str, scope_col: str, primary_id: int, secondary_id: int) -> None:
    # Remove duplicates before repointing rows in tables with UNIQUE(scope, user_id).
    await database.execute(
        sa.text(
            f"""
            DELETE FROM {table_name} s
            USING {table_name} p
            WHERE s.user_id = :sid
              AND p.user_id = :pid
              AND s.{scope_col} = p.{scope_col}
            """
        ),
        {"sid": secondary_id, "pid": primary_id},
    )


async def _merge_shop_cart(primary_id: int, secondary_id: int) -> None:
    sec_rows = await database.fetch_all(
        shop_cart_items.select().where(shop_cart_items.c.user_id == secondary_id)
    )
    for row in sec_rows:
        product_id = int(row["product_id"])
        qty = int(row.get("quantity") or 1)
        existing = await database.fetch_one(
            shop_cart_items.select()
            .where(shop_cart_items.c.user_id == primary_id)
            .where(shop_cart_items.c.product_id == product_id)
        )
        if existing:
            await database.execute(
                shop_cart_items.update()
                .where(shop_cart_items.c.id == existing["id"])
                .values(quantity=int(existing.get("quantity") or 0) + qty)
            )
            await database.execute(
                shop_cart_items.delete()
                .where(shop_cart_items.c.user_id == secondary_id)
                .where(shop_cart_items.c.product_id == product_id)
            )
        else:
            await database.execute(
                shop_cart_items.update()
                .where(shop_cart_items.c.user_id == secondary_id)
                .where(shop_cart_items.c.product_id == product_id)
                .values(user_id=primary_id)
            )


async def merge_accounts(primary_id: int, secondary_id: int):
    """Move secondary account data into primary and disable secondary login."""
    primary = await _resolve_primary_row(primary_id)
    secondary_row = await database.fetch_one(users.select().where(users.c.id == int(secondary_id)))
    if not primary or not secondary_row:
        return
    primary_id = int(primary["id"])
    secondary = dict(secondary_row)
    secondary_id = int(secondary["id"])
    if primary_id == secondary_id:
        return

    # Repoint common user references.
    for table, col in (
        (messages, messages.c.user_id),
        (posts, posts.c.user_id),
        (leads, leads.c.user_id),
        (orders, orders.c.user_id),
        (subscriptions, subscriptions.c.user_id),
        (referrals, referrals.c.referrer_id),
        (referrals, referrals.c.referred_id),
        (referral_withdrawals, referral_withdrawals.c.user_id),
        (followups, followups.c.user_id),
        (page_views, page_views.c.user_id),
        (feedback, feedback.c.user_id),
        (community_posts, community_posts.c.user_id),
        (community_comments, community_comments.c.user_id),
        (community_likes, community_likes.c.user_id),
        (community_saved, community_saved.c.user_id),
        (community_folders, community_folders.c.user_id),
        (community_messages, community_messages.c.sender_id),
        (community_messages, community_messages.c.recipient_id),
        (profile_likes, profile_likes.c.user_id),
        (profile_likes, profile_likes.c.liked_user_id),
        (direct_messages, direct_messages.c.sender_id),
        (direct_messages, direct_messages.c.recipient_id),
        (wellness_journal_entries, wellness_journal_entries.c.user_id),
        (product_reviews, product_reviews.c.user_id),
        (shop_product_comments, shop_product_comments.c.user_id),
        (product_questions, product_questions.c.user_id),
        (product_questions, product_questions.c.answered_by),
        (shop_market_orders, shop_market_orders.c.user_id),
        (community_groups, community_groups.c.created_by),
        (support_message_deliveries, support_message_deliveries.c.admin_id),
        (support_message_deliveries, support_message_deliveries.c.recipient_id),
        (community_follows, community_follows.c.follower_id),
        (community_follows, community_follows.c.following_id),
        (user_block_overrides, user_block_overrides.c.user_id),
        (users, users.c.referred_by),
        (users, users.c.primary_user_id),
    ):
        await _repoint_user_fk(table, col, primary_id, secondary_id)

    # Unique(scope, user) tables: dedupe first.
    await _dedupe_user_scope("shop_product_likes", "product_id", primary_id, secondary_id)
    await _dedupe_user_scope("community_group_join_requests", "group_id", primary_id, secondary_id)
    await _dedupe_user_scope("community_group_members", "group_id", primary_id, secondary_id)
    await _dedupe_user_scope("community_group_member_permissions", "group_id", primary_id, secondary_id)
    await _dedupe_user_scope("community_group_member_bans", "group_id", primary_id, secondary_id)
    await _dedupe_user_scope("community_group_typing_status", "group_id", primary_id, secondary_id)
    await _dedupe_user_scope("community_group_message_likes", "message_id", primary_id, secondary_id)

    await _repoint_user_fk(shop_product_likes, shop_product_likes.c.user_id, primary_id, secondary_id)
    await _merge_shop_cart(primary_id, secondary_id)
    await _repoint_user_fk(community_group_join_requests, community_group_join_requests.c.user_id, primary_id, secondary_id)
    await _repoint_user_fk(community_group_members, community_group_members.c.user_id, primary_id, secondary_id)
    await _repoint_user_fk(community_group_member_permissions, community_group_member_permissions.c.user_id, primary_id, secondary_id)
    await _repoint_user_fk(community_group_member_bans, community_group_member_bans.c.user_id, primary_id, secondary_id)
    await _repoint_user_fk(community_group_member_bans, community_group_member_bans.c.banned_by, primary_id, secondary_id)
    await _repoint_user_fk(community_group_typing_status, community_group_typing_status.c.user_id, primary_id, secondary_id)
    await _repoint_user_fk(community_group_message_likes, community_group_message_likes.c.user_id, primary_id, secondary_id)
    await _repoint_user_fk(community_group_messages, community_group_messages.c.sender_id, primary_id, secondary_id)
    await _repoint_user_fk(community_group_messages, community_group_messages.c.addressed_user_id, primary_id, secondary_id)

    # Resolve unique single-row tables.
    pri_profile = await database.fetch_one(community_profiles.select().where(community_profiles.c.user_id == primary_id))
    sec_profile = await database.fetch_one(community_profiles.select().where(community_profiles.c.user_id == secondary_id))
    if sec_profile and not pri_profile:
        await database.execute(
            community_profiles.update().where(community_profiles.c.user_id == secondary_id).values(user_id=primary_id)
        )
    elif sec_profile and pri_profile:
        await database.execute(
            community_profiles.delete().where(community_profiles.c.user_id == secondary_id)
        )

    pri_perm = await database.fetch_one(admin_permissions.select().where(admin_permissions.c.user_id == primary_id))
    sec_perm = await database.fetch_one(admin_permissions.select().where(admin_permissions.c.user_id == secondary_id))
    if sec_perm and not pri_perm:
        await database.execute(
            admin_permissions.update().where(admin_permissions.c.user_id == secondary_id).values(user_id=primary_id)
        )
    elif sec_perm and pri_perm:
        await database.execute(
            admin_permissions.delete().where(admin_permissions.c.user_id == secondary_id)
        )

    pri_tb = await database.fetch_one(training_bot_operators.select().where(training_bot_operators.c.user_id == primary_id))
    sec_tb = await database.fetch_one(training_bot_operators.select().where(training_bot_operators.c.user_id == secondary_id))
    if sec_tb and not pri_tb:
        await database.execute(
            training_bot_operators.update().where(training_bot_operators.c.user_id == secondary_id).values(user_id=primary_id)
        )
    elif sec_tb and pri_tb:
        await database.execute(
            training_bot_operators.delete().where(training_bot_operators.c.user_id == secondary_id)
        )

    # Carry over stronger fields.
    updates = {}
    if not primary.get("linked_tg_id") and (secondary.get("linked_tg_id") or secondary.get("tg_id")):
        updates["linked_tg_id"] = secondary.get("linked_tg_id") or secondary.get("tg_id")
    if not primary.get("linked_google_id") and (secondary.get("linked_google_id") or secondary.get("google_id")):
        updates["linked_google_id"] = secondary.get("linked_google_id") or secondary.get("google_id")
    if not primary.get("email") and secondary.get("email"):
        updates["email"] = secondary["email"]
    if not primary.get("password_hash") and secondary.get("password_hash"):
        updates["password_hash"] = secondary["password_hash"]
    if not primary.get("name") and secondary.get("name"):
        updates["name"] = secondary["name"]
    if not primary.get("avatar") and secondary.get("avatar"):
        updates["avatar"] = secondary["avatar"]
    if (
        (secondary.get("subscription_plan") or "free") != "free"
        and (primary.get("subscription_plan") or "free") == "free"
    ):
        updates["subscription_plan"] = secondary.get("subscription_plan")
        updates["subscription_end"] = secondary.get("subscription_end")
        updates["subscription_admin_granted"] = bool(secondary.get("subscription_admin_granted"))
    if not primary.get("legal_accepted_at") and secondary.get("legal_accepted_at"):
        updates["legal_accepted_at"] = secondary.get("legal_accepted_at")
        if secondary.get("legal_docs_version"):
            updates["legal_docs_version"] = secondary.get("legal_docs_version")

    # Рефералка / амбассадорский магазин: не терять при слиянии (часто primary=Google, secondary=Telegram).
    if not primary.get("referred_by") and secondary.get("referred_by"):
        updates["referred_by"] = int(secondary["referred_by"])
    if not (primary.get("referral_shop_url") or "").strip() and (secondary.get("referral_shop_url") or "").strip():
        updates["referral_shop_url"] = (secondary.get("referral_shop_url") or "").strip()
    if not (primary.get("referral_code") or "").strip() and (secondary.get("referral_code") or "").strip():
        updates["referral_code"] = (secondary.get("referral_code") or "").strip().upper()

    try:
        pb = float(primary.get("referral_balance") or 0)
        sb = float(secondary.get("referral_balance") or 0)
        if sb:
            updates["referral_balance"] = pb + sb
    except (TypeError, ValueError):
        pass

    # Пробный «Старт»: у Telegram часто plan=free, но есть start_trial_until — раньше терялось при merge.
    p_claim = primary.get("start_trial_claimed_at")
    s_claim = secondary.get("start_trial_claimed_at")
    p_until = primary.get("start_trial_until")
    s_until = secondary.get("start_trial_until")
    if not p_claim and s_claim:
        updates["start_trial_claimed_at"] = s_claim
        updates["start_trial_until"] = s_until
        updates["start_trial_end_notified"] = bool(secondary.get("start_trial_end_notified"))
    elif p_claim and s_claim and s_until and (not p_until or s_until > p_until):
        updates["start_trial_until"] = s_until
    elif p_claim and not p_until and s_until:
        updates["start_trial_until"] = s_until

    if not primary.get("needs_tariff_choice") and secondary.get("needs_tariff_choice"):
        updates["needs_tariff_choice"] = True

    # Mark secondary as merged and remove direct login identifiers.
    await database.execute(
        users.update().where(users.c.id == secondary_id).values(
            primary_user_id=primary_id,
            tg_id=None,
            google_id=None,
            linked_tg_id=None,
            linked_google_id=None,
            email=None,
            password_hash=None,
            referral_balance=0,
        )
    )

    if updates:
        await database.execute(users.update().where(users.c.id == primary_id).values(**updates))


async def attach_telegram_login(primary_user_id: int, tg_id: int, name: str = "", avatar: str = "") -> tuple[bool, str]:
    primary = await _resolve_primary_row(primary_user_id)
    if not primary:
        return False, "Аккаунт не найден."
    primary_id = int(primary["id"])

    existing_tg = primary.get("tg_id")
    if existing_tg and int(existing_tg) != int(tg_id):
        return False, "К аккаунту уже привязан другой Telegram."

    holder = await find_user_by_telegram_id(tg_id)
    if holder and int(holder["id"]) != primary_id:
        await merge_accounts(primary_id=primary_id, secondary_id=int(holder["id"]))

    vals = {"tg_id": int(tg_id), "linked_tg_id": int(tg_id)}
    if name and not primary.get("name"):
        vals["name"] = name
    if avatar and not primary.get("avatar"):
        vals["avatar"] = avatar
    await database.execute(users.update().where(users.c.id == primary_id).values(**vals))
    return True, "Telegram привязан."


async def attach_google_login(primary_user_id: int, google_id: str, email: str = "", name: str = "", avatar: str = "") -> tuple[bool, str]:
    primary = await _resolve_primary_row(primary_user_id)
    if not primary:
        return False, "Аккаунт не найден."
    primary_id = int(primary["id"])

    existing_google = (primary.get("google_id") or "").strip()
    if existing_google and existing_google != google_id:
        return False, "К аккаунту уже привязан другой Google."

    holder = await find_user_by_google_id(google_id)
    if holder and int(holder["id"]) != primary_id:
        await merge_accounts(primary_id=primary_id, secondary_id=int(holder["id"]))

    vals = {"google_id": google_id, "linked_google_id": google_id}
    if email and not primary.get("email"):
        vals["email"] = email
    if name and not primary.get("name"):
        vals["name"] = name
    if avatar and not primary.get("avatar"):
        vals["avatar"] = avatar
    await database.execute(users.update().where(users.c.id == primary_id).values(**vals))
    return True, "Google привязан."


@router.get("/link", response_class=HTMLResponse)
async def link_account_hub(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    attach_screen_rim_prefs(user)
    from config import settings

    return templates.TemplateResponse(
        "account/link_account.html",
        {
            "request": request,
            "user": user,
            "site_url": settings.SITE_URL,
            "bot_username": settings.TELEGRAM_BOT_USERNAME,
        },
    )


@router.get("/language", response_class=HTMLResponse)
async def account_language_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/language")
    attach_screen_rim_prefs(user)
    return templates.TemplateResponse(
        "account/language.html",
        {"request": request, "user": user},
    )


@router.get("/settings", response_class=HTMLResponse)
async def account_settings_hub(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/settings")
    attach_screen_rim_prefs(user)
    uid = int(user.get("primary_user_id") or user["id"])
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if row:
        user = dict(row)
        attach_screen_rim_prefs(user)
        # Иначе effective_subscription_plan теряется: в БД при пробном «Старт» plan остаётся free → бургер режет ленту/чаты.
        await attach_subscription_effective(user)
    return templates.TemplateResponse(
        "account/settings.html",
        {"request": request, "user": user},
    )


@router.get("/settings/sound-notifications", response_class=HTMLResponse)
async def account_sound_notifications_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/settings/sound-notifications", status_code=302)
    attach_screen_rim_prefs(user)
    uid = int(user.get("primary_user_id") or user["id"])
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if row:
        user = dict(row)
        attach_screen_rim_prefs(user)
        await attach_subscription_effective(user)
    prefs_row = await database.fetch_one(
        sa.select(users.c.notification_prefs_json).where(users.c.id == uid)
    )
    notification_prefs = merge_notification_prefs(
        prefs_row["notification_prefs_json"] if prefs_row else None
    )
    return templates.TemplateResponse(
        "account/sound_notifications.html",
        {"request": request, "user": user, "notification_prefs": notification_prefs},
    )


@router.get("/profile-edit", response_class=HTMLResponse)
async def account_profile_edit_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/profile-edit")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    uid = int(user.get("primary_user_id") or user["id"])
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if row:
        user = dict(row)
        attach_screen_rim_prefs(user)
        await attach_subscription_effective(user)
    circles = await database.fetch_all(
        community_folders.select()
        .where(community_folders.c.user_id == uid)
        .order_by(community_folders.c.created_at.asc())
    )
    posts = await database.fetch_all(
        community_posts.select()
        .where(community_posts.c.user_id == uid)
        .where(community_posts.c.approved == True)
        .order_by(community_posts.c.created_at.desc())
        .limit(300)
    )
    profile_cards = merge_profile_public_cards(user.get("profile_public_cards_json"))
    return templates.TemplateResponse(
        "account/profile_edit.html",
        {
            "request": request,
            "user": user,
            "circles": [dict(c) for c in circles],
            "my_posts": [dict(p) for p in posts],
            "max_profile_circles": MAX_PROFILE_CIRCLES_ACCOUNT,
            "profile_cards": profile_cards,
        },
    )


@router.post("/profile-edit")
async def account_profile_edit_save(
    request: Request,
    name: str = Form(""),
    bio: str = Form(""),
    profile_link_label: str = Form(""),
    profile_link_url: str = Form(""),
    profile_thoughts: str = Form(""),
    profile_thoughts_font: str = Form(""),
    profile_thoughts_color: str = Form(""),
):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    uid = int(user.get("primary_user_id") or user["id"])
    form = await request.form()
    show_crypto = str(form.get("show_crypto_slide") or "0").strip() == "1"
    show_social = str(form.get("show_social_slide") or "0").strip() == "1"
    slide_order_csv = str(form.get("profile_slide_order") or "")
    social_raw = {k: str(form.get(f"social_{k}") or "") for k in SOCIAL_KEYS}
    v0_img = str(form.get("validator0_image") or "")
    v0_label = str(form.get("validator0_label") or "")
    v0_url = str(form.get("validator0_url") or "")
    v1_img = str(form.get("validator1_image") or "")
    v1_label = str(form.get("validator1_label") or "")
    v1_url = str(form.get("validator1_url") or "")
    cards_json = profile_public_cards_from_form(
        show_crypto,
        show_social,
        social_raw,
        v0_img,
        v0_label,
        v0_url,
        v1_img,
        v1_label,
        v1_url,
        slide_order_csv=slide_order_csv,
    )
    nm = (name or "").strip()[:255] or None
    bio_clean = (bio or "").strip()[:4000] or None
    lbl = (profile_link_label or "").strip()[:500] or None
    url = (profile_link_url or "").strip()[:2000] or None
    thoughts = (profile_thoughts or "").strip()[:1200] or None
    allowed_fonts = {
        "Inter", "Roboto", "Open Sans", "Montserrat", "Lora", "Playfair Display", "Manrope",
    }
    thought_font = (profile_thoughts_font or "").strip()[:80]
    if thought_font not in allowed_fonts:
        thought_font = "Inter"
    thought_color = (profile_thoughts_color or "").strip()[:16]
    if not thought_color:
        thought_color = "#3dd4e0"
    if not thought_color.startswith("#") or len(thought_color) not in (4, 7):
        thought_color = "#3dd4e0"
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(
            name=nm,
            bio=bio_clean,
            profile_link_label=lbl,
            profile_link_url=url,
            profile_thoughts=thoughts,
            profile_thoughts_font=thought_font,
            profile_thoughts_color=thought_color,
            profile_public_cards_json=cards_json,
        )
    )
    return RedirectResponse("/account/profile-edit", status_code=302)


@router.get("/style", response_class=HTMLResponse)
async def account_style_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/style")
    uid = int(user.get("primary_user_id") or user["id"])
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if row:
        user = dict(row)
        attach_screen_rim_prefs(user)
        await attach_subscription_effective(user)
    cur_theme = (user.get("profile_ui_theme") or "default").strip() or "default"
    if cur_theme not in PROFILE_UI_THEME_IDS:
        cur_theme = "default"
    return templates.TemplateResponse(
        "account/style.html",
        {
            "request": request,
            "user": user,
            "profile_ui_themes": PROFILE_UI_THEMES,
            "current_profile_ui_theme": cur_theme,
        },
    )


@router.post("/style/save")
async def account_style_save(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    uid = int(user.get("primary_user_id") or user["id"])
    theme = (body.get("profile_ui_theme") or "default").strip() or "default"
    if theme not in PROFILE_UI_THEME_IDS:
        theme = "default"
    token_lamp = bool(body.get("token_lamp_enabled", True))
    rim_on = bool(body.get("screen_rim_on", False))
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    rim = DEFAULT_SCREEN_RIM.copy()
    if row and row.get("screen_rim_json"):
        try:
            loaded = json.loads(row["screen_rim_json"])
            if isinstance(loaded, dict):
                rim = {**DEFAULT_SCREEN_RIM, **{k: v for k, v in loaded.items() if k in DEFAULT_SCREEN_RIM}}
        except Exception:
            pass
    rim["on"] = rim_on
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(
            token_lamp_enabled=token_lamp,
            profile_ui_theme=theme,
            screen_rim_json=json.dumps(rim),
        )
    )
    return JSONResponse({"ok": True})


@router.get("/screen-rim", response_class=HTMLResponse)
async def screen_rim_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    attach_screen_rim_prefs(user)
    return templates.TemplateResponse(
        "account/screen_rim.html",
        {"request": request, "user": user},
    )


@router.get("/wallet", response_class=HTMLResponse)
async def account_wallet_page(request: Request):
    """Экран привязки кошелька Decimal / MetaMask (раньше был в кабинете /dashboard)."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/wallet")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    uid = int(user.get("primary_user_id") or user["id"])
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if row:
        user = dict(row)
        attach_screen_rim_prefs(user)
        await attach_subscription_effective(user)
    from config import shevelev_token_address

    _wa = (user.get("wallet_address") or "").strip()
    shevelev_auto_sync = _wa.startswith("0x")
    return templates.TemplateResponse(
        "account/wallet.html",
        {
            "request": request,
            "user": user,
            "shevelev_token": shevelev_token_address(),
            "shevelev_auto_sync": shevelev_auto_sync,
        },
    )


@router.post("/screen-rim")
async def screen_rim_save(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)

    uid = int(user.get("primary_user_id") or user["id"])
    out = DEFAULT_SCREEN_RIM.copy()
    if "on" in body:
        out["on"] = bool(body["on"])
    if "r" in body:
        out["r"] = max(0, min(255, int(body["r"])))
    if "g" in body:
        out["g"] = max(0, min(255, int(body["g"])))
    if "b" in body:
        out["b"] = max(0, min(255, int(body["b"])))
    if "s" in body:
        out["s"] = max(0.05, min(1.0, float(body["s"])))
    if "w" in body:
        out["w"] = max(0.05, min(1.0, float(body["w"])))

    await database.execute(
        users.update().where(users.c.id == uid).values(screen_rim_json=json.dumps(out))
    )
    return JSONResponse({"ok": True, "screen_rim": out})


@router.get("/link-telegram", response_class=HTMLResponse)
async def link_telegram_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    from config import settings
    return templates.TemplateResponse(
        "account/link_telegram.html",
        {"request": request, "user": user, "site_url": settings.SITE_URL,
         "bot_username": settings.TELEGRAM_BOT_USERNAME},
    )


@router.post("/link-telegram-start")
async def link_telegram_start(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    primary = await _resolve_primary_row(int(user["id"]))
    if not primary:
        return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)

    token = secrets.token_urlsafe(24).replace("-", "").replace("_", "")[:48]
    expires = datetime.utcnow() + timedelta(minutes=30)
    uid = int(primary["id"])
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(link_token=token, link_token_expires=expires, link_merge_secondary_id=None)
    )

    from config import settings

    bot_username = (settings.TELEGRAM_BOT_USERNAME or "").strip() or "mushrooms_ai_bot"
    deeplink = f"https://t.me/{bot_username}?start=link_{token}"
    return JSONResponse({"ok": True, "deeplink": deeplink, "expires_at": expires.isoformat()})


@router.get("/check-link-status")
async def check_link_status(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"linked": False}, status_code=401)
    primary = await _resolve_primary_row(int(user["id"]))
    if not primary:
        return JSONResponse({"linked": False}, status_code=404)
    linked = bool(primary.get("tg_id") or primary.get("linked_tg_id"))
    return JSONResponse({"linked": linked})


@router.get("/link-telegram-callback")
async def link_telegram_callback(request: Request):
    user = await get_user_from_request(request)
    if not user:
        logger.warning("link-telegram-callback: no session user, redirecting to login")
        return RedirectResponse("/login")

    try:
        data = dict(request.query_params)
        if not verify_telegram_auth(data.copy()):
            logger.warning("Telegram auth verification failed for user_id=%s data=%s", user["id"], data)
            primary = await _resolve_primary_row(int(user["id"]))
            uid = int(primary["id"]) if primary else int(user["id"])
            base = await web_default_home_path(uid)
            return RedirectResponse(f"{base}?error=tg_auth_failed")

        raw_id = data.get("id")
        if not raw_id:
            logger.error("Missing 'id' in Telegram callback params: %s", data)
            primary = await _resolve_primary_row(int(user["id"]))
            uid = int(primary["id"]) if primary else int(user["id"])
            base = await web_default_home_path(uid)
            return RedirectResponse(f"{base}?error=tg_auth_failed")

        tg_id = int(raw_id)
        name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        photo = data.get("photo_url", "")

        ok, msg = await attach_telegram_login(
            primary_user_id=int(user["id"]),
            tg_id=tg_id,
            name=name,
            avatar=photo,
        )
        if not ok:
            logger.warning("link_telegram_callback failed for user_id=%s: %s", user["id"], msg)
            primary = await _resolve_primary_row(int(user["id"]))
            uid = int(primary["id"]) if primary else int(user["id"])
            base = await web_default_home_path(uid)
            return RedirectResponse(f"{base}?error=tg_link_conflict")
        primary = await _resolve_primary_row(int(user["id"]))
        uid = int(primary["id"]) if primary else int(user["id"])
        base = await web_default_home_path(uid)
        return RedirectResponse(f"{base}?linked=telegram")

    except Exception as exc:
        logger.exception("Unexpected error in link_telegram_callback for user_id=%s: %s", user["id"], exc)
        _uid = int(user.get("primary_user_id") or user["id"])
        _b = await web_default_home_path(_uid)
        return RedirectResponse(f"{_b}?error=tg_link_failed")


@router.get("/link-google", response_class=HTMLResponse)
async def link_google_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    from config import settings
    return templates.TemplateResponse(
        "account/link_google.html",
        {"request": request, "user": user, "site_url": settings.SITE_URL},
    )


@router.get("/link-google-start")
async def link_google_start(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    from config import settings
    request.session["link_user_id"] = user["id"]
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{settings.SITE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "state": "link",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@router.get("/link-google-url")
async def link_google_url(request: Request):
    """Возвращает Google OAuth URL как JSON — для AJAX из Telegram Mini App."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from config import settings
    request.session["link_user_id"] = user["id"]
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{settings.SITE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "state": "link",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return JSONResponse({"ok": True, "url": url})


@router.get("/check-google-link-status")
async def check_google_link_status(request: Request):
    """Polling: привязан ли Google к текущему аккаунту."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    row = await database.fetch_one(users.select().where(users.c.id == user["id"]))
    if row and (row["google_id"] or row["linked_google_id"]):
        return JSONResponse({"linked": True, "email": row.get("email") or ""})
    return JSONResponse({"linked": False})


@router.get("/glow", response_class=HTMLResponse)
async def glow_page(request: Request):
    """Страница настройки подсветки экрана."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        "settings/glow.html",
        {"request": request, "user": user},
    )


@router.post("/sync-history")
async def sync_history(request: Request):
    """Compat endpoint: merge residual secondary accounts into current user."""
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    primary_id = int(user["id"])
    secondary_accounts = await database.fetch_all(
        users.select().where(users.c.primary_user_id == primary_id)
    )

    if not secondary_accounts:
        return JSONResponse({"ok": True, "merged": 0, "secondaries": 0})

    merged = 0
    for secondary in secondary_accounts:
        sid = int(secondary["id"])
        if sid == primary_id:
            continue
        await merge_accounts(primary_id=primary_id, secondary_id=sid)
        merged += 1

    return JSONResponse({
        "ok": True,
        "merged": merged,
        "secondaries": len(secondary_accounts),
    })


@router.post("/start-trial")
async def account_start_trial(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return JSONResponse({"ok": False, "error": "legal"}, status_code=403)
    uid = int(user.get("primary_user_id") or user["id"])
    r = await claim_start_trial(uid)
    if not r.get("ok"):
        return JSONResponse(r, status_code=400)
    return JSONResponse(r)


@router.get("/wellness-results", response_class=HTMLResponse)
async def wellness_results_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/wellness-results")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    uid = int(user.get("primary_user_id") or user["id"])
    plan = await check_subscription(uid)
    if plan == "free":
        return RedirectResponse("/subscriptions", status_code=302)
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return RedirectResponse("/subscriptions", status_code=302)
    from services.wellness_journal_service import aggregate_entries_for_display, wellness_journal_globally_enabled

    entries_raw = await database.fetch_all(
        wellness_journal_entries.select()
        .where(wellness_journal_entries.c.user_id == uid)
        .order_by(wellness_journal_entries.c.created_at.desc())
        .limit(220)
    )
    entries = [dict(e) for e in entries_raw]
    agg = aggregate_entries_for_display(entries)
    coach_ok = await wellness_journal_globally_enabled()
    return templates.TemplateResponse(
        "account/wellness_results.html",
        {
            "request": request,
            "user": user,
            "wrow": dict(row),
            "agg": agg,
            "entries": entries[:40],
            "coach_ok": coach_ok,
        },
    )


@router.post("/wellness-results")
async def wellness_results_save(
    request: Request,
    interval_days: int = Form(1),
    opt_out: str = Form(""),
):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/wellness-results")
    uid = int(user.get("primary_user_id") or user["id"])
    plan = await check_subscription(uid)
    if plan == "free":
        return RedirectResponse("/subscriptions", status_code=302)
    iv = int(interval_days) if str(interval_days).isdigit() else 1
    if iv not in (1, 3, 5, 7):
        iv = 1
    opt = (opt_out or "").strip().lower() in ("1", "true", "on", "yes")

    vals = {
        "wellness_journal_interval_days": iv,
        "wellness_journal_opt_out": opt,
    }
    if opt:
        vals["wellness_next_prompt_at"] = None
    else:
        vals["wellness_next_prompt_at"] = datetime.utcnow() + timedelta(hours=1)
    await database.execute(users.update().where(users.c.id == uid).values(**vals))
    return RedirectResponse("/account/wellness-results?saved=1", status_code=303)


@router.get("/wellness-results/pdf")
async def wellness_results_pdf(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/account/wellness-results")
    uid = int(user.get("primary_user_id") or user["id"])
    plan = await check_subscription(uid)
    if plan == "free":
        return RedirectResponse("/subscriptions", status_code=302)
    urow = await database.fetch_one(users.select().where(users.c.id == uid))
    if urow and urow.get("wellness_journal_pdf_allowed") is False:
        return RedirectResponse("/account/wellness-results?pdf=0", status_code=302)
    from services.wellness_journal_service import aggregate_entries_for_display
    from services.pdf_service import generate_wellness_journal_pdf

    entries_raw = await database.fetch_all(
        wellness_journal_entries.select()
        .where(wellness_journal_entries.c.user_id == uid)
        .order_by(wellness_journal_entries.c.created_at.desc())
        .limit(200)
    )
    entries = [dict(e) for e in entries_raw]
    agg = aggregate_entries_for_display(entries)
    nm = (user.get("name") or "").strip() or f"id {uid}"
    lines = [
        (
            "Сводка",
            f"Ответов в дневнике: {agg.get('reply_count', 0)}\n"
            f"Напоминаний AI: {agg.get('prompt_count', 0)}\n"
            f"Среднее настроение (0–10): {agg.get('mood_avg') or '—'}\n"
            f"Средняя энергия (0–10): {agg.get('energy_avg') or '—'}",
        )
    ]
    mush = agg.get("mushroom_counts") or []
    if mush:
        lines.append(("Упоминания грибов (по числу ответов)", "\n".join(f"• {m[0]}: {m[1]}" for m in mush[:20])))
    for t in agg.get("timeline", [])[:25]:
        at = t.get("at")
        ds = at.strftime("%d.%m.%Y %H:%M") if hasattr(at, "strftime") else str(at)
        raw = (t.get("raw") or "")[:600]
        lines.append((f"Запись {ds}", raw))
    pdf_bytes = generate_wellness_journal_pdf(nm, lines)
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="wellness-{uid}.pdf"'},
    )


@router.post("/merge")
async def manual_merge(
    request: Request,
    primary_id: int = Form(...),
    secondary_id: int = Form(...),
):
    user = await get_user_from_request(request)
    if not user or user["role"] != "admin":
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if primary_id == secondary_id:
        return JSONResponse({"error": "Same account"}, status_code=400)
    await merge_accounts(primary_id=primary_id, secondary_id=secondary_id)
    return JSONResponse({"ok": True, "merged": secondary_id, "into": primary_id})
