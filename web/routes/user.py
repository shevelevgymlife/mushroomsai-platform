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
)
from services.referral_service import get_referral_stats
from services.subscription_service import check_subscription, PLANS
from ai.openai_client import chat_with_ai
from services.subscription_service import can_ask_question, increment_question_count
from services.plan_access import plan_allowed_block_keys, can_create_community_groups
import sqlalchemy as sa
import secrets
import traceback as _traceback

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
    # Соцсеть: главный экран — профиль в стиле Instagram, лента отдельно
    if "community" in keys:
        out = ["me", "feed", "groups"]
    else:
        out = ["home"]
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
    if user.get("role") == "admin":
        return RedirectResponse("/dashboard")
    if not user.get("needs_tariff_choice"):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse(
        "onboarding_tariff.html",
        {"request": request, "user": user, "plans": PLANS},
    )


@router.post("/onboarding/tariff")
async def onboarding_tariff_submit(request: Request, choice: str = Form(...)):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")
    uid = user.get("primary_user_id") or user["id"]
    choice = (choice or "").strip().lower()
    if choice not in ("free", "start", "pro", "maxi"):
        return RedirectResponse("/onboarding/tariff")
    if choice == "free":
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(subscription_plan="free", needs_tariff_choice=False)
        )
        return RedirectResponse("/dashboard")
    await database.execute(
        users.update().where(users.c.id == uid).values(needs_tariff_choice=False)
    )
    return RedirectResponse("https://t.me/mushrooms_ai_bot?start=tariff_" + choice, status_code=302)


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

    effective_user_id = user.get("primary_user_id") or user["id"]

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
            ),
            {
                "uid": effective_user_id, "name": user.get("name"),
                "pc": my_post_count,
                "fc": user.get("followers_count") or 0,
                "fgc": user.get("following_count") or 0,
            },
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

    from config import settings as _settings
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
    can_create_groups = can_create_community_groups(plan, user)
    dashboard_secs = build_dashboard_secs(visible_block_keys)

    group_list = []
    try:
        if "community" in visible_block_keys:
            _gr = await database.fetch_all(
                sa.text("""
                    SELECT g.id, g.name, g.description, g.created_at,
                      (SELECT COUNT(*)::bigint FROM community_group_members m WHERE m.group_id = g.id) AS member_count,
                      EXISTS(SELECT 1 FROM community_group_members m2 WHERE m2.group_id = g.id AND m2.user_id = :uid) AS is_member
                    FROM community_groups g
                    ORDER BY g.created_at DESC
                    LIMIT 80
                """),
                {"uid": effective_user_id},
            )
            group_list = [dict(r) for r in _gr]
    except Exception:
        group_list = []

    response = templates.TemplateResponse(
        "dashboard/user.html",
        {
            "request": request,
            "user": user,
            "plan": plan,
            "plan_info": plan_info,
            "can_create_groups": can_create_groups,
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
            "shevelev_token": _settings.SHEVELEV_TOKEN_ADDRESS,
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
    post_id = await database.execute(
        community_posts.insert().values(
            user_id=effective_uid,
            content=content.strip(),
            image_url=image_url,
            folder_id=fid,
            approved=True,
        )
    )
    return JSONResponse({"ok": True, "id": post_id})


@router.post("/community/like/{post_id}")
async def like_post(request: Request, post_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    uid = user.get("primary_user_id") or user["id"]
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
            await database.execute(
                community_likes.insert().values(post_id=post_id, user_id=uid)
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
    comment_id = await database.execute(
        community_comments.insert().values(
            post_id=post_id, user_id=uid, content=content.strip()
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
    return int(user.get("primary_user_id") or user["id"])


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


@router.get("/community/groups")
async def community_groups_list_api(request: Request):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    rows = await database.fetch_all(
        sa.text("""
            SELECT g.id, g.name, g.description, g.created_at,
              (SELECT COUNT(*)::bigint FROM community_group_members m WHERE m.group_id = g.id) AS member_count,
              EXISTS(SELECT 1 FROM community_group_members m2 WHERE m2.group_id = g.id AND m2.user_id = :uid) AS is_member
            FROM community_groups g
            ORDER BY g.created_at DESC
            LIMIT 80
        """),
        {"uid": uid},
    )
    return JSONResponse({"groups": [dict(r) for r in rows]})


@router.post("/community/groups/create")
async def community_group_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    pl = await check_subscription(uid)
    if not can_create_community_groups(pl, user):
        return JSONResponse(
            {"error": "Создание групп доступно с тарифов Про и Макси"},
            status_code=403,
        )
    nm = (name or "").strip()
    if len(nm) < 2:
        return JSONResponse({"error": "name too short"}, status_code=400)
    if len(nm) > 120:
        return JSONResponse({"error": "name too long"}, status_code=400)
    desc = (description or "").strip()[:2000] or None
    row = await database.fetch_one(
        sa.text(
            "INSERT INTO community_groups (name, description, created_by) VALUES (:n, :d, :c) RETURNING id"
        ),
        {"n": nm, "d": desc, "c": uid},
    )
    gid = row["id"] if row else None
    if gid:
        try:
            await database.execute(
                community_group_members.insert().values(group_id=gid, user_id=uid)
            )
        except Exception:
            pass
    return JSONResponse({"ok": True, "id": gid})


@router.post("/community/groups/{group_id}/join")
async def community_group_join(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    g = await database.fetch_one(community_groups.select().where(community_groups.c.id == group_id))
    if not g:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        await database.execute(
            community_group_members.insert().values(group_id=group_id, user_id=uid)
        )
    except Exception:
        pass
    return JSONResponse({"ok": True})


@router.get("/community/groups/{group_id}/messages")
async def community_group_messages_get(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    if not await _user_in_community_group(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    rows = await database.fetch_all(
        community_group_messages.select()
        .where(community_group_messages.c.group_id == group_id)
        .order_by(community_group_messages.c.created_at.asc())
        .limit(200)
    )
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
            "created_at": r["created_at"].strftime("%d.%m %H:%M") if r["created_at"] else "",
        })
    return JSONResponse({"messages": out})


@router.post("/community/groups/{group_id}/message")
async def community_group_message_post(request: Request, group_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = user.get("primary_user_id") or user["id"]
    if not await _user_in_community_group(group_id, uid):
        return JSONResponse({"error": "not a member"}, status_code=403)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text or len(text) > 8000:
        return JSONResponse({"error": "bad text"}, status_code=400)
    await database.execute(
        community_group_messages.insert().values(group_id=group_id, sender_id=uid, text=text)
    )
    return JSONResponse({"ok": True})
