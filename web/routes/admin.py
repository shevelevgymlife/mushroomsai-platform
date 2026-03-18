from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from auth.session import get_user_from_request
from db.database import database
from db.models import users, messages, leads, products, orders, posts, page_views, ai_settings, subscriptions
import sqlalchemy
from datetime import datetime, timedelta, date

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="web/templates")


async def require_admin(request: Request):
    user = await get_user_from_request(request)
    if not user or user.get("role") != "admin":
        return None
    return user


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    today = datetime.utcnow().date()
    week_ago = datetime.utcnow() - timedelta(days=7)
    month_ago = datetime.utcnow() - timedelta(days=30)

    users_today = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.count()).select_from(users).where(
            sqlalchemy.cast(users.c.created_at, sqlalchemy.Date) == today
        )
    )
    users_week = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.count()).select_from(users).where(users.c.created_at >= week_ago)
    )
    users_month = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.count()).select_from(users).where(users.c.created_at >= month_ago)
    )
    new_leads = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.count()).select_from(leads).where(leads.c.status == "new")
    )
    revenue_today = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.coalesce(sqlalchemy.func.sum(orders.c.amount), 0)).where(
            sqlalchemy.cast(orders.c.created_at, sqlalchemy.Date) == today
        )
    )
    revenue_month = await database.fetch_val(
        sqlalchemy.select(sqlalchemy.func.coalesce(sqlalchemy.func.sum(orders.c.amount), 0)).where(
            orders.c.created_at >= month_ago
        )
    )
    recent_msgs = await database.fetch_all(
        messages.select().order_by(messages.c.created_at.desc()).limit(10)
    )

    return templates.TemplateResponse(
        "dashboard/admin.html",
        {
            "request": request,
            "user": admin,
            "users_today": users_today or 0,
            "users_week": users_week or 0,
            "users_month": users_month or 0,
            "new_leads": new_leads or 0,
            "revenue_today": revenue_today or 0,
            "revenue_month": revenue_month or 0,
            "recent_msgs": recent_msgs,
        },
    )


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


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, search: str = ""):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    query = users.select().order_by(users.c.created_at.desc())
    if search:
        query = query.where(
            (users.c.name.ilike(f"%{search}%")) |
            (users.c.email.ilike(f"%{search}%"))
        )
    all_users = await database.fetch_all(query.limit(100))

    return templates.TemplateResponse(
        "dashboard/users_list.html",
        {"request": request, "user": admin, "users": all_users, "search": search},
    )


@router.get("/ai", response_class=HTMLResponse)
async def ai_settings_page(request: Request):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    from ai.system_prompt import DEFAULT_SYSTEM_PROMPT
    row = await database.fetch_one(
        ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(1)
    )
    current_prompt = row["system_prompt"] if row else DEFAULT_SYSTEM_PROMPT
    history = await database.fetch_all(
        ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(10)
    )

    return templates.TemplateResponse(
        "dashboard/ai_settings.html",
        {"request": request, "user": admin, "current_prompt": current_prompt, "history": history},
    )


@router.post("/ai")
async def update_ai_settings(request: Request, system_prompt: str = Form(...)):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    await database.execute(
        ai_settings.insert().values(system_prompt=system_prompt, updated_by=admin["id"])
    )
    return RedirectResponse("/admin/ai", status_code=302)


@router.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    return templates.TemplateResponse(
        "dashboard/broadcast.html",
        {"request": request, "user": admin},
    )


@router.post("/broadcast/send")
async def broadcast_send(
    request: Request,
    message_text: str = Form(...),
    segment: str = Form("all"),
):
    admin = await require_admin(request)
    if not admin:
        return RedirectResponse("/login")

    query = users.select().where(users.c.tg_id != None)
    if segment == "subscribers":
        query = query.where(users.c.subscription_plan != "free")
    elif segment == "free":
        query = query.where(users.c.subscription_plan == "free")

    all_users = await database.fetch_all(query)

    from config import settings
    from telegram import Bot
    bot = Bot(token=settings.TELEGRAM_TOKEN)

    sent = 0
    for u in all_users:
        try:
            await bot.send_message(chat_id=u["tg_id"], text=message_text)
            sent += 1
        except Exception:
            pass

    return templates.TemplateResponse(
        "dashboard/broadcast.html",
        {"request": request, "user": admin, "success": f"Отправлено: {sent}"},
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
        return RedirectResponse("/login")

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
