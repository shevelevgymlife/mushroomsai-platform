import os
import uuid
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from web.templates_utils import Jinja2Templates
from auth.session import get_user_from_request
from db.database import database
from db.models import users, messages, orders, posts, post_likes, community_posts, community_likes, community_comments, community_folders
from services.referral_service import get_referral_stats
from services.subscription_service import check_subscription, PLANS
from ai.openai_client import chat_with_ai
from services.subscription_service import can_ask_question, increment_question_count
import sqlalchemy as sa
import secrets

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


async def require_auth(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return None
    return user


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")

    # Use primary account's ID so linked accounts see the same history
    effective_user_id = user.get("primary_user_id") or user["id"]

    plan = await check_subscription(effective_user_id)
    plan_info = PLANS.get(plan, PLANS["free"])
    ref_stats = await get_referral_stats(user["id"])
    from config import settings
    ref_link = f"https://t.me/mushrooms_ai_bot?start={user.get('referral_code', '')}"

    recent_messages = await database.fetch_all(
        messages.select()
        .where(messages.c.user_id == effective_user_id)
        .order_by(messages.c.created_at.desc())
        .limit(20)
    )
    my_orders = await database.fetch_all(
        orders.select().where(orders.c.user_id == user["id"]).order_by(orders.c.created_at.desc())
    )

    return templates.TemplateResponse(
        "dashboard/user.html",
        {
            "request": request,
            "user": user,
            "plan": plan,
            "plan_info": plan_info,
            "ref_stats": ref_stats,
            "ref_link": ref_link,
            "messages": list(reversed(recent_messages)),
            "orders": my_orders,
        },
    )


@router.post("/api/chat")
async def api_chat(request: Request):
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
    await database.execute(
        community_posts.insert().values(
            user_id=user["id"],
            content=content.strip(),
            image_url=image_url,
            folder_id=fid,
            approved=True,
        )
    )
    return RedirectResponse("/community", status_code=302)


@router.post("/community/like/{post_id}")
async def like_post(request: Request, post_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    existing = await database.fetch_one(
        community_likes.select()
        .where(community_likes.c.post_id == post_id)
        .where(community_likes.c.user_id == user["id"])
    )
    if existing:
        await database.execute(
            community_likes.delete()
            .where(community_likes.c.post_id == post_id)
            .where(community_likes.c.user_id == user["id"])
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
                community_likes.insert().values(post_id=post_id, user_id=user["id"])
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

    comment_id = await database.execute(
        community_comments.insert().values(
            post_id=post_id, user_id=user["id"], content=content.strip()
        )
    )
    await database.execute(
        community_posts.update().where(community_posts.c.id == post_id)
        .values(comments_count=community_posts.c.comments_count + 1)
    )
    rep_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_posts).where(community_posts.c.user_id == user["id"])
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
    fid = await database.execute(
        community_folders.insert().values(user_id=user["id"], name=name.strip())
    )
    return JSONResponse({"ok": True, "id": fid, "name": name.strip()})


@router.post("/profile/wallet")
async def update_wallet(request: Request, wallet: str = Form(...)):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    await database.execute(
        users.update().where(users.c.id == user["id"]).values(wallet_address=wallet.strip() or None)
    )
    return JSONResponse({"ok": True})


@router.post("/dashboard/language")
async def update_language(request: Request, language: str = Form(...)):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")
    await database.execute(
        users.update().where(users.c.id == user["id"]).values(language=language)
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
    filename = f"{user['id']}.{ext}"

    base = "/data" if os.path.exists("/data") else "./media"
    save_path = os.path.join(base, "avatars", filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    with open(save_path, "wb") as f:
        f.write(data)

    url = f"/media/avatars/{filename}"
    await database.execute(
        users.update().where(users.c.id == user["id"]).values(avatar=url)
    )
    return JSONResponse({"ok": True, "url": url})
