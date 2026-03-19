import os
import uuid
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from web.templates_utils import Jinja2Templates
from auth.session import get_user_from_request
from db.database import database
from db.models import (
    users, messages, leads, products, orders, posts,
    page_views, ai_settings, subscriptions, knowledge_base,
    shop_products, feedback, admin_permissions, product_reviews,
    community_posts, community_comments, community_likes, community_folders,
)
import sqlalchemy
from datetime import datetime, timedelta, date

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="web/templates")

SUPER_ADMIN_TG_ID = 742166400

ADMIN_NAV = [
    ("Dashboard", "/admin"),
    ("AI", "/admin/ai"),
    ("Магазин", "/admin/shop"),
    ("Пользователи", "/admin/users"),
    ("Обратная связь", "/admin/feedback"),
    ("Рассылки", "/admin/broadcast"),
    ("База знаний", "/admin/knowledge"),
    ("Сообщество", "/admin/community"),
]

PERM_KEYS = [
    "can_dashboard", "can_ai", "can_shop", "can_users",
    "can_feedback", "can_broadcast", "can_knowledge",
]


def is_super_admin(user: dict) -> bool:
    return (
        user.get("tg_id") == SUPER_ADMIN_TG_ID
        or user.get("linked_tg_id") == SUPER_ADMIN_TG_ID
    )


async def require_admin(request: Request):
    """Basic admin check — role=admin. Super-admins pass automatically."""
    user = await get_user_from_request(request)
    if not user or user.get("role") != "admin":
        return None
    return user


async def require_permission(request: Request, perm: str):
    """Return user only if they have admin role AND the given permission (or are super-admin)."""
    user = await get_user_from_request(request)
    if not user or user.get("role") != "admin":
        return None
    if is_super_admin(user):
        return user
    try:
        row = await database.fetch_one(
            admin_permissions.select().where(admin_permissions.c.user_id == user["id"])
        )
        if row and row.get(perm):
            return user
    except Exception:
        pass
    return None


async def get_user_permissions(user: dict) -> dict:
    """Return a dict of all permission booleans for an admin user."""
    if is_super_admin(user):
        return {k: True for k in PERM_KEYS}
    try:
        row = await database.fetch_one(
            admin_permissions.select().where(admin_permissions.c.user_id == user["id"])
        )
        if row:
            return {k: bool(row.get(k, False)) for k in PERM_KEYS}
    except Exception:
        pass
    return {k: False for k in PERM_KEYS}


# ─── Dashboard ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    # Any admin can visit /admin — content is filtered by permissions
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    perms = await get_user_permissions(admin)

    total_users = users_today = messages_today = active_subs = 0
    msgs_with_users = []
    fb_with_users = []

    if perms.get("can_dashboard"):
        today = datetime.utcnow().date()
        total_users = await database.fetch_val(
            sqlalchemy.select(sqlalchemy.func.count()).select_from(users)
            .where(users.c.primary_user_id == None)
        ) or 0
        users_today = await database.fetch_val(
            sqlalchemy.select(sqlalchemy.func.count()).select_from(users).where(
                sqlalchemy.cast(users.c.created_at, sqlalchemy.Date) == today
            ).where(users.c.primary_user_id == None)
        ) or 0
        messages_today = await database.fetch_val(
            sqlalchemy.select(sqlalchemy.func.count()).select_from(messages).where(
                sqlalchemy.cast(messages.c.created_at, sqlalchemy.Date) == today
            )
        ) or 0
        active_subs = await database.fetch_val(
            sqlalchemy.select(sqlalchemy.func.count()).select_from(users).where(
                users.c.subscription_plan != "free"
            )
        ) or 0
        recent_msgs = await database.fetch_all(
            messages.select()
            .where(messages.c.role == "user")
            .order_by(messages.c.created_at.desc())
            .limit(10)
        )
        for msg in recent_msgs:
            u = None
            if msg["user_id"]:
                u = await database.fetch_one(users.select().where(users.c.id == msg["user_id"]))
            msgs_with_users.append({"msg": msg, "msg_user": u})

        if perms.get("can_feedback"):
            recent_feedback = await database.fetch_all(
                feedback.select().order_by(feedback.c.created_at.desc()).limit(5)
            )
            for fb_row in recent_feedback:
                u = None
                if fb_row["user_id"]:
                    u = await database.fetch_one(users.select().where(users.c.id == fb_row["user_id"]))
                fb_with_users.append({"fb": fb_row, "fb_user": u})

    return templates.TemplateResponse(
        "dashboard/admin.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": perms,
            "total_users": total_users,
            "users_today": users_today,
            "messages_today": messages_today,
            "active_subs": active_subs,
            "recent_msgs": msgs_with_users,
            "recent_feedback": fb_with_users,
        },
    )


# ─── AI Settings ──────────────────────────────────────────────────────────────

@router.get("/ai", response_class=HTMLResponse)
async def ai_settings_page(request: Request):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return RedirectResponse("/login")

    from ai.system_prompt import DEFAULT_SYSTEM_PROMPT
    row = await database.fetch_one(
        ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(1)
    )
    current_prompt = row["system_prompt"] if row else DEFAULT_SYSTEM_PROMPT
    history = await database.fetch_all(
        ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(5)
    )

    return templates.TemplateResponse(
        "dashboard/admin_ai.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "current_prompt": current_prompt,
            "history": history,
        },
    )


@router.post("/ai")
async def update_ai_settings(request: Request, system_prompt: str = Form(...)):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    await database.execute(
        ai_settings.insert().values(system_prompt=system_prompt, updated_by=admin["id"])
    )
    return RedirectResponse("/admin/ai", status_code=302)


@router.post("/ai/test")
async def test_ai(request: Request, question: str = Form(...)):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from ai.openai_client import chat_with_ai
    try:
        answer = await chat_with_ai(user_message=question, user_id=None)
        return JSONResponse({"answer": answer})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Shop ─────────────────────────────────────────────────────────────────────

@router.get("/shop", response_class=HTMLResponse)
async def shop_page(request: Request):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return RedirectResponse("/login")

    all_products = await database.fetch_all(
        shop_products.select().order_by(shop_products.c.id.desc())
    )
    return templates.TemplateResponse(
        "dashboard/admin_shop.html",
        {"request": request, "user": admin, "nav": ADMIN_NAV, "user_permissions": await get_user_permissions(admin), "products": all_products},
    )


@router.post("/shop/add")
async def add_shop_product(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    price: int = Form(0),
    url: str = Form(""),
    mushroom_type: str = Form(""),
    image_url: str = Form(""),
    category: str = Form(""),
    in_stock: str = Form(""),
):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    await database.execute(
        shop_products.insert().values(
            name=name, description=description, price=price or None,
            url=url or None, mushroom_type=mushroom_type or None,
            image_url=image_url or None, category=category or None,
            in_stock=(in_stock == "true"),
        )
    )
    return RedirectResponse("/admin/shop", status_code=302)


@router.post("/shop/edit/{product_id}")
async def edit_shop_product(
    request: Request,
    product_id: int,
    name: str = Form(...),
    description: str = Form(""),
    price: int = Form(0),
    url: str = Form(""),
    mushroom_type: str = Form(""),
    image_url: str = Form(""),
    category: str = Form(""),
    in_stock: str = Form(""),
):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    await database.execute(
        shop_products.update().where(shop_products.c.id == product_id).values(
            name=name, description=description, price=price or None,
            url=url or None, mushroom_type=mushroom_type or None,
            image_url=image_url or None, category=category or None,
            in_stock=(in_stock == "true"),
        )
    )
    return RedirectResponse("/admin/shop", status_code=302)


@router.post("/shop/delete/{product_id}")
async def delete_shop_product(request: Request, product_id: int):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    await database.execute(shop_products.delete().where(shop_products.c.id == product_id))
    return JSONResponse({"ok": True})


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


@router.post("/shop/upload-image")
async def upload_product_image(request: Request, file: UploadFile = File(...)):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if file.content_type not in ALLOWED_IMAGE_TYPES:
        return JSONResponse({"error": "Допустимые форматы: JPEG, PNG, WebP, GIF"}, status_code=400)

    data = await file.read()
    if len(data) > MAX_IMAGE_SIZE:
        return JSONResponse({"error": "Файл слишком большой (макс. 5 МБ)"}, status_code=400)

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    filename = f"{uuid.uuid4().hex}.{ext}"

    base = "/data" if os.path.exists("/data") else "./media"
    save_path = os.path.join(base, "products", filename)

    with open(save_path, "wb") as f:
        f.write(data)

    return JSONResponse({"ok": True, "url": f"/media/products/{filename}"})


# ─── Users ────────────────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, search: str = ""):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")

    query = users.select().where(users.c.primary_user_id == None).order_by(users.c.created_at.desc())
    if search:
        query = query.where(
            (users.c.name.ilike(f"%{search}%"))
            | (users.c.email.ilike(f"%{search}%"))
            | (sqlalchemy.cast(users.c.tg_id, sqlalchemy.String).ilike(f"%{search}%"))
        )
    all_users = await database.fetch_all(query.limit(100))

    msg_counts = {}
    for u in all_users:
        count = await database.fetch_val(
            sqlalchemy.select(sqlalchemy.func.count())
            .select_from(messages)
            .where(messages.c.user_id == u["id"])
        )
        msg_counts[u["id"]] = count or 0

    return templates.TemplateResponse(
        "dashboard/admin_users.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "users": all_users,
            "search": search,
            "msg_counts": msg_counts,
        },
    )


@router.post("/users/set-role")
async def set_user_role(request: Request, user_id: int = Form(...), role: str = Form(...)):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not is_super_admin(admin):
        return JSONResponse({"error": "Только главный администратор может назначать роли"}, status_code=403)

    if role not in ("admin", "user"):
        return JSONResponse({"error": "invalid role"}, status_code=400)

    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "user not found"}, status_code=404)
    if is_super_admin(target):
        return JSONResponse({"error": "Нельзя изменить роль главного администратора"}, status_code=403)

    await database.execute(users.update().where(users.c.id == user_id).values(role=role))
    return JSONResponse({"ok": True, "user_id": user_id, "role": role})


@router.get("/users/{user_id}/permissions")
async def get_user_perms_route(request: Request, user_id: int):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)

    row = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == user_id)
    )
    perms = {k: bool(row.get(k)) if row else False for k in PERM_KEYS}
    return JSONResponse({"ok": True, "permissions": perms, "role": target.get("role")})


@router.post("/users/{user_id}/permissions")
async def set_user_permissions(request: Request, user_id: int):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not is_super_admin(admin):
        return JSONResponse({"error": "Только главный администратор может назначать права"}, status_code=403)

    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)
    if is_super_admin(target):
        return JSONResponse({"error": "Нельзя изменить права главного администратора"}, status_code=403)

    body = await request.json()

    if body.get("revoke_all"):
        await database.execute(
            admin_permissions.delete().where(admin_permissions.c.user_id == user_id)
        )
        await database.execute(
            users.update().where(users.c.id == user_id).values(role="user")
        )
        return JSONResponse({"ok": True, "action": "revoked"})

    perms = {k: bool(body.get(k, False)) for k in PERM_KEYS}
    existing = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == user_id)
    )
    if existing:
        await database.execute(
            admin_permissions.update()
            .where(admin_permissions.c.user_id == user_id)
            .values(**perms)
        )
    else:
        await database.execute(
            admin_permissions.insert().values(user_id=user_id, **perms)
        )
    await database.execute(
        users.update().where(users.c.id == user_id).values(role="admin")
    )
    return JSONResponse({"ok": True, "permissions": perms})


@router.post("/users/{user_id}/subscription")
async def change_subscription(request: Request, user_id: int, plan: str = Form(...)):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if plan not in ("free", "start", "pro"):
        return JSONResponse({"error": "invalid plan"}, status_code=400)

    end_date = datetime.utcnow() + timedelta(days=30) if plan != "free" else None
    await database.execute(
        users.update().where(users.c.id == user_id).values(
            subscription_plan=plan, subscription_end=end_date
        )
    )
    return JSONResponse({"ok": True, "plan": plan})


@router.get("/users/{user_id}/dialogs", response_class=HTMLResponse)
async def user_dialogs(request: Request, user_id: int):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")

    target_user = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target_user:
        return RedirectResponse("/admin/users")

    dialogs = await database.fetch_all(
        messages.select()
        .where(messages.c.user_id == user_id)
        .order_by(messages.c.created_at.desc())
        .limit(50)
    )

    return templates.TemplateResponse(
        "dashboard/admin_user_dialogs.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "target_user": target_user,
            "dialogs": dialogs,
        },
    )


# ─── Feedback ─────────────────────────────────────────────────────────────────

@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request):
    admin = await require_permission(request, "can_feedback")
    if not admin:
        return RedirectResponse("/login")

    all_feedback = await database.fetch_all(
        feedback.select().order_by(feedback.c.created_at.desc())
    )
    fb_with_users = []
    for fb_row in all_feedback:
        u = None
        if fb_row["user_id"]:
            u = await database.fetch_one(users.select().where(users.c.id == fb_row["user_id"]))
        fb_with_users.append({"fb": fb_row, "fb_user": u})

    return templates.TemplateResponse(
        "dashboard/admin_feedback.html",
        {"request": request, "user": admin, "nav": ADMIN_NAV, "user_permissions": await get_user_permissions(admin), "feedbacks": fb_with_users},
    )


@router.post("/feedback/{feedback_id}/status")
async def update_feedback_status(request: Request, feedback_id: int, status: str = Form(...)):
    admin = await require_permission(request, "can_feedback")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    await database.execute(
        feedback.update().where(feedback.c.id == feedback_id).values(status=status)
    )
    return JSONResponse({"ok": True})


@router.post("/feedback/{feedback_id}/reply")
async def reply_to_feedback(request: Request, feedback_id: int, reply_text: str = Form(...)):
    admin = await require_permission(request, "can_feedback")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    fb_row = await database.fetch_one(feedback.select().where(feedback.c.id == feedback_id))
    if not fb_row or not fb_row["user_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)

    target_user = await database.fetch_one(users.select().where(users.c.id == fb_row["user_id"]))
    if not target_user or not target_user["tg_id"]:
        return JSONResponse({"error": "no telegram"}, status_code=400)

    from config import settings
    from telegram import Bot
    bot = Bot(token=settings.TELEGRAM_TOKEN)
    try:
        await bot.send_message(chat_id=target_user["tg_id"], text=f"💬 Ответ от команды MushroomsAI:\n\n{reply_text}")
        await database.execute(
            feedback.update().where(feedback.c.id == feedback_id).values(status="replied")
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Broadcast ────────────────────────────────────────────────────────────────

@router.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request):
    admin = await require_permission(request, "can_broadcast")
    if not admin:
        return RedirectResponse("/login")

    return templates.TemplateResponse(
        "dashboard/admin_broadcast.html",
        {"request": request, "user": admin, "nav": ADMIN_NAV, "user_permissions": await get_user_permissions(admin)},
    )


@router.post("/broadcast/send")
async def broadcast_send(
    request: Request,
    message_text: str = Form(...),
    segment: str = Form("all"),
):
    admin = await require_permission(request, "can_broadcast")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    query = users.select().where(users.c.tg_id != None)
    if segment == "pro":
        query = query.where(users.c.subscription_plan == "pro")
    elif segment == "start":
        query = query.where(users.c.subscription_plan == "start")
    elif segment == "free":
        query = query.where(users.c.subscription_plan == "free")

    all_users_list = await database.fetch_all(query)

    from config import settings
    from telegram import Bot
    bot = Bot(token=settings.TELEGRAM_TOKEN)

    sent = 0
    for u in all_users_list:
        try:
            await bot.send_message(chat_id=u["tg_id"], text=message_text)
            sent += 1
        except Exception:
            pass

    return templates.TemplateResponse(
        "dashboard/admin_broadcast.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "success": f"Отправлено: {sent} из {len(all_users_list)}",
        },
    )


# ─── Knowledge Base ───────────────────────────────────────────────────────────

@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request):
    admin = await require_permission(request, "can_knowledge")
    if not admin:
        return RedirectResponse("/login")

    entries = await database.fetch_all(
        knowledge_base.select().order_by(knowledge_base.c.id.desc())
    )
    return templates.TemplateResponse(
        "dashboard/admin_knowledge.html",
        {"request": request, "user": admin, "nav": ADMIN_NAV, "user_permissions": await get_user_permissions(admin), "entries": entries},
    )


@router.post("/knowledge/add")
async def add_knowledge(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    category: str = Form(""),
):
    admin = await require_permission(request, "can_knowledge")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    await database.execute(
        knowledge_base.insert().values(title=title, content=content, category=category)
    )
    return RedirectResponse("/admin/knowledge", status_code=302)


@router.post("/knowledge/delete/{entry_id}")
async def delete_knowledge(request: Request, entry_id: int):
    admin = await require_permission(request, "can_knowledge")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    await database.execute(knowledge_base.delete().where(knowledge_base.c.id == entry_id))
    return JSONResponse({"ok": True})


@router.post("/knowledge/sync")
async def sync_knowledge(request: Request):
    admin = await require_permission(request, "can_knowledge")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    import asyncio
    import json as _json
    import os as _os
    from load_knowledge import sync_drive_to_db

    try:
        creds_env = _os.getenv("GOOGLE_SERVICE_ACCOUNT", "")
        if not creds_env:
            return JSONResponse(
                {"error": "Переменная GOOGLE_SERVICE_ACCOUNT не задана на сервере."},
                status_code=500,
            )
        creds_dict = _json.loads(creds_env)

        from config import settings
        result = await asyncio.to_thread(sync_drive_to_db, settings.DATABASE_URL, creds_dict)
        return JSONResponse({
            "ok": True,
            "loaded": result["loaded"],
            "updated": result["updated"],
            "errors": result["errors"],
            "log": result["log"][-30:],
        })
    except _json.JSONDecodeError as e:
        return JSONResponse({"error": f"GOOGLE_SERVICE_ACCOUNT невалидный JSON: {e}"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Community ────────────────────────────────────────────────────────────────

@router.get("/community", response_class=HTMLResponse)
async def community_admin(request: Request):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    today = datetime.utcnow().date()

    total_posts = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.count()).select_from(community_posts)
    ) or 0
    posts_today = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.count()).select_from(community_posts).where(
            sqlalchemy.cast(community_posts.c.created_at, sqlalchemy.Date) == today
        )
    ) or 0
    active_users = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.count(sqlalchemy.distinct(community_posts.c.user_id)))
        .select_from(community_posts)
        .where(sqlalchemy.cast(community_posts.c.created_at, sqlalchemy.Date) >= (datetime.utcnow() - timedelta(days=7)).date())
    ) or 0
    total_comments = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.count()).select_from(community_comments)
    ) or 0

    all_posts = await database.fetch_all(
        community_posts.select()
        .order_by(community_posts.c.pinned.desc(), community_posts.c.created_at.desc())
        .limit(50)
    )
    feed = []
    for p in all_posts:
        author = None
        if p["user_id"]:
            author = await database.fetch_one(users.select().where(users.c.id == p["user_id"]))
        feed.append({"post": p, "author": author})

    community_users = await database.fetch_all(
        users.select()
        .where(sqlalchemy.select(sqlalchemy.func.count()).select_from(community_posts).where(community_posts.c.user_id == users.c.id).scalar_subquery() > 0)
        .order_by(users.c.created_at.desc())
        .limit(30)
    )

    return templates.TemplateResponse(
        "dashboard/admin_community.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "total_posts": total_posts,
            "posts_today": posts_today,
            "active_users": active_users,
            "total_comments": total_comments,
            "feed": feed,
            "community_users": community_users,
        },
    )


@router.post("/community/posts/{post_id}/delete")
async def delete_community_post(request: Request, post_id: int):
    admin = await require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(community_posts.delete().where(community_posts.c.id == post_id))
    return JSONResponse({"ok": True})


@router.post("/community/posts/{post_id}/pin")
async def pin_community_post(request: Request, post_id: int):
    admin = await require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return JSONResponse({"error": "not found"}, status_code=404)
    await database.execute(
        community_posts.update().where(community_posts.c.id == post_id)
        .values(pinned=not post["pinned"])
    )
    return JSONResponse({"ok": True, "pinned": not post["pinned"]})


# ─── Legacy routes ────────────────────────────────────────────────────────────

@router.get("/analytics", response_class=HTMLResponse)
async def analytics(request: Request):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    days = []
    for i in range(30):
        d = (datetime.utcnow() - timedelta(days=29 - i)).date()
        count = await database.fetch_val(
            sqlalchemy.select(sqlalchemy.func.count()).select_from(users).where(
                sqlalchemy.cast(users.c.created_at, sqlalchemy.Date) == d
            )
        )
        days.append({"date": str(d), "count": count or 0})

    return templates.TemplateResponse(
        "dashboard/analytics.html",
        {"request": request, "user": admin, "days": days},
    )


@router.get("/marketplace", response_class=HTMLResponse)
async def marketplace(request: Request):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    all_products = await database.fetch_all(products.select().order_by(products.c.id.desc()))
    all_orders = await database.fetch_all(orders.select().order_by(orders.c.created_at.desc()).limit(20))

    return templates.TemplateResponse(
        "dashboard/marketplace_mgr.html",
        {"request": request, "user": admin, "products": all_products, "orders": all_orders},
    )


@router.post("/marketplace/add")
async def add_product(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    category: str = Form(...),
    stock: int = Form(0),
    image_url: str = Form(""),
):
    admin = await require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    await database.execute(
        products.insert().values(
            name=name, description=description, price=price,
            category=category, stock=stock, image_url=image_url, active=True,
        )
    )
    return RedirectResponse("/admin/marketplace", status_code=302)


@router.post("/marketplace/toggle/{product_id}")
async def toggle_product(request: Request, product_id: int):
    admin = await require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    product = await database.fetch_one(products.select().where(products.c.id == product_id))
    if product:
        await database.execute(
            products.update().where(products.c.id == product_id).values(active=not product["active"])
        )
    return JSONResponse({"ok": True})


@router.get("/constructor", response_class=HTMLResponse)
async def constructor(request: Request):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    return templates.TemplateResponse(
        "dashboard/constructor.html",
        {"request": request, "user": admin},
    )


@router.post("/posts/{post_id}/approve")
async def approve_post(request: Request, post_id: int):
    admin = await require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(posts.update().where(posts.c.id == post_id).values(approved=True))
    return JSONResponse({"ok": True})


@router.post("/posts/{post_id}/delete")
async def delete_post(request: Request, post_id: int):
    admin = await require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(posts.delete().where(posts.c.id == post_id))
    return JSONResponse({"ok": True})
