from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from auth.session import get_user_from_request
from db.database import database
from db.models import users, messages, orders, posts, post_likes
from services.referral_service import get_referral_stats
from services.subscription_service import check_subscription, PLANS
from ai.openai_client import chat_with_ai
from services.subscription_service import can_ask_question, increment_question_count
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

    plan = await check_subscription(user["id"])
    plan_info = PLANS.get(plan, PLANS["free"])
    ref_stats = await get_referral_stats(user["id"])
    from config import settings
    ref_link = f"https://t.me/mushrooms_ai_bot?start={user.get('referral_code', '')}"

    recent_messages = await database.fetch_all(
        messages.select()
        .where(messages.c.user_id == user["id"])
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
        UNLIMITED_TG_IDS = {742166400}
        is_unlimited = (
            user.get("role") == "admin"
            or user.get("tg_id") in UNLIMITED_TG_IDS
            or user.get("linked_tg_id") in UNLIMITED_TG_IDS
        )
        if not is_unlimited:
            allowed = await can_ask_question(user["id"])
            if not allowed:
                return JSONResponse({"error": "limit", "message": "Дневной лимит исчерпан. Подключите подписку для безлимитного доступа."}, status_code=429)
        answer = await chat_with_ai(user_message=user_message, user_id=user["id"])
        await increment_question_count(user["id"])
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


@router.post("/community/post")
async def create_post(request: Request, content: str = Form(...)):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")

    if len(content.strip()) < 10:
        return RedirectResponse("/community")

    await database.execute(
        posts.insert().values(user_id=user["id"], content=content.strip(), approved=False)
    )
    return RedirectResponse("/community", status_code=302)


@router.post("/community/like/{post_id}")
async def like_post(request: Request, post_id: int):
    user = await require_auth(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    existing = await database.fetch_one(
        post_likes.select()
        .where(post_likes.c.post_id == post_id)
        .where(post_likes.c.user_id == user["id"])
    )
    if existing:
        await database.execute(
            post_likes.delete()
            .where(post_likes.c.post_id == post_id)
            .where(post_likes.c.user_id == user["id"])
        )
        await database.execute(
            posts.update().where(posts.c.id == post_id).values(likes=posts.c.likes - 1)
        )
        return JSONResponse({"liked": False})
    else:
        await database.execute(post_likes.insert().values(post_id=post_id, user_id=user["id"]))
        await database.execute(
            posts.update().where(posts.c.id == post_id).values(likes=posts.c.likes + 1)
        )
        return JSONResponse({"liked": True})


@router.post("/dashboard/language")
async def update_language(request: Request, language: str = Form(...)):
    user = await require_auth(request)
    if not user:
        return RedirectResponse("/login")
    await database.execute(
        users.update().where(users.c.id == user["id"]).values(language=language)
    )
    return RedirectResponse("/dashboard", status_code=302)
