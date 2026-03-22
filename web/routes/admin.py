import os
import uuid
from typing import Optional

from fastapi import APIRouter, Request, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from web.templates_utils import Jinja2Templates
from services.subscription_service import PLANS
from auth.session import get_user_from_request
from auth.blocked_identities import block_identities_for_user, unblock_identities_for_user
from db.database import database
from db.models import (
    users, messages, leads, products, orders, posts,
    page_views, ai_settings, subscriptions, knowledge_base,
    shop_products, feedback, admin_permissions, product_reviews,
    community_posts, community_comments, community_likes, community_saved, community_folders,
    homepage_blocks, dashboard_blocks, user_block_overrides,
    ai_training_posts, ai_training_folders,
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


def _parse_form_price(raw: Optional[str]) -> Optional[int]:
    """Пустое поле цены и нечисловой ввод не должны ломать сохранение товара (422)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


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


@router.get("/shop/product/{product_id}")
async def admin_shop_product_json(request: Request, product_id: int):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    row = await database.fetch_one(shop_products.select().where(shop_products.c.id == product_id))
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    p = dict(row)
    p["price"] = int(p["price"] or 0)
    p["in_stock"] = p.get("in_stock") is not False
    rv_avg = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.avg(product_reviews.c.rating)).where(
            product_reviews.c.product_id == product_id
        )
    )
    rv_n = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.count())
        .select_from(product_reviews)
        .where(product_reviews.c.product_id == product_id)
    ) or 0
    p["review_avg"] = round(float(rv_avg), 2) if rv_avg is not None else None
    p["review_count"] = int(rv_n)
    return JSONResponse(p)


@router.post("/shop/add")
async def add_shop_product(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    price: str = Form("0"),
    url: str = Form(""),
    mushroom_type: str = Form(""),
    image_url: str = Form(""),
    category: str = Form(""),
    in_stock: str = Form(""),
):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    price_val = _parse_form_price(price)
    await database.execute(
        shop_products.insert().values(
            seller_id=None,
            name=name, description=description, price=price_val,
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
    price: str = Form("0"),
    url: str = Form(""),
    mushroom_type: str = Form(""),
    image_url: str = Form(""),
    category: str = Form(""),
    in_stock: str = Form(""),
):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    price_val = _parse_form_price(price)
    await database.execute(
        shop_products.update().where(shop_products.c.id == product_id).values(
            name=name, description=description, price=price_val,
            url=url or None, mushroom_type=mushroom_type or None,
            image_url=image_url or None, category=category or None,
            in_stock=(in_stock == "true"),
        )
    )
    return JSONResponse({"ok": True})


@router.post("/shop/delete/{product_id}")
async def delete_shop_product(request: Request, product_id: int):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    await database.execute(shop_products.delete().where(shop_products.c.id == product_id))
    return JSONResponse({"ok": True})


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


def _normalize_image_content_type(upload: UploadFile) -> Optional[str]:
    raw = (upload.content_type or "").lower()
    if ";" in raw:
        raw = raw.split(";", 1)[0].strip()
    if raw in ALLOWED_IMAGE_TYPES:
        return raw
    name = (upload.filename or "").lower()
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".gif"):
        return "image/gif"
    return None


@router.post("/shop/upload-image")
async def upload_product_image(request: Request, file: UploadFile = File(...)):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    ct = _normalize_image_content_type(file)
    if not ct:
        return JSONResponse({"error": "Допустимые форматы: JPEG, PNG, WebP, GIF"}, status_code=400)

    data = await file.read()
    if len(data) > MAX_IMAGE_SIZE:
        return JSONResponse({"error": "Файл слишком большой (макс. 5 МБ)"}, status_code=400)

    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif" }.get(ct, "jpg")
    filename = f"{uuid.uuid4().hex}.{ext}"

    base = "/data" if os.path.exists("/data") else "./media"
    save_dir = os.path.join(base, "products")
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, filename)

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
    # Build enriched user list with display_tg_id = tg_id OR linked_tg_id
    enriched_users = []
    online_threshold = datetime.utcnow() - timedelta(minutes=10)
    for u in all_users:
        count = await database.fetch_val(
            sqlalchemy.select(sqlalchemy.func.count())
            .select_from(messages)
            .where(messages.c.user_id == u["id"])
        )
        msg_counts[u["id"]] = count or 0
        d = dict(u)
        d["display_tg_id"] = u["tg_id"] or u["linked_tg_id"]
        ls = d.get("last_seen_at")
        d["is_online"] = bool(ls and ls > online_threshold)
        enriched_users.append(d)

    return templates.TemplateResponse(
        "dashboard/admin_users.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "users": enriched_users,
            "search": search,
            "msg_counts": msg_counts,
            "now": datetime.utcnow(),
            "plan_labels": {k: v["name"] for k, v in PLANS.items()},
            "plan_modal_rows": [
                ("free", PLANS["free"]["name"], PLANS["free"]["price"]),
                ("start", PLANS["start"]["name"], PLANS["start"]["price"]),
                ("pro", PLANS["pro"]["name"], PLANS["pro"]["price"]),
                ("maxi", PLANS["maxi"]["name"], PLANS["maxi"]["price"]),
            ],
        },
    )


@router.post("/users/set-role")
async def set_user_role(request: Request, user_id: int = Form(...), role: str = Form(...)):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not is_super_admin(admin):
        return JSONResponse({"error": "Только главный администратор может назначать роли"}, status_code=403)

    if role not in ("admin", "user", "moderator"):
        return JSONResponse({"error": "invalid role"}, status_code=400)

    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "user not found"}, status_code=404)
    if is_super_admin(target):
        return JSONResponse({"error": "Нельзя изменить роль главного администратора"}, status_code=403)

    await database.execute(users.update().where(users.c.id == user_id).values(role=role))
    return JSONResponse({"ok": True, "user_id": user_id, "role": role})


@router.post("/users/{user_id}/marketplace-seller")
async def set_marketplace_seller_flag(request: Request, user_id: int, enabled: str = Form(...)):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)
    flag = enabled.strip().lower() in ("1", "true", "yes", "on")
    await database.execute(
        users.update().where(users.c.id == user_id).values(marketplace_seller=flag)
    )
    return JSONResponse({"ok": True, "user_id": user_id, "marketplace_seller": flag})


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


@router.post("/users/{user_id}/ban")
async def ban_user(request: Request, user_id: int):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)
    if is_super_admin(target):
        return JSONResponse({"error": "protected"}, status_code=403)
    await block_identities_for_user(dict(target))
    await database.execute(
        users.update().where(users.c.id == user_id).values(is_banned=True)
    )
    return JSONResponse({"ok": True})


@router.post("/users/{user_id}/unban")
async def unban_user(request: Request, user_id: int):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if target:
        await unblock_identities_for_user(dict(target))
    await database.execute(
        users.update().where(users.c.id == user_id).values(
            is_banned=False, ban_until=None, ban_reason=None, violations_count=0
        )
    )
    return JSONResponse({"ok": True})


@router.post("/users/{user_id}/subscription")
async def change_subscription(request: Request, user_id: int, plan: str = Form(...)):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if plan not in ("free", "start", "pro", "maxi"):
        return JSONResponse({"error": "invalid plan"}, status_code=400)

    sub_end_str: str = None
    try:
        form = await request.form()
        sub_end_str = form.get("subscription_end")
    except Exception:
        pass

    if plan == "free":
        end_date = None
    elif sub_end_str:
        try:
            end_date = datetime.strptime(sub_end_str, "%Y-%m-%d")
        except ValueError:
            end_date = datetime.utcnow() + timedelta(days=30)
    else:
        end_date = datetime.utcnow() + timedelta(days=30)

    await database.execute(
        users.update().where(users.c.id == user_id).values(
            subscription_plan=plan, subscription_end=end_date
        )
    )
    return JSONResponse({"ok": True, "plan": plan})


@router.patch("/users/{user_id}/plan")
async def patch_user_plan(request: Request, user_id: int):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    plan = body.get("plan")
    plan_expires_at = body.get("plan_expires_at")
    if plan not in ("free", "start", "pro", "maxi"):
        return JSONResponse({"error": "invalid plan"}, status_code=400)
    if plan == "free":
        end_date = None
    elif plan_expires_at:
        try:
            end_date = datetime.strptime(plan_expires_at, "%Y-%m-%d")
        except ValueError:
            end_date = datetime.utcnow() + timedelta(days=30)
    else:
        end_date = datetime.utcnow() + timedelta(days=30)
    await database.execute(
        users.update().where(users.c.id == user_id).values(
            subscription_plan=plan, subscription_end=end_date
        )
    )
    return JSONResponse({"ok": True, "plan": plan})


@router.post("/users/{user_id}/send-message")
async def send_message_to_user(request: Request, user_id: int, text: str = Form(...)):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)

    from services.support_delivery import deliver_support_message

    aid = admin.get("primary_user_id") or admin["id"]
    result = await deliver_support_message(
        admin_id=aid,
        recipient_user_id=user_id,
        text=text,
        feedback_id=None,
    )
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "delivery failed")}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "user_was_online": result.get("user_was_online"),
            "telegram_sent": result.get("telegram_sent"),
            "telegram_attempted": result.get("telegram_attempted"),
        }
    )


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
    if not target_user:
        return JSONResponse({"error": "not found"}, status_code=404)

    from services.support_delivery import deliver_support_message

    aid = admin.get("primary_user_id") or admin["id"]
    result = await deliver_support_message(
        admin_id=aid,
        recipient_user_id=fb_row["user_id"],
        text=reply_text,
        feedback_id=feedback_id,
    )
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "delivery failed")}, status_code=400)

    await database.execute(
        feedback.update().where(feedback.c.id == feedback_id).values(status="replied")
    )
    return JSONResponse(
        {
            "ok": True,
            "user_was_online": result.get("user_was_online"),
            "telegram_sent": result.get("telegram_sent"),
            "telegram_attempted": result.get("telegram_attempted"),
        }
    )


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
    elif segment == "maxi":
        query = query.where(users.c.subscription_plan == "maxi")
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
    await database.execute(community_likes.delete().where(community_likes.c.post_id == post_id))
    await database.execute(community_comments.delete().where(community_comments.c.post_id == post_id))
    await database.execute(community_saved.delete().where(community_saved.c.post_id == post_id))
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


@router.delete("/users/{user_id}/permanent")
async def delete_user_permanent(request: Request, user_id: int):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)
    if target.get("tg_id") == SUPER_ADMIN_TG_ID or target.get("linked_tg_id") == SUPER_ADMIN_TG_ID:
        return JSONResponse({"error": "protected"}, status_code=403)
    if (target.get("role") or "") == "admin":
        return JSONResponse({"error": "Нельзя удалить администратора"}, status_code=403)

    await unblock_identities_for_user(dict(target))

    import sqlalchemy as sa_
    for sql in [
        "DELETE FROM direct_messages WHERE sender_id=:uid OR recipient_id=:uid",
        "DELETE FROM moderation_log WHERE user_id=:uid",
        "DELETE FROM community_likes WHERE user_id=:uid",
        "DELETE FROM community_saved WHERE user_id=:uid",
        "DELETE FROM community_comments WHERE user_id=:uid",
        "DELETE FROM community_follows WHERE follower_id=:uid OR following_id=:uid",
        "DELETE FROM profile_likes WHERE user_id=:uid OR liked_user_id=:uid",
        "DELETE FROM community_posts WHERE user_id=:uid",
        "DELETE FROM messages WHERE user_id=:uid",
        "DELETE FROM sessions WHERE user_id=:uid",
        "DELETE FROM orders WHERE user_id=:uid",
        "DELETE FROM shop_market_order_items WHERE order_id IN (SELECT id FROM shop_market_orders WHERE user_id=:uid)",
        "DELETE FROM shop_market_orders WHERE user_id=:uid",
        "DELETE FROM shop_cart_items WHERE user_id=:uid",
        "DELETE FROM product_questions WHERE user_id=:uid OR answered_by=:uid",
        "DELETE FROM support_message_deliveries WHERE recipient_id=:uid OR admin_id=:uid",
        "DELETE FROM feedback WHERE user_id=:uid",
        "DELETE FROM product_reviews WHERE user_id=:uid",
        "UPDATE users SET primary_user_id=NULL WHERE primary_user_id=:uid",
        "UPDATE users SET referred_by=NULL WHERE referred_by=:uid",
    ]:
        try:
            await database.execute(sa_.text(sql), {"uid": user_id})
        except Exception:
            pass

    await database.execute(users.delete().where(users.c.id == user_id))
    return JSONResponse({"ok": True})


# ─── AI Training Posts ─────────────────────────────────────────────────────────

@router.get("/ai-posts", response_class=HTMLResponse)
async def ai_posts_page(request: Request):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return RedirectResponse("/login")
    try:
        posts_list = await database.fetch_all(
            ai_training_posts.select().order_by(ai_training_posts.c.created_at.desc())
        )
    except Exception:
        posts_list = []
    try:
        folder_rows = await database.fetch_all(ai_training_folders.select().order_by(ai_training_folders.c.name))
        extra_folder_names = [r["name"] for r in folder_rows]
    except Exception:
        extra_folder_names = []
    folder_order: list[str] = []
    posts_by_folder: dict[str, list] = {}
    for p in posts_list:
        fn = (p.get("folder") or "").strip() or "Без папки"
        if fn not in posts_by_folder:
            folder_order.append(fn)
            posts_by_folder[fn] = []
        posts_by_folder[fn].append(p)
    for fn in extra_folder_names:
        if fn and fn not in posts_by_folder:
            folder_order.append(fn)
            posts_by_folder[fn] = []
    if "Без папки" in folder_order:
        folder_order = ["Без папки"] + [x for x in folder_order if x != "Без папки"]
    folder_options = sorted(set(folder_order), key=lambda x: (0 if x == "Без папки" else 1, x.lower()))
    return templates.TemplateResponse(
        "dashboard/admin_ai_posts.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "posts": posts_list,
            "posts_by_folder": posts_by_folder,
            "folder_order": folder_order,
            "folder_options": folder_options,
        },
    )


@router.post("/ai-posts")
async def add_ai_post(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    category: str = Form(""),
    folder: str = Form(""),
):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        fn = folder.strip() or None
        await database.execute(
            ai_training_posts.insert().values(
                title=title.strip(),
                content=content.strip(),
                category=category.strip() or None,
                folder=fn,
            )
        )
        if fn:
            try:
                await database.execute(
                    ai_training_folders.insert().values(name=fn)
                )
            except Exception:
                pass
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/ai-posts/{post_id}/one")
async def get_ai_post_one(request: Request, post_id: int):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    row = await database.fetch_one(ai_training_posts.select().where(ai_training_posts.c.id == post_id))
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    p = dict(row)
    if p.get("created_at"):
        p["created_at"] = p["created_at"].isoformat()
    return JSONResponse(p)


@router.post("/ai-posts/{post_id}/update")
async def update_ai_post(
    request: Request,
    post_id: int,
    title: str = Form(...),
    content: str = Form(...),
    category: str = Form(""),
    folder: str = Form(""),
):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    row = await database.fetch_one(ai_training_posts.select().where(ai_training_posts.c.id == post_id))
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    fn = folder.strip() or None
    await database.execute(
        ai_training_posts.update()
        .where(ai_training_posts.c.id == post_id)
        .values(
            title=title.strip(),
            content=content.strip(),
            category=category.strip() or None,
            folder=fn,
        )
    )
    if fn:
        try:
            await database.execute(ai_training_folders.insert().values(name=fn))
        except Exception:
            pass
    return JSONResponse({"ok": True})


@router.post("/ai-folders")
async def add_ai_folder_only(request: Request, name: str = Form(...)):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    nm = name.strip()
    if len(nm) < 1:
        return JSONResponse({"error": "name required"}, status_code=400)
    try:
        await database.execute(ai_training_folders.insert().values(name=nm))
        return JSONResponse({"ok": True})
    except Exception:
        return JSONResponse({"error": "уже есть или ошибка БД"}, status_code=400)


@router.delete("/ai-folders")
async def delete_ai_folder_label(request: Request, name: str = Query(default="")):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    nm = (name or "").strip()
    if not nm:
        return JSONResponse({"error": "name required"}, status_code=400)
    await database.execute(ai_training_folders.delete().where(ai_training_folders.c.name == nm))
    return JSONResponse({"ok": True})


@router.delete("/ai-posts/{post_id}")
async def delete_ai_post(request: Request, post_id: int):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        await database.execute(ai_training_posts.delete().where(ai_training_posts.c.id == post_id))
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/ai-posts/{post_id}/toggle")
async def toggle_ai_post(request: Request, post_id: int):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        row = await database.fetch_one(ai_training_posts.select().where(ai_training_posts.c.id == post_id))
        if not row:
            return JSONResponse({"error": "not found"}, status_code=404)
        await database.execute(
            ai_training_posts.update().where(ai_training_posts.c.id == post_id).values(is_active=not row["is_active"])
        )
        return JSONResponse({"ok": True, "is_active": not row["is_active"]})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/homepage", response_class=HTMLResponse)
async def admin_homepage(request: Request):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")
    blocks_raw = await database.fetch_all(homepage_blocks.select().order_by(homepage_blocks.c.position, homepage_blocks.c.id))
    blocks = [dict(b) for b in blocks_raw]
    return templates.TemplateResponse(
        "dashboard/admin_homepage.html",
        {"request": request, "user": admin, "blocks": blocks},
    )


@router.post("/homepage/{block_name}")
async def update_homepage_block(
    request: Request,
    block_name: str,
    title: str = Form(""),
    subtitle: str = Form(""),
    content: str = Form(""),
    is_visible: str = Form(""),
    access_level: str = Form("all"),
    custom_title: str = Form(""),
    blur_for_guests: str = Form("false"),
    blur_text: str = Form(""),
):
    admin = await require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        await database.execute(
            homepage_blocks.update()
            .where(homepage_blocks.c.block_name == block_name)
            .values(
                title=title,
                subtitle=subtitle,
                content=content,
                is_visible=(is_visible == "true"),
                access_level=access_level,
                custom_title=custom_title or None,
                blur_for_guests=(blur_for_guests == "true"),
                blur_text=blur_text or None,
                updated_at=sqlalchemy.func.now(),
            )
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/homepage-blocks/reorder")
async def reorder_homepage_blocks(request: Request):
    admin = await require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
        order = body.get("order", [])
        for i, block_name in enumerate(order):
            await database.execute(
                homepage_blocks.update()
                .where(homepage_blocks.c.block_name == block_name)
                .values(position=i)
            )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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


# ─── Dashboard Blocks Manager ─────────────────────────────────────────────────

@router.get("/dashboard-blocks", response_class=HTMLResponse)
async def admin_dashboard_blocks(request: Request):
    admin = await require_permission(request, "can_dashboard")
    if not admin:
        return RedirectResponse("/login")
    blocks_raw = await database.fetch_all(
        dashboard_blocks.select().order_by(dashboard_blocks.c.position, dashboard_blocks.c.id)
    )
    blocks = [dict(b) for b in blocks_raw]
    return templates.TemplateResponse(
        "dashboard/admin_dashboard_blocks.html",
        {"request": request, "user": admin, "blocks": blocks,
         "user_permissions": await get_user_permissions(admin)},
    )


@router.post("/dashboard-blocks/reorder")
async def reorder_dashboard_blocks(request: Request):
    admin = await require_permission(request, "can_dashboard")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
        order = body.get("order", [])
        for i, block_key in enumerate(order):
            await database.execute(
                dashboard_blocks.update()
                .where(dashboard_blocks.c.block_key == block_key)
                .values(position=i)
            )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/dashboard-blocks/{block_key}")
async def update_dashboard_block(
    request: Request,
    block_key: str,
    is_visible: str = Form("true"),
    access_level: str = Form("all"),
    block_name: str = Form(""),
):
    admin = await require_permission(request, "can_dashboard")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        vals = {"is_visible": (is_visible == "true"), "access_level": access_level}
        if block_name:
            vals["block_name"] = block_name
        await database.execute(
            dashboard_blocks.update()
            .where(dashboard_blocks.c.block_key == block_key)
            .values(**vals)
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/dashboard-blocks/user/{user_id}")
async def get_user_block_overrides(request: Request, user_id: int):
    admin = await require_permission(request, "can_dashboard")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)
    overrides_raw = await database.fetch_all(
        user_block_overrides.select().where(user_block_overrides.c.user_id == user_id)
    )
    overrides = {r["block_key"]: dict(r) for r in overrides_raw}
    blocks_raw = await database.fetch_all(
        dashboard_blocks.select().order_by(dashboard_blocks.c.position)
    )
    result = []
    for b in blocks_raw:
        ov = overrides.get(b["block_key"])
        result.append({
            "block_key": b["block_key"],
            "block_name": b["block_name"],
            "global_visible": b["is_visible"],
            "override_visible": ov["is_visible"] if ov and ov["is_visible"] is not None else None,
            "custom_name": ov["custom_name"] if ov else None,
        })
    return JSONResponse({"ok": True, "user": {"id": target["id"], "name": target["name"]}, "blocks": result})


@router.post("/dashboard-blocks/user/{user_id}")
async def set_user_block_override(request: Request, user_id: int):
    admin = await require_permission(request, "can_dashboard")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    block_key = body.get("block_key")
    if not block_key:
        return JSONResponse({"error": "block_key required"}, status_code=400)
    is_visible = body.get("is_visible")  # None means "use global"
    custom_name = body.get("custom_name")
    existing = await database.fetch_one(
        user_block_overrides.select()
        .where(user_block_overrides.c.user_id == user_id)
        .where(user_block_overrides.c.block_key == block_key)
    )
    if existing:
        await database.execute(
            user_block_overrides.update()
            .where(user_block_overrides.c.user_id == user_id)
            .where(user_block_overrides.c.block_key == block_key)
            .values(is_visible=is_visible, custom_name=custom_name)
        )
    else:
        await database.execute(
            user_block_overrides.insert().values(
                user_id=user_id, block_key=block_key,
                is_visible=is_visible, custom_name=custom_name
            )
        )
    return JSONResponse({"ok": True})


@router.delete("/dashboard-blocks/user/{user_id}/{block_key}")
async def delete_user_block_override(request: Request, user_id: int, block_key: str):
    admin = await require_permission(request, "can_dashboard")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        user_block_overrides.delete()
        .where(user_block_overrides.c.user_id == user_id)
        .where(user_block_overrides.c.block_key == block_key)
    )
    return JSONResponse({"ok": True})


@router.get("/users/search")
async def search_users_api(request: Request, q: str = ""):
    admin = await require_permission(request, "can_dashboard")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not q or len(q) < 2:
        return JSONResponse({"users": []})
    results = await database.fetch_all(
        users.select()
        .where(users.c.primary_user_id == None)
        .where(
            (users.c.name.ilike(f"%{q}%"))
            | (users.c.email.ilike(f"%{q}%"))
            | (sqlalchemy.cast(users.c.tg_id, sqlalchemy.String).ilike(f"%{q}%"))
        )
        .limit(10)
    )
    return JSONResponse({"users": [{"id": u["id"], "name": u["name"], "email": u["email"]} for u in results]})
