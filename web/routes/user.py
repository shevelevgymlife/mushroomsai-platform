import os
import uuid
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from web.templates_utils import Jinja2Templates
from auth.session import get_user_from_request
from db.database import database
from db.models import (
    users, messages, orders, posts, post_likes, community_posts, community_likes, community_comments,
    community_folders, community_follows, community_saved, community_messages, profile_likes, community_profiles,
    dashboard_blocks, user_block_overrides, community_groups, community_group_members, community_group_messages,
    community_group_join_requests,
)
from services.referral_service import get_referral_stats
from services.subscription_service import check_subscription, PLANS
from ai.openai_client import chat_with_ai
from services.subscription_service import can_ask_question, increment_question_count
from services.plan_access import plan_allowed_block_keys, is_platform_operator
from services.group_platform_settings import user_can_create_community_group
from services.community_group_queries import fetch_community_group_row
from services.legal import legal_acceptance_redirect
from services.notify_admin import notify_admin_telegram
import sqlalchemy as sa
import secrets
import traceback as _traceback
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from config import settings

_logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


async def compute_visible_blocks(user_id: int, plan: str) -> list[str]:
    """Return list of block_keys visible for this user, respecting global settings and per-user overrides."""
    blocks_raw = await database.fetch_all(
        dashboard_blocks.select().order_by(dashboard_blocks.c.position, dashboard_blocks.c.id)
    )
    overrides_raw = await database.fetch_all(
        user_block_overrides.select().where(user_block_overrides.c.user_id == user_id)
    )
    overrides = {r["block_key"]: r for r in overrides_raw}

    PLAN_ORDER = ["free", "start", "pro", "maxi"]
    plan_idx = PLAN_ORDER.index(plan) if plan in PLAN_ORDER else 0

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

    return visible


def build_dashboard_secs(visible_block_keys: list[str]) -> list[str]:
    keys = set(visible_block_keys)
    # Единый каркас как Instagram у всех: профиль, лента, группы; наполнение внутри — по тарифу (vbk)
    out = ["me", "feed", "groups"]
    if "messages" in keys:
        out.append("messages")
    if "ai_chat" in keys:
        out.append("ai")
    if "knowledge_base" in keys:
        out.append("knowledge")
    if "pro_telegram" in keys or "pro_pin_info" in keys:
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


@router.get("/onboarding/tariff", response_class=HTMLResponse)
async def onboarding_tariff_page(request: Request):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login?next=/onboarding/tariff")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    if user.get("role") == "admin":
        return RedirectResponse("/dashboard")
    if not user.get("needs_tariff_choice"):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse(
        "onboarding_tariff.html",
        {"request": request, "user": user, "plans": PLANS, "error": None},
    )


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
    if choice not in ("free", "start", "pro", "maxi"):
        return RedirectResponse("/onboarding/tariff")
    # Платный приём на сайте не подключён — доступен только бесплатный план при регистрации.
    if choice != "free":
        return templates.TemplateResponse(
            "onboarding_tariff.html",
            {
                "request": request,
                "user": user,
                "plans": PLANS,
                "error": "Оплата тарифов на сайте пока не подключена. Выберите бесплатный план — он доступен сразу. Платные тарифы можно запросить у администратора через кабинет после входа.",
            },
            status_code=400,
        )
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(subscription_plan="free", needs_tariff_choice=False)
    )
    return RedirectResponse("/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")

    # If secondary account slips through session, re-issue token for primary
    if user.get("primary_user_id"):
        primary = await database.fetch_one(
            users.select().where(users.c.id == user["primary_user_id"])
        )
        if primary:
            from auth.session import create_access_token
            token = create_access_token(primary["id"])
            response = RedirectResponse("/dashboard", status_code=302)
            response.set_cookie("access_token", token, httponly=True, samesite="lax", max_age=60*60*24*30)
            return response

    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg

    effective_user_id = user.get("primary_user_id") or user["id"]

    try:
        await database.execute(
            users.update().where(users.c.id == effective_user_id).values(last_seen_at=datetime.utcnow())
        )
    except Exception:
        pass

    # Full profile first (bio, followers_count, following_count, etc.)
    full_profile = await database.fetch_one(users.select().where(users.c.id == effective_user_id))
    if full_profile:
        user = dict(full_profile)

    if user.get("needs_tariff_choice") and user.get("role") != "admin":
        return RedirectResponse("/onboarding/tariff")

    plan = await check_subscription(effective_user_id)
    plan_info = PLANS.get(plan, PLANS["free"])
    ref_stats = await get_referral_stats(effective_user_id)
    from config import settings
    ref_link = f"https://t.me/mushrooms_ai_bot?start={user.get('referral_code', '')}"
    ref_link_site = f"{settings.SITE_URL.rstrip('/')}/login?ref={user.get('referral_code', '')}"

    recent_messages = await database.fetch_all(
        messages.select()
        .where(messages.c.user_id == effective_user_id)
        .order_by(messages.c.created_at.desc())
        .limit(20)
    )
    my_orders = await database.fetch_all(
        orders.select().where(orders.c.user_id == user["id"]).order_by(orders.c.created_at.desc())
    )

    my_post_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_posts)
        .where(community_posts.c.user_id == effective_user_id)
    ) or 0
    ai_questions_today = user.get("daily_questions") or 0

    # Auto-upsert community profile with fresh stats
    try:
        await database.execute(
            sa.text(
                "INSERT INTO community_profiles (user_id, display_name, posts_count, followers_count, following_count) "
                "VALUES (:uid, :name, :pc, :fc, :fgc) ON CONFLICT (user_id) DO UPDATE SET "
                "display_name = EXCLUDED.display_name, posts_count = EXCLUDED.posts_count, "
                "followers_count = EXCLUDED.followers_count, following_count = EXCLUDED.following_count"
            ).bindparams(
                uid=effective_user_id,
                name=user.get("name"),
                pc=my_post_count,
                fc=user.get("followers_count") or 0,
                fgc=user.get("following_count") or 0,
            )
        )
    except Exception:
        pass

    # Last 5 posts by user (for classic home screen)
    recent_posts = await database.fetch_all(
        community_posts.select()
        .where(community_posts.c.user_id == effective_user_id)
        .order_by(community_posts.c.created_at.desc())
        .limit(5)
    )
    # Сетка профиля (Instagram): до 120 постов пользователя
    my_grid_posts = await database.fetch_all(
        community_posts.select()
        .where(community_posts.c.user_id == effective_user_id)
        .order_by(community_posts.c.created_at.desc())
        .limit(120)
    )

    # Feed: last 20 approved posts
    feed_raw = await database.fetch_all(
        community_posts.select()
        .where(community_posts.c.approved == True)
        .order_by(community_posts.c.pinned.desc(), community_posts.c.created_at.desc())
        .limit(20)
    )
    feed_authors = {}
    for p in feed_raw:
        if p["user_id"] and p["user_id"] not in feed_authors:
            a = await database.fetch_one(users.select().where(users.c.id == p["user_id"]))
            if a:
                feed_authors[p["user_id"]] = dict(a)

    # Posts liked by user
    liked_rows = await database.fetch_all(
        community_likes.select().where(community_likes.c.user_id == effective_user_id)
    )
    liked_post_ids = {r["post_id"] for r in liked_rows}

    # IDs the user follows (for "подписки" tab)
    following_rows = await database.fetch_all(
        community_follows.select().where(community_follows.c.follower_id == effective_user_id)
    )
    following_ids = {r["following_id"] for r in following_rows}

    # Shop products preview
    from db.models import shop_products as shop_products_table
    shop_preview = await database.fetch_all(
        shop_products_table.select().order_by(shop_products_table.c.created_at.desc()).limit(12)
    )

    # Community profile record
    comm_profile = await database.fetch_one(
        community_profiles.select().where(community_profiles.c.user_id == effective_user_id)
    )

    from config import settings as _settings, shevelev_token_address
    try:
        shevelev_tok = shevelev_token_address()
    except Exception:
        shevelev_tok = ""
    try:
        visible_block_keys = await compute_visible_blocks(effective_user_id, plan)
    except Exception:
        visible_block_keys = ["ai_chat", "messages", "community", "shop", "profile_photo", "posts", "tariffs", "referral", "knowledge_base"]

    allowed_by_plan = plan_allowed_block_keys(plan, user)
    visible_block_keys = [k for k in visible_block_keys if k in allowed_by_plan]
    for _extra in ("knowledge_base", "pro_telegram", "pro_pin_info", "seller_marketplace"):
        if _extra in allowed_by_plan and _extra not in visible_block_keys:
            visible_block_keys.append(_extra)

    if "community" not in visible_block_keys:
        feed_raw = []
        feed_authors = {}
    if "shop" not in visible_block_keys:
        shop_preview = []
    can_create_groups = await user_can_create_community_group(plan, user)
    can_manage_group_settings = is_platform_operator(user)
    dashboard_secs = build_dashboard_secs(visible_block_keys)

    group_list = []
    if "community" in visible_block_keys:
        try:
            group_list = await fetch_community_groups_for_user(effective_user_id)
        except Exception:
            _logger.exception("dashboard: fetch_community_groups_for_user")
            group_list = []

    response = templates.TemplateResponse(
        "dashboard/user.html",
        {
            "request": request,
            "user": user,
            "plan": plan,
            "plan_info": plan_info,
            "can_create_groups": can_create_groups,
            "can_manage_group_settings": can_manage_group_settings,
            "ref_stats": ref_stats,
            "ref_link": ref_link,
            "ref_link_site": ref_link_site,
            "messages": list(reversed(recent_messages)),
            "orders": my_orders,
            "my_post_count": my_post_count,
            "ai_questions_today": ai_questions_today,
            "feed_raw": feed_raw,
            "feed_authors": feed_authors,
            "liked_post_ids": liked_post_ids,
            "following_ids": following_ids,
            "recent_posts": recent_posts,
            "my_grid_posts": my_grid_posts,
            "plans_catalog": PLANS,
            "shop_preview": shop_preview,
            "comm_profile": dict(comm_profile) if comm_profile else None,
            "shevelev_token": shevelev_tok,
            "effective_user_id": effective_user_id,
            "visible_block_keys": visible_block_keys,
            "dashboard_secs": dashboard_secs,
            "group_list": group_list,
        },
    )
    # Не кэшировать HTML кабинета — иначе после деплоя виден старый интерфейс
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


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

            UNLIMITED_TG_IDS = {742166400}
            is_unlimited = (
                user.get("role") == "admin"
                or user.get("tg_id") in UNLIMITED_TG_IDS
                or user.get("linked_tg_id") in UNLIMITED_TG_IDS
            )
            if not is_unlimited:
                allowed = await can_ask_question(effective_user_id)
                if not allowed:
                    return JSONResponse({"error": "limit", "message": "Дневной лимит исчерпан. Подключите подписку для безлимитного доступа."}, status_code=429)
            answer = await chat_with_ai(user_message=user_message, user_id=effective_user_id)
            await increment_question_count(effective_user_id)
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
async def create_post(
    request: Request,
    content: str = Form(...),
    title: str = Form(""),
    folder_id: str = Form(""),
    image: UploadFile = File(None),
):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")

    if len(content.strip()) < 2:
        return RedirectResponse("/community")

    image_url = None
    if image and image.filename:
        if image.content_type in _POST_IMAGE_ALLOWED:
            data = await image.read()
            if len(data) <= _POST_IMAGE_MAX:
                ext = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else "jpg"
                filename = f"{uuid.uuid4().hex}.{ext}"
                base = "/data" if os.path.exists("/data") else "./media"
                save_path = os.path.join(base, "community", filename)
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(data)
                image_url = f"/media/community/{filename}"

    fid = int(folder_id) if folder_id.strip().isdigit() else None
    effective_uid = user.get("primary_user_id") or user["id"]
    tit = (title or "").strip()[:200] or None
    post_id = await database.execute(
        community_posts.insert().values(
            user_id=effective_uid,
            title=tit,
            content=content.strip(),
            image_url=image_url,
            folder_id=fid,
            approved=True,
        )
    )
    return JSONResponse({"ok": True, "id": post_id})


@router.post("/community/post/{post_id}/edit")
async def edit_community_post(
    request: Request,
    post_id: int,
    content: str = Form(...),
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
    if post["user_id"] != uid and user.get("role") != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if len((content or "").strip()) < 2:
        return JSONResponse({"error": "too short"}, status_code=400)
    tit = (title or "").strip()[:200] or None
    vals = {"content": content.strip(), "title": tit}
    if image and image.filename and image.content_type in _POST_IMAGE_ALLOWED:
        data = await image.read()
        if len(data) <= _POST_IMAGE_MAX:
            ext = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else "jpg"
            filename = f"{uuid.uuid4().hex}.{ext}"
            base = "/data" if os.path.exists("/data") else "./media"
            save_path = os.path.join(base, "community", filename)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)
            vals["image_url"] = f"/media/community/{filename}"
    await database.execute(
        community_posts.update().where(community_posts.c.id == post_id).values(**vals)
    )
    return JSONResponse({"ok": True})


@router.post("/community/post/{post_id}/share-dm")
async def share_community_post_dm(request: Request, post_id: int):
    """Отправить ссылку на пост в личку подписчику (подписка: я → он)."""
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
    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)
    if post["user_id"] != uid and user.get("role") != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    fol = await database.fetch_one(
        community_follows.select()
        .where(community_follows.c.follower_id == uid)
        .where(community_follows.c.following_id == recipient_id)
    )
    if not fol:
        return JSONResponse({"error": "not following"}, status_code=403)
    author_id = post["user_id"]
    base = settings.SITE_URL.rstrip("/")
    link = f"{base}/community/profile/{author_id}#pc-{post_id}"
    ttitle = (post.get("title") or "").strip()
    line = f"🔗 Пост: {ttitle}\n{link}" if ttitle else f"🔗 Пост в сообществе\n{link}"
    try:
        await database.execute(
            sa.text(
                "INSERT INTO direct_messages (sender_id, recipient_id, text, is_read, is_system) "
                "VALUES (:s, :r, :t, false, false)"
            ),
            {"s": uid, "r": recipient_id, "t": line},
        )
    except Exception as e:
        _logger.exception("share dm: %s", e)
        return JSONResponse({"error": "db"}, status_code=500)
    return JSONResponse({"ok": True, "link": link})


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
        return JSONResponse({"liked": False})
    else:
        try:
            seen = author_id is not None and author_id == uid
            await database.execute(
                community_likes.insert().values(
                    post_id=post_id,
                    user_id=uid,
                    seen_by_post_owner=seen,
                )
            )
            await database.execute(
                community_posts.update().where(community_posts.c.id == post_id)
                .values(likes_count=community_posts.c.likes_count + 1)
            )
        except Exception:
            pass
        return JSONResponse({"liked": True})


@router.post("/community/comment/{post_id}")
async def add_comment(request: Request, post_id: int, content: str = Form(...)):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    if len(content.strip()) < 1:
        return JSONResponse({"error": "empty"}, status_code=400)

    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)

    uid = user.get("primary_user_id") or user["id"]
    c_seen = post["user_id"] is not None and post["user_id"] == uid
    comment_id = await database.execute(
        community_comments.insert().values(
            post_id=post_id,
            user_id=uid,
            content=content.strip(),
            seen_by_post_owner=c_seen,
        )
    )
    await database.execute(
        community_posts.update().where(community_posts.c.id == post_id)
        .values(comments_count=community_posts.c.comments_count + 1)
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


def _normalize_profile_url(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return s[:2000]


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
    if rp not in ("start", "pro", "maxi"):
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
        "📋 Запрос смены тарифа (MushroomsAI)\n"
        f"Пользователь: {uname} (id {uid})\n"
        f"Email: {uemail}\n"
        f"Telegram id: {utg or '—'}\n"
        f"Текущий план: {cur}\n"
        f"Запрошен: {rp}\n"
        f"Комментарий: {nm or '—'}"
    )
    await notify_admin_telegram(txt)
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
        await database.execute(
            sa.text(
                "INSERT INTO direct_messages (sender_id, recipient_id, text, is_read, is_system) "
                "VALUES (:s, :r, :t, false, false)"
            ),
            {"s": uid, "r": rid, "t": msg},
        )
    except Exception:
        return JSONResponse({"error": "dm"}, status_code=500)
    tg_id = recipient.get("tg_id") or recipient.get("linked_tg_id")
    last_seen = recipient.get("last_seen_at")
    online = False
    if last_seen:
        try:
            online = datetime.utcnow() - last_seen < timedelta(minutes=3)
        except Exception:
            online = False
    if tg_id and not online:
        from bot.handlers.notify import notify_user

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
    return RedirectResponse("/dashboard", status_code=302)


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
            await database.execute(
                community_follows.insert().values(follower_id=uid, following_id=target_id)
            )
            await database.execute(
                users.update().where(users.c.id == uid)
                .values(following_count=users.c.following_count + 1)
            )
            await database.execute(
                users.update().where(users.c.id == target_id)
                .values(followers_count=users.c.followers_count + 1)
            )
        except Exception:
            pass
        # Notify target
        target = await database.fetch_one(users.select().where(users.c.id == target_id))
        if target:
            tg_id = target.get("tg_id") or target.get("linked_tg_id")
            if tg_id:
                from bot.handlers.notify import notify_user
                actor_name = user.get("name") or "Участник"
                await notify_user(tg_id, f"👤 <b>{actor_name}</b> подписался на вас в Сообществе MushroomsAI")
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
    msg_id = await database.execute(
        community_messages.insert().values(sender_id=uid, recipient_id=recipient_id, text=text)
    )
    # Notify recipient
    recipient = await database.fetch_one(users.select().where(users.c.id == recipient_id))
    if recipient:
        tg_id = recipient.get("tg_id") or recipient.get("linked_tg_id")
        if tg_id:
            from bot.handlers.notify import notify_user
            actor_name = user.get("name") or "Участник"
            await notify_user(tg_id, f"💬 Новое сообщение от <b>{actor_name}</b>:\n{text[:200]}\n\n<a href='https://mushroomsai.ru/community'>Открыть</a>")
    return JSONResponse({"ok": True, "id": msg_id})


@router.get("/community/messages/{other_id}")
async def get_dm_thread(request: Request, other_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    rows = await database.fetch_all(
        community_messages.select()
        .where(
            sa.or_(
                sa.and_(community_messages.c.sender_id == uid, community_messages.c.recipient_id == other_id),
                sa.and_(community_messages.c.sender_id == other_id, community_messages.c.recipient_id == uid),
            )
        )
        .order_by(community_messages.c.created_at.asc())
        .limit(100)
    )
    # Mark as read
    await database.execute(
        community_messages.update()
        .where(community_messages.c.sender_id == other_id)
        .where(community_messages.c.recipient_id == uid)
        .where(community_messages.c.is_read == False)
        .values(is_read=True)
    )
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
        sa.select(sa.func.count()).select_from(community_messages)
        .where(community_messages.c.recipient_id == uid)
        .where(community_messages.c.is_read == False)
    ) or 0
    return JSONResponse({"count": count})


@router.get("/community/conversations")
async def get_conversations(request: Request):
    """Get list of DM conversations for the current user."""
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    # Get all distinct conversation partners
    sent = await database.fetch_all(
        sa.select(community_messages.c.recipient_id.label("other_id"))
        .where(community_messages.c.sender_id == uid)
        .distinct()
    )
    received = await database.fetch_all(
        sa.select(community_messages.c.sender_id.label("other_id"))
        .where(community_messages.c.recipient_id == uid)
        .distinct()
    )
    partner_ids = list({r["other_id"] for r in sent} | {r["other_id"] for r in received})
    convos = []
    for pid in partner_ids:
        partner = await database.fetch_one(users.select().where(users.c.id == pid))
        if not partner:
            continue
        last_msg = await database.fetch_one(
            community_messages.select()
            .where(
                sa.or_(
                    sa.and_(community_messages.c.sender_id == uid, community_messages.c.recipient_id == pid),
                    sa.and_(community_messages.c.sender_id == pid, community_messages.c.recipient_id == uid),
                )
            )
            .order_by(community_messages.c.created_at.desc())
            .limit(1)
        )
        unread = await database.fetch_val(
            sa.select(sa.func.count()).select_from(community_messages)
            .where(community_messages.c.sender_id == pid)
            .where(community_messages.c.recipient_id == uid)
            .where(community_messages.c.is_read == False)
        ) or 0
        convos.append({
            "user_id": pid,
            "name": partner["name"] or "Участник",
            "avatar": partner["avatar"],
            "last_text": last_msg["text"][:80] if last_msg else "",
            "unread": unread,
            "last_at": last_msg["created_at"].strftime("%H:%M") if last_msg and last_msg["created_at"] else "",
        })
    convos.sort(key=lambda x: x["last_at"], reverse=True)
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
            await database.execute(
                profile_likes.insert().values(user_id=uid, liked_user_id=target_id)
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


async def _ensure_community_group_member(group_id: int, uid: int) -> bool:
    """Если пользователь — создатель или группа open, но строки в members нет — добавить (после сбоя INSERT)."""
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
    for q in (
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


@router.get("/community/groups")
async def community_groups_list_api(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = _effective_user_id(user)
    out = await fetch_community_groups_for_user(uid)
    return JSONResponse({"groups": out})


@router.post("/community/groups/create")
async def community_group_create(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required", "ok": False}, status_code=401)
    uid = _effective_user_id(user)
    pl = await check_subscription(uid)
    if not await user_can_create_community_group(pl, user):
        _logger.warning(
            "community_group_create 403 uid=%s plan=%s role=%s is_operator=%s",
            uid,
            pl,
            user.get("role"),
            is_platform_operator(user),
        )
        return JSONResponse(
            {
                "ok": False,
                "error": "Создание групп недоступно: проверьте тариф и политику в админке «Группы»",
                "plan": pl,
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
    uid = _effective_user_id(user)
    g = await fetch_community_group_row(group_id)
    if not g:
        return JSONResponse({"error": "not found"}, status_code=404)
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


@router.get("/community/groups/{group_id}/join-requests")
async def community_group_join_requests_list(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
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
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    try:
        await database.execute(
            sa.text(
                "UPDATE community_group_members SET last_read_at = NOW() WHERE group_id = :gid AND user_id = :uid"
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


@router.get("/community/groups/{group_id}/messages")
async def community_group_messages_get(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    g_row = await fetch_community_group_row(group_id)
    mem_row = await database.fetch_one(
        community_group_members.select()
        .where(community_group_members.c.group_id == group_id)
        .where(community_group_members.c.user_id == uid)
    )
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
    out = []
    for r in rows:
        snd = r["sender_id"]
        uname = "Участник"
        if snd:
            u = await database.fetch_one(users.select().where(users.c.id == snd))
            if u:
                uname = u["name"] or uname
        out.append({
            "id": r["id"],
            "sender_id": snd,
            "sender_name": uname,
            "text": r["text"],
            "is_mine": snd == uid,
            "can_delete": (snd == uid) or is_admin,
            "created_at": r["created_at"].strftime("%d.%m %H:%M") if r["created_at"] else "",
        })
    return JSONResponse({"messages": out})


@router.post("/community/groups/{group_id}/message")
async def community_group_message_post(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = _effective_user_id(user)
    if not await _ensure_community_group_member(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = (body.get("text") or "").strip()
    if not text or len(text) > 8000:
        return JSONResponse({"error": "bad text"}, status_code=400)
    g_row = await fetch_community_group_row(group_id)
    sm = None
    if g_row is not None:
        sm = g_row.get("slow_mode_seconds")
    try:
        sm_int = int(sm) if sm is not None else 0
    except (TypeError, ValueError):
        sm_int = 0
    if sm_int > 0:
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
    await database.execute(
        community_group_messages.insert().values(group_id=group_id, sender_id=uid, text=text)
    )
    return JSONResponse({"ok": True})


@router.delete("/community/groups/{group_id}/messages/{message_id}")
async def community_group_message_delete(request: Request, group_id: int, message_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
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
    is_admin = user.get("role") == "admin"
    if msg["sender_id"] != uid and not is_admin:
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
    is_admin = user.get("role") == "admin"
    if post["user_id"] != uid and not is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await _delete_community_post_and_related(post_id)
    return JSONResponse({"ok": True})


@router.get("/community/activity/unread-count")
async def community_activity_unread_count(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"likes": 0, "comments": 0, "total": 0})
    uid = user.get("primary_user_id") or user["id"]
    subq = sa.select(community_posts.c.id).where(community_posts.c.user_id == uid)
    try:
        n_likes = await database.fetch_val(
            sa.select(sa.func.count())
            .select_from(community_likes)
            .where(community_likes.c.post_id.in_(subq))
            .where(community_likes.c.seen_by_post_owner.is_(False))
        ) or 0
        n_com = await database.fetch_val(
            sa.select(sa.func.count())
            .select_from(community_comments)
            .where(community_comments.c.post_id.in_(subq))
            .where(community_comments.c.seen_by_post_owner.is_(False))
        ) or 0
    except Exception:
        n_likes, n_com = 0, 0
    return JSONResponse({"likes": int(n_likes), "comments": int(n_com), "total": int(n_likes + n_com)})


@router.post("/community/activity/mark-read")
async def community_activity_mark_read(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    subq = sa.select(community_posts.c.id).where(community_posts.c.user_id == uid)
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
    except Exception:
        pass
    return JSONResponse({"ok": True})
