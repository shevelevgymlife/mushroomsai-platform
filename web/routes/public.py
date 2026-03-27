import logging
import os
import uuid
import sqlalchemy as sa
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from web.templates_utils import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from db.database import database
from db.models import (
    products,
    posts,
    users,
    shop_products,
    product_reviews,
    product_questions,
    shop_cart_items,
    shop_market_orders,
    shop_market_order_items,
    shop_product_likes,
    shop_product_comments,
    admin_permissions,
    community_posts,
    community_likes,
    community_folders,
    community_follows,
    community_saved,
    community_comments,
    profile_likes,
    direct_messages,
    homepage_blocks,
)
from auth.session import get_user_from_request
from auth.ui_prefs import attach_screen_rim_prefs
from config import settings, shevelev_token_address
from services.subscription_service import check_subscription, PLANS
from services.shop_catalog import product_gallery_urls
from services.legal import legal_acceptance_redirect
from services.referral_service import attach_invite_ref_from_query, get_referral_stats, ensure_user_referral_code
from services.ops_alerts import (
    notify_new_feedback,
    notify_new_order,
    notify_product_question,
)
from datetime import datetime
from web.profile_ui_themes import PROFILE_UI_THEME_IDS


def _is_free_restricted_user(user: dict | None) -> bool:
    if not user:
        return False
    role = (user.get("role") or "user").lower()
    if role in ("admin", "moderator"):
        return False
    plan = (user.get("subscription_plan") or "free").lower()
    return plan == "free"


def get_public_user_data(row: dict) -> dict:
    """Публичные поля профиля (без адреса кошелька; баланс SHEVELEV — только после синхронизации)."""
    return {
        "id": row["id"],
        "name": row["name"],
        "avatar": row["avatar"],
        "bio": row.get("bio"),
        "role": row["role"],
        "followers_count": row.get("followers_count") or 0,
        "following_count": row.get("following_count") or 0,
        "created_at": row.get("created_at"),
        "shevelev_balance_cached": row.get("shevelev_balance_cached"),
        "shevelev_balance_cached_at": row.get("shevelev_balance_cached_at"),
        "decimal_del_balance": row.get("decimal_del_balance"),
        "decimal_balance_cached_at": row.get("decimal_balance_cached_at"),
        "profile_link_label": row.get("profile_link_label"),
        "profile_link_url": row.get("profile_link_url"),
        "show_del_to_public": True if row.get("show_del_to_public") is None else bool(row.get("show_del_to_public")),
        "show_shev_to_public": True if row.get("show_shev_to_public") is None else bool(row.get("show_shev_to_public")),
        "token_lamp_enabled": True if row.get("token_lamp_enabled") is None else bool(row.get("token_lamp_enabled")),
    }


def apply_token_privacy_for_viewer(profile: dict, viewer_id: int | None, owner_id: int) -> None:
    """Скрывает кэшированные балансы у чужих глаз согласно настройкам владельца (мутирует profile)."""
    if viewer_id is not None and viewer_id == owner_id:
        return
    if not profile.get("show_del_to_public", True):
        profile["decimal_del_balance"] = None
    if not profile.get("show_shev_to_public", True):
        profile["shevelev_balance_cached"] = None


MAX_PROFILE_CIRCLES = 6


async def _profile_family_ids(profile_id: int) -> list[int]:
    family_rows = await database.fetch_all(
        users.select().with_only_columns(users.c.id).where(
            sa.or_(users.c.id == profile_id, users.c.primary_user_id == profile_id)
        )
    )
    return sorted({int(r["id"]) for r in family_rows} | {int(profile_id)})


async def ensure_default_profile_circles(profile_id: int) -> None:
    """У каждого профиля должен быть хотя бы один кружок (папка)."""
    cnt = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_folders).where(community_folders.c.user_id == profile_id)
    ) or 0
    if int(cnt) == 0:
        await database.execute(
            community_folders.insert().values(user_id=profile_id, name="Кружок")
        )


router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


def _shop_effective_uid(user: dict) -> int:
    return int(user.get("primary_user_id") or user["id"])


async def _shop_cart_qty(user_id: int) -> int:
    q = await database.fetch_val(
        sa.select(sa.func.coalesce(sa.func.sum(shop_cart_items.c.quantity), 0)).where(
            shop_cart_items.c.user_id == user_id
        )
    )
    return int(q or 0)


async def _can_answer_product_question(user_id: int, product: dict) -> bool:
    urow = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not urow:
        return False
    if urow.get("role") in ("admin", "moderator"):
        return True
    perm = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == user_id)
    )
    if perm and perm.get("can_shop"):
        return True
    if urow.get("marketplace_seller") and product.get("seller_id") == user_id:
        return True
    return False

MUSHROOM_TYPES = ["Рейши", "Шиитаке", "Кордицепс", "Ежовик", "Красный мухомор", "Пантерный мухомор", "Королевский мухомор"]
CATEGORIES = ["Экстракт", "Плодовое тело", "Капсулы", "Порошок"]


@router.get("/app", response_class=HTMLResponse)
async def app_entry(request: Request):
    """Точка входа для Telegram Mini App. Если залогинен — сразу в соцсеть."""
    current_user = await get_user_from_request(request)
    if current_user:
        return templates.TemplateResponse(
            "telegram_redirect_preserve.html",
            {"request": request, "redirect_dest": "/community"},
        )
    # Незалогиненный — главная страница сайта (там есть кнопка Войти → /login)
    return templates.TemplateResponse(
        "telegram_redirect_preserve.html",
        {"request": request, "redirect_dest": "/"},
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    current_user = await get_user_from_request(request)
    if current_user:
        # 302 теряет #tgWebAppData — клиентский редирект сохраняет fragment для Telegram Mini App
        return templates.TemplateResponse(
            "telegram_redirect_preserve.html",
            {"request": request, "redirect_dest": "/community"},
        )
    prods = await database.fetch_all(
        products.select().where(products.c.active == True).limit(6)
    )
    # Users count (primary accounts only)
    users_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(users).where(users.c.primary_user_id == None)
    ) or 0

    # Community members for social block (latest 6 with avatars)
    community_members_raw = await database.fetch_all(
        users.select()
        .where(users.c.primary_user_id == None)
        .order_by(users.c.id.desc())
        .limit(6)
    )
    community_members = [{"name": r["name"] or "User", "avatar": r["avatar"]} for r in community_members_raw]

    # Recent community posts with author info (только видимые/approved,
    # чтобы "последнее сообщение" совпадало с общим счётчиком в блоке)
    last_community_posts_raw = await database.fetch_all(
        sa.text("""
            SELECT cp.id, cp.content, cp.likes_count, cp.comments_count, cp.created_at,
                   u.name as author_name, u.avatar as author_avatar
            FROM community_posts cp
            LEFT JOIN users u ON u.id = cp.user_id
            WHERE cp.approved = true
            ORDER BY cp.created_at DESC
            LIMIT 3
        """)
    )
    last_community_posts = [dict(r) for r in last_community_posts_raw]

    # Community totals for chaos stat cards
    community_posts_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_posts).where(community_posts.c.approved == True)
    ) or 0

    # Featured marketplace products with avg ratings
    featured_raw = await database.fetch_all(
        shop_products.select().where(shop_products.c.in_stock == True)
        .order_by(shop_products.c.created_at.desc()).limit(6)
    )
    featured_products = []
    for p in featured_raw:
        avg = await database.fetch_val(
            sa.select(sa.func.avg(product_reviews.c.rating))
            .where(product_reviews.c.product_id == p["id"])
        )
        g = product_gallery_urls(dict(p))
        featured_products.append({
            "id": p["id"],
            "name": p["name"],
            "description": p.get("description") or "",
            "price": p.get("price"),
            "image_url": g[0] if g else p.get("image_url"),
            "avg_rating": round(float(avg), 1) if avg else None,
        })

    # Homepage blocks — ordered by position for dynamic section order
    try:
        blocks_raw = await database.fetch_all(
            homepage_blocks.select()
            .where(homepage_blocks.c.is_visible == True)
            .order_by(homepage_blocks.c.position, homepage_blocks.c.id)
        )
        blocks = {r["block_name"]: dict(r) for r in blocks_raw}
        # Use custom_title as the display title if set
        for b in blocks.values():
            if b.get("custom_title"):
                b["title"] = b["custom_title"]
        block_order = [r["block_name"] for r in blocks_raw]
    except Exception:
        blocks = {}
        block_order = []

    await database.execute(
        __import__("db.models", fromlist=["page_views"]).page_views.insert().values(
            path="/", user_id=current_user["id"] if current_user else None
        )
    )
    response = templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": current_user,
            "products": prods,
            "users_count": users_count,
            "community_posts_count": community_posts_count,
            "featured_products": featured_products,
            "community_members": community_members,
            "last_community_posts": last_community_posts,
            "blocks": blocks,
            "block_order": block_order,
        },
    )
    # Главная страница с live-метриками: без кэша, чтобы блок всегда обновлялся.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    attach_invite_ref_from_query(request, response)
    return response


@router.get("/shop", response_class=HTMLResponse)
async def shop(
    request: Request,
    mushroom_type: str = "",
    category: str = "",
    sort: str = "newest",
    search: str = "",
):
    current_user = await get_user_from_request(request)
    query = shop_products.select()

    if mushroom_type:
        query = query.where(shop_products.c.mushroom_type == mushroom_type)
    if category:
        query = query.where(shop_products.c.category == category)
    if search:
        query = query.where(shop_products.c.name.ilike(f"%{search}%"))

    if sort == "price_asc":
        query = query.order_by(shop_products.c.price.asc().nullslast())
    elif sort == "price_desc":
        query = query.order_by(shop_products.c.price.desc().nullsfirst())
    else:
        query = query.order_by(shop_products.c.created_at.desc())

    prods = await database.fetch_all(query)

    stats_rows = await database.fetch_all(
        sa.select(
            product_reviews.c.product_id,
            sa.func.avg(product_reviews.c.rating).label("avg_r"),
            sa.func.count().label("cnt"),
        ).group_by(product_reviews.c.product_id)
    )
    review_stats = {
        int(r["product_id"]): {
            "avg": round(float(r["avg_r"]), 1),
            "count": int(r["cnt"] or 0),
        }
        for r in stats_rows
    }

    products_enriched = []
    for p in prods:
        row = dict(p)
        row["_images"] = product_gallery_urls(row)
        products_enriched.append(row)

    cart_qty = 0
    if current_user:
        cart_qty = await _shop_cart_qty(_shop_effective_uid(current_user))

    cart_totals_rows = await database.fetch_all(
        sa.text(
            "SELECT product_id, COALESCE(SUM(quantity), 0)::int AS n "
            "FROM shop_cart_items GROUP BY product_id"
        )
    )
    cart_totals = {int(r["product_id"]): int(r["n"] or 0) for r in cart_totals_rows}

    return templates.TemplateResponse(
        "shop.html",
        {
            "request": request,
            "user": current_user,
            "products": products_enriched,
            "review_stats": review_stats,
            "mushroom_types": MUSHROOM_TYPES,
            "categories": CATEGORIES,
            "sel_mushroom": mushroom_type,
            "sel_category": category,
            "sort": sort,
            "search": search,
            "cart_qty": cart_qty,
            "cart_totals": cart_totals,
        },
    )


@router.get("/shop/{product_id}", response_class=HTMLResponse)
async def product_page(request: Request, product_id: int):
    current_user = await get_user_from_request(request)
    product = await database.fetch_one(
        shop_products.select().where(shop_products.c.id == product_id)
    )
    if not product:
        return HTMLResponse("Товар не найден", status_code=404)

    # Reviews with reviewer info
    reviews_raw = await database.fetch_all(
        product_reviews.select()
        .where(product_reviews.c.product_id == product_id)
        .order_by(product_reviews.c.created_at.desc())
    )
    reviews = []
    for r in reviews_raw:
        reviewer = None
        if r["user_id"]:
            reviewer = await database.fetch_one(users.select().where(users.c.id == r["user_id"]))
        reviews.append({"review": r, "reviewer": reviewer})

    avg_rating = None
    if reviews:
        avg_rating = round(sum(r["review"]["rating"] for r in reviews) / len(reviews), 1)

    # Similar products (same mushroom type, different id)
    similar = []
    if product["mushroom_type"]:
        similar = await database.fetch_all(
            shop_products.select()
            .where(shop_products.c.mushroom_type == product["mushroom_type"])
            .where(shop_products.c.id != product_id)
            .limit(4)
        )

    q_rows = await database.fetch_all(
        product_questions.select()
        .where(product_questions.c.product_id == product_id)
        .order_by(product_questions.c.created_at.desc())
        .limit(50)
    )
    questions = []
    for q in q_rows:
        asker = None
        if q["user_id"]:
            asker = await database.fetch_one(users.select().where(users.c.id == q["user_id"]))
        ans_by = None
        if q["answered_by"]:
            ans_by = await database.fetch_one(users.select().where(users.c.id == q["answered_by"]))
        questions.append({"q": q, "asker": asker, "answerer": ans_by})

    can_answer_questions = False
    cart_qty = 0
    uid = None
    if current_user:
        uid = _shop_effective_uid(current_user)
        cart_qty = await _shop_cart_qty(uid)
        can_answer_questions = await _can_answer_product_question(uid, dict(product))

    like_count = (
        await database.fetch_val(
            sa.select(sa.func.count())
            .select_from(shop_product_likes)
            .where(shop_product_likes.c.product_id == product_id)
        )
        or 0
    )
    user_liked = False
    if uid:
        lk = await database.fetch_one(
            shop_product_likes.select()
            .where(shop_product_likes.c.product_id == product_id)
            .where(shop_product_likes.c.user_id == uid)
        )
        user_liked = lk is not None

    comments_raw = await database.fetch_all(
        shop_product_comments.select()
        .where(shop_product_comments.c.product_id == product_id)
        .order_by(shop_product_comments.c.created_at.desc())
        .limit(80)
    )
    shop_comments = []
    for c in comments_raw:
        cu = None
        if c["user_id"]:
            cu = await database.fetch_one(users.select().where(users.c.id == c["user_id"]))
        shop_comments.append({"c": c, "author": cu})

    gallery_images = product_gallery_urls(dict(product))

    return templates.TemplateResponse(
        "shop_product.html",
        {
            "request": request,
            "user": current_user,
            "product": product,
            "gallery_images": gallery_images,
            "reviews": reviews,
            "avg_rating": avg_rating,
            "similar": similar,
            "product_questions": questions,
            "can_answer_questions": can_answer_questions,
            "cart_qty": cart_qty,
            "product_like_count": int(like_count),
            "product_user_liked": user_liked,
            "shop_comments": shop_comments,
            "shevelev_token": shevelev_token_address(),
        },
    )


@router.post("/shop/{product_id}/review")
async def add_review(
    request: Request,
    product_id: int,
    rating: int = Form(...),
    text: str = Form(""),
):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/shop/{product_id}", status_code=302)

    if not 1 <= rating <= 5:
        return RedirectResponse(f"/shop/{product_id}#reviews", status_code=302)

    rv_uid = _shop_effective_uid(current_user)
    await database.execute(
        product_reviews.insert().values(
            product_id=product_id,
            user_id=rv_uid,
            rating=rating,
            text=text.strip() or None,
        )
    )
    return RedirectResponse(f"/shop/{product_id}#reviews", status_code=302)


@router.post("/shop/{product_id}/like-toggle")
async def shop_product_like_toggle(request: Request, product_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = _shop_effective_uid(current_user)
    prod = await database.fetch_one(
        shop_products.select().where(shop_products.c.id == product_id)
    )
    if not prod:
        return JSONResponse({"error": "not found"}, status_code=404)
    row = await database.fetch_one(
        shop_product_likes.select()
        .where(shop_product_likes.c.product_id == product_id)
        .where(shop_product_likes.c.user_id == uid)
    )
    if row:
        await database.execute(
            shop_product_likes.delete().where(shop_product_likes.c.id == row["id"])
        )
        liked = False
    else:
        await database.execute(
            shop_product_likes.insert().values(user_id=uid, product_id=product_id)
        )
        liked = True
    cnt = (
        await database.fetch_val(
            sa.select(sa.func.count())
            .select_from(shop_product_likes)
            .where(shop_product_likes.c.product_id == product_id)
        )
        or 0
    )
    return JSONResponse({"ok": True, "liked": liked, "count": int(cnt)})


@router.post("/shop/{product_id}/product-comment")
async def shop_product_add_comment(
    request: Request,
    product_id: int,
    content: str = Form(...),
):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/shop/{product_id}", status_code=302)
    body = (content or "").strip()
    if len(body) < 1:
        return RedirectResponse(f"/shop/{product_id}#social", status_code=302)
    uid = _shop_effective_uid(current_user)
    prod = await database.fetch_one(
        shop_products.select().where(shop_products.c.id == product_id)
    )
    if not prod:
        return HTMLResponse("Не найдено", status_code=404)
    await database.execute(
        shop_product_comments.insert().values(
            product_id=product_id,
            user_id=uid,
            content=body[:4000],
        )
    )
    return RedirectResponse(f"/shop/{product_id}#social", status_code=302)


@router.post("/shop/{product_id}/question")
async def add_product_question(
    request: Request,
    product_id: int,
    question_text: str = Form(...),
):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/shop/{product_id}", status_code=302)
    product = await database.fetch_one(
        shop_products.select().where(shop_products.c.id == product_id)
    )
    if not product:
        return HTMLResponse("Не найдено", status_code=404)
    text = (question_text or "").strip()
    if len(text) < 3:
        return RedirectResponse(f"/shop/{product_id}#questions", status_code=302)
    uid = _shop_effective_uid(current_user)
    await database.execute(
        product_questions.insert().values(
            product_id=product_id,
            user_id=uid,
            question_text=text[:4000],
        )
    )
    try:
        await notify_product_question(product_id=product_id, question_text=text, user_id=uid)
    except Exception:
        pass
    return RedirectResponse(f"/shop/{product_id}#questions", status_code=302)


@router.post("/shop/questions/{question_id}/answer")
async def answer_product_question(
    request: Request,
    question_id: int,
    answer_text: str = Form(...),
):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth"}, status_code=401)
    qrow = await database.fetch_one(
        product_questions.select().where(product_questions.c.id == question_id)
    )
    if not qrow:
        return JSONResponse({"error": "not found"}, status_code=404)
    product = await database.fetch_one(
        shop_products.select().where(shop_products.c.id == qrow["product_id"])
    )
    if not product:
        return JSONResponse({"error": "not found"}, status_code=404)
    uid = _shop_effective_uid(current_user)
    if not await _can_answer_product_question(uid, dict(product)):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = (answer_text or "").strip()
    if len(body) < 1:
        return JSONResponse({"error": "empty"}, status_code=400)
    await database.execute(
        product_questions.update()
        .where(product_questions.c.id == question_id)
        .values(answer_text=body[:8000], answered_by=uid, answered_at=datetime.utcnow())
    )
    return JSONResponse({"ok": True})


@router.post("/shop/cart/add")
async def shop_cart_add(
    request: Request,
    product_id: int = Form(...),
    quantity: int = Form(1),
):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/shop/{product_id}", status_code=302)
    product = await database.fetch_one(
        shop_products.select().where(shop_products.c.id == product_id)
    )
    if not product or not product.get("in_stock", True):
        return RedirectResponse("/shop", status_code=302)
    qty = max(1, min(99, int(quantity or 1)))
    uid = _shop_effective_uid(current_user)
    existing = await database.fetch_one(
        shop_cart_items.select()
        .where(shop_cart_items.c.user_id == uid)
        .where(shop_cart_items.c.product_id == product_id)
    )
    if existing:
        new_q = min(99, int(existing["quantity"] or 0) + qty)
        await database.execute(
            shop_cart_items.update()
            .where(shop_cart_items.c.id == existing["id"])
            .values(quantity=new_q)
        )
    else:
        await database.execute(
            shop_cart_items.insert().values(user_id=uid, product_id=product_id, quantity=qty)
        )
    nxt = request.query_params.get("next") or f"/shop/{product_id}"
    return RedirectResponse(nxt, status_code=302)


@router.post("/shop/cart/update")
async def shop_cart_update(
    request: Request,
    line_id: int = Form(...),
    quantity: int = Form(...),
):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login?next=/shop/cart", status_code=302)
    uid = _shop_effective_uid(current_user)
    row = await database.fetch_one(
        shop_cart_items.select().where(shop_cart_items.c.id == line_id)
    )
    if not row or row["user_id"] != uid:
        return RedirectResponse("/shop/cart", status_code=302)
    q = int(quantity or 0)
    if q < 1:
        await database.execute(shop_cart_items.delete().where(shop_cart_items.c.id == line_id))
    else:
        await database.execute(
            shop_cart_items.update()
            .where(shop_cart_items.c.id == line_id)
            .values(quantity=min(99, q))
        )
    return RedirectResponse("/shop/cart", status_code=302)


@router.post("/shop/cart/remove")
async def shop_cart_remove(request: Request, line_id: int = Form(...)):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login?next=/shop/cart", status_code=302)
    uid = _shop_effective_uid(current_user)
    row = await database.fetch_one(
        shop_cart_items.select().where(shop_cart_items.c.id == line_id)
    )
    if row and row["user_id"] == uid:
        await database.execute(shop_cart_items.delete().where(shop_cart_items.c.id == line_id))
    return RedirectResponse("/shop/cart", status_code=302)


@router.get("/shop/cart", response_class=HTMLResponse)
async def shop_cart_page(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login?next=/shop/cart", status_code=302)
    uid = _shop_effective_uid(current_user)
    lines = await database.fetch_all(
        shop_cart_items.select().where(shop_cart_items.c.user_id == uid)
    )
    items = []
    total = 0
    for line in lines:
        p = await database.fetch_one(
            shop_products.select().where(shop_products.c.id == line["product_id"])
        )
        if not p:
            continue
        price = int(p["price"] or 0)
        qty = int(line["quantity"] or 1)
        line_total = price * qty
        total += line_total
        items.append({"line": line, "product": p, "line_total": line_total})
    cart_qty = await _shop_cart_qty(uid)
    return templates.TemplateResponse(
        "shop_cart.html",
        {
            "request": request,
            "user": current_user,
            "items": items,
            "total": total,
            "cart_qty": cart_qty,
        },
    )


@router.get("/shop/checkout", response_class=HTMLResponse)
async def shop_checkout_get(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login?next=/shop/checkout", status_code=302)
    uid = _shop_effective_uid(current_user)
    lines = await database.fetch_all(
        shop_cart_items.select().where(shop_cart_items.c.user_id == uid)
    )
    if not lines:
        return RedirectResponse("/shop/cart", status_code=302)
    items = []
    total = 0
    for line in lines:
        p = await database.fetch_one(
            shop_products.select().where(shop_products.c.id == line["product_id"])
        )
        if not p:
            continue
        price = int(p["price"] or 0)
        qty = int(line["quantity"] or 1)
        line_total = price * qty
        total += line_total
        items.append({"line": line, "product": p, "line_total": line_total})
    if not items:
        return RedirectResponse("/shop/cart", status_code=302)
    cart_qty = await _shop_cart_qty(uid)
    return templates.TemplateResponse(
        "shop_checkout.html",
        {
            "request": request,
            "user": current_user,
            "items": items,
            "total": total,
            "cart_qty": cart_qty,
        },
    )


@router.post("/shop/checkout")
async def shop_checkout_post(
    request: Request,
    delivery_address: str = Form(...),
    delivery_city: str = Form(""),
    delivery_phone: str = Form(""),
    delivery_comment: str = Form(""),
):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login?next=/shop/checkout", status_code=302)
    uid = _shop_effective_uid(current_user)
    lines = await database.fetch_all(
        shop_cart_items.select().where(shop_cart_items.c.user_id == uid)
    )
    if not lines:
        return RedirectResponse("/shop/cart", status_code=302)
    items = []
    total = 0
    for line in lines:
        p = await database.fetch_one(
            shop_products.select().where(shop_products.c.id == line["product_id"])
        )
        if not p or not p.get("in_stock", True):
            return RedirectResponse("/shop/cart?err=stock", status_code=302)
        price = int(p["price"] or 0)
        qty = int(line["quantity"] or 1)
        line_total = price * qty
        total += line_total
        items.append({"line": line, "product": p, "line_total": line_total})
    addr = (delivery_address or "").strip()
    if len(addr) < 5:
        return RedirectResponse("/shop/checkout?err=addr", status_code=302)
    oid = await database.execute(
        shop_market_orders.insert().values(
            user_id=uid,
            status="new",
            delivery_address=addr[:2000],
            delivery_city=(delivery_city or "").strip()[:500] or None,
            delivery_phone=(delivery_phone or "").strip()[:100] or None,
            delivery_comment=(delivery_comment or "").strip()[:2000] or None,
            total_amount=total,
        )
    )
    if oid is None:
        oid = await database.fetch_val(
            sa.select(shop_market_orders.c.id)
            .where(shop_market_orders.c.user_id == uid)
            .order_by(shop_market_orders.c.id.desc())
            .limit(1)
        )
    for it in items:
        p = it["product"]
        await database.execute(
            shop_market_order_items.insert().values(
                order_id=oid,
                product_id=p["id"],
                quantity=it["line"]["quantity"],
                unit_price=p["price"],
            )
        )
    for it in items:
        await database.execute(
            shop_cart_items.delete().where(shop_cart_items.c.id == it["line"]["id"])
        )
    try:
        await notify_new_order(order_id=int(oid or 0), user_id=uid, total_amount=total)
    except Exception:
        pass
    return RedirectResponse(f"/shop/order/{oid}", status_code=302)


@router.get("/shop/order/{order_id}", response_class=HTMLResponse)
async def shop_order_thanks(request: Request, order_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login", status_code=302)
    uid = _shop_effective_uid(current_user)
    order = await database.fetch_one(
        shop_market_orders.select().where(shop_market_orders.c.id == order_id)
    )
    if not order or order["user_id"] != uid:
        return RedirectResponse("/shop", status_code=302)
    oitems = await database.fetch_all(
        shop_market_order_items.select().where(shop_market_order_items.c.order_id == order_id)
    )
    enriched = []
    for oi in oitems:
        pn = None
        if oi["product_id"]:
            pr = await database.fetch_one(
                shop_products.select().where(shop_products.c.id == oi["product_id"])
            )
            if pr:
                pn = pr.get("name")
        d = dict(oi)
        d["product_name"] = pn
        enriched.append(d)
    cart_qty = await _shop_cart_qty(uid)
    return templates.TemplateResponse(
        "shop_order_thanks.html",
        {
            "request": request,
            "user": current_user,
            "order": order,
            "order_items": enriched,
            "cart_qty": cart_qty,
        },
    )


@router.get("/api/chain/decimal-balance")
async def api_decimal_balance(address: str = ""):
    """Публичное чтение баланса DEL по адресу (без авторизации)."""
    from services.decimal_chain import fetch_native_del_balance

    bal = await fetch_native_del_balance(address)
    if bal is None:
        return JSONResponse({"error": "invalid or unreachable"}, status_code=400)
    fmt = f"{bal:.12f}".rstrip("0").rstrip(".") or "0"
    return JSONResponse({"ok": True, "del": bal, "formatted": fmt})


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    current_user = await get_user_from_request(request)
    plan = None
    if current_user:
        uid = current_user.get("primary_user_id") or current_user["id"]
        plan = await check_subscription(uid)
    return templates.TemplateResponse(
        "chat.html",
        {"request": request, "user": current_user, "subscription_plan": plan},
    )


@router.get("/downtempo", response_class=HTMLResponse)
async def downtempo_radio_page(request: Request):
    """Страница радио Down Tempo (плейлист с сервера, без скачивания у пользователя)."""
    from services.radio_downtempo import get_playlist_version, list_tracks_ordered

    current_user = await get_user_from_request(request)
    tracks = await list_tracks_ordered()
    ver = await get_playlist_version()
    return templates.TemplateResponse(
        "downtempo.html",
        {
            "request": request,
            "user": current_user,
            "tracks": tracks,
            "playlist_version": ver,
        },
    )


@router.get("/api/radio/downtempo/playlist")
async def api_radio_downtempo_playlist():
    from services.radio_downtempo import get_playlist_version, list_tracks_ordered

    tracks = await list_tracks_ordered()
    ver = await get_playlist_version()
    return JSONResponse(
        {
            "version": ver,
            "tracks": [{"id": t["id"], "title": t["title"], "url": t["url"]} for t in tracks],
        }
    )


@router.get("/referral", response_class=HTMLResponse)
async def referral_program_page(request: Request):
    """Отдельная страница реферальной программы (ссылки, баланс, статистика)."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/referral")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    uid = user.get("primary_user_id") or user["id"]
    plan = await check_subscription(uid)
    code = await ensure_user_referral_code(uid)
    bot = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@") or "mushrooms_ai_bot"
    base = (settings.SITE_URL or "").strip().rstrip("/")
    ref_link = f"https://t.me/{bot}?start={code}" if code else ""
    ref_link_site = f"{base}/login?ref={code}" if code and base else ""
    ref_stats = await get_referral_stats(uid)
    display_user = user
    if user.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == uid))
        if primary:
            display_user = dict(primary)
            attach_screen_rim_prefs(display_user)
    from web.routes.user import compute_visible_blocks

    visible_block_keys = await compute_visible_blocks(uid, plan)
    return templates.TemplateResponse(
        "referral_program.html",
        {
            "request": request,
            "user": display_user,
            "ref_link": ref_link,
            "ref_link_site": ref_link_site,
            "ref_stats": ref_stats,
            "visible_block_keys": visible_block_keys,
        },
    )


@router.get("/community")
async def community(request: Request):
    # Mobile flow uses dedicated pages for feed/search/profile; keep /community as feed page.
    return await community_old(request)


@router.get("/community/_old", response_class=HTMLResponse)
async def community_old(request: Request):
    current_user = await get_user_from_request(request)

    if not current_user:
        post_count = await database.fetch_val(
            sa.select(sa.func.count()).select_from(community_posts).where(community_posts.c.approved == True)
        )
        member_count = await database.fetch_val(
            sa.select(sa.func.count()).select_from(users)
            .where(users.c.primary_user_id == None)
        )
        recent_members = await database.fetch_all(
            users.select()
            .where(users.c.primary_user_id == None)
            .order_by(users.c.created_at.desc())
            .limit(10)
        )
        preview_posts = await database.fetch_all(
            community_posts.select()
            .where(community_posts.c.approved == True)
            .order_by(community_posts.c.pinned.desc(), community_posts.c.created_at.desc())
            .limit(5)
        )
        return templates.TemplateResponse(
            "community_preview.html",
            {
                "request": request,
                "user": None,
                "post_count": post_count or 0,
                "member_count": member_count or 0,
                "recent_members": recent_members,
                "preview_posts": preview_posts,
            },
        )

    # Authenticated: full social network
    effective_user_id = current_user.get("primary_user_id") or current_user["id"]
    display_user = current_user
    if current_user.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == effective_user_id))
        if primary:
            display_user = dict(primary)
            attach_screen_rim_prefs(display_user)

    tab = request.query_params.get("tab", "all")  # all | following | popular | saved
    folder_id = request.query_params.get("folder")
    folder_id = int(folder_id) if folder_id and folder_id.isdigit() else None
    search = request.query_params.get("q", "").strip()

    base_query = community_posts.select().where(community_posts.c.approved == True)
    if folder_id:
        base_query = base_query.where(community_posts.c.folder_id == folder_id)
    if search:
        base_query = base_query.where(community_posts.c.content.ilike(f"%{search}%"))

    raw_posts = None
    if tab == "following":
        followed_ids_rows = await database.fetch_all(
            community_follows.select().where(community_follows.c.follower_id == effective_user_id)
        )
        followed_ids = [r["following_id"] for r in followed_ids_rows]
        if followed_ids:
            query = base_query.where(community_posts.c.user_id.in_(followed_ids))
            query = query.order_by(community_posts.c.pinned.desc(), community_posts.c.created_at.desc()).limit(30)
            raw_posts = await database.fetch_all(query)
        else:
            raw_posts = []
    elif tab == "popular":
        query = base_query.order_by(community_posts.c.likes_count.desc(), community_posts.c.created_at.desc()).limit(30)
        raw_posts = await database.fetch_all(query)
    elif tab == "saved":
        saved_rows_tab = await database.fetch_all(
            community_saved.select().where(community_saved.c.user_id == effective_user_id)
        )
        saved_post_ids = [r["post_id"] for r in saved_rows_tab]
        if saved_post_ids:
            query = base_query.where(community_posts.c.id.in_(saved_post_ids))
            query = query.order_by(community_posts.c.pinned.desc(), community_posts.c.created_at.desc()).limit(30)
            raw_posts = await database.fetch_all(query)
        else:
            raw_posts = []
    else:
        query = base_query.order_by(community_posts.c.pinned.desc(), community_posts.c.created_at.desc()).limit(30)
        raw_posts = await database.fetch_all(query)

    # Get saved post IDs for this user
    saved_rows = await database.fetch_all(
        community_saved.select().where(community_saved.c.user_id == effective_user_id)
    )
    saved_post_ids_set = {r["post_id"] for r in saved_rows}

    feed = []
    for p in raw_posts:
        author = None
        if p["user_id"]:
            raw_author = await database.fetch_one(users.select().where(users.c.id == p["user_id"]))
            if raw_author:
                if raw_author["primary_user_id"]:
                    primary_author = await database.fetch_one(
                        users.select().where(users.c.id == raw_author["primary_user_id"])
                    )
                    author = dict(primary_author) if primary_author else dict(raw_author)
                else:
                    author = dict(raw_author)
        post_count = 0
        if author:
            post_count = await database.fetch_val(
                sa.select(sa.func.count()).select_from(community_posts)
                .where(community_posts.c.user_id == author["id"])
            ) or 0
        lk = await database.fetch_one(
            community_likes.select()
            .where(community_likes.c.post_id == p["id"])
            .where(community_likes.c.user_id == effective_user_id)
        )
        liked = lk is not None
        saved = p["id"] in saved_post_ids_set
        # Check if we follow the author
        is_following = False
        if author and author["id"] != effective_user_id:
            fol = await database.fetch_one(
                community_follows.select()
                .where(community_follows.c.follower_id == effective_user_id)
                .where(community_follows.c.following_id == author["id"])
            )
            is_following = fol is not None
        folder_name = None
        if p["folder_id"]:
            fl = await database.fetch_one(community_folders.select().where(community_folders.c.id == p["folder_id"]))
            folder_name = fl["name"] if fl else None
        feed.append({
            "post": p,
            "author": author,
            "author_post_count": post_count,
            "liked": liked,
            "saved": saved,
            "is_following": is_following,
            "folder_name": folder_name,
        })

    all_folders = await database.fetch_all(
        community_folders.select().order_by(community_folders.c.name.asc())
    )
    my_folders = await database.fetch_all(
        community_folders.select()
        .where(community_folders.c.user_id == effective_user_id)
        .order_by(community_folders.c.created_at.asc())
    )
    my_post_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_posts)
        .where(community_posts.c.user_id == effective_user_id)
    ) or 0

    # Get display_user's full profile data (bio, followers_count, following_count)
    full_profile = await database.fetch_one(users.select().where(users.c.id == effective_user_id))
    if full_profile:
        display_user = dict(full_profile)
        attach_screen_rim_prefs(display_user)

    _wa = (display_user.get("wallet_address") or "").strip()
    shevelev_auto_sync = _wa.startswith("0x")

    return templates.TemplateResponse(
        "community.html",
        {
            "request": request,
            "user": display_user,
            "feed": feed,
            "folders": all_folders,
            "my_folders": my_folders,
            "sel_folder": folder_id,
            "my_post_count": my_post_count,
            "tab": tab,
            "search": search,
            "shevelev_token": shevelev_token_address(),
            "shevelev_auto_sync": shevelev_auto_sync,
        },
    )


@router.get("/community/post/new", response_class=HTMLResponse)
async def community_post_new_page(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login?next=/community/post/new")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg

    effective_user_id = current_user.get("primary_user_id") or current_user["id"]
    display_user = current_user
    if current_user.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == effective_user_id))
        if primary:
            display_user = dict(primary)
            attach_screen_rim_prefs(display_user)

    my_folders = await database.fetch_all(
        community_folders.select()
        .where(community_folders.c.user_id == effective_user_id)
        .order_by(community_folders.c.created_at.asc())
    )
    err = (request.query_params.get("error") or "").strip()

    return templates.TemplateResponse(
        "community_post_new.html",
        {
            "request": request,
            "user": display_user,
            "my_folders": my_folders,
            "error": err,
        },
    )


@router.get("/community/user-profile/{user_id}")
async def community_user_profile(request: Request, user_id: int):
    current_user = await get_user_from_request(request)
    u = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not u:
        return JSONResponse({"error": "not found"}, status_code=404)
    if u["primary_user_id"]:
        primary = await database.fetch_one(users.select().where(users.c.id == u["primary_user_id"]))
        if primary:
            u = primary
    profile_id = u["id"]
    post_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_posts).where(community_posts.c.user_id == profile_id)
    ) or 0
    is_following = False
    if current_user:
        viewer_id = current_user.get("primary_user_id") or current_user["id"]
        if viewer_id != profile_id:
            fol = await database.fetch_one(
                community_follows.select()
                .where(community_follows.c.follower_id == viewer_id)
                .where(community_follows.c.following_id == profile_id)
            )
            is_following = fol is not None
    return JSONResponse({
        "id": profile_id,
        "name": u["name"],
        "avatar": u["avatar"],
        "bio": u["bio"] if "bio" in u.keys() else None,
        "wallet": u["wallet_address"] if "wallet_address" in u.keys() else None,
        "post_count": post_count,
        "followers_count": u["followers_count"] if "followers_count" in u.keys() else 0,
        "following_count": u["following_count"] if "following_count" in u.keys() else 0,
        "is_following": is_following,
    })


_COMMUNITY_ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_COMMUNITY_MAX_SIZE = 8 * 1024 * 1024  # 8 MB


@router.post("/community/upload")
async def community_upload_photo(request: Request, file: UploadFile = File(...)):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    if file.content_type not in _COMMUNITY_ALLOWED:
        return JSONResponse({"error": "Допустимые форматы: JPEG, PNG, WebP, GIF"}, status_code=400)

    data = await file.read()
    if len(data) > _COMMUNITY_MAX_SIZE:
        return JSONResponse({"error": "Файл слишком большой (макс. 8 МБ)"}, status_code=400)

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    filename = f"{uuid.uuid4().hex}.{ext}"

    base = "/data" if os.path.exists("/data") else "./media"
    save_path = os.path.join(base, "community", filename)

    with open(save_path, "wb") as f:
        f.write(data)

    return JSONResponse({"ok": True, "url": f"/media/community/{filename}"})


def _rep_level(n: int) -> tuple[str, str]:
    if n >= 100:
        return ("👑", "Легенда")
    if n >= 51:
        return ("🔥", "Мастер")
    if n >= 21:
        return ("⚡", "Адепт")
    if n >= 6:
        return ("🍄", "Участник")
    return ("🌱", "Зерно")


@router.get("/community/profile/{user_id}", response_class=HTMLResponse)
async def community_profile(request: Request, user_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/community/profile/{user_id}")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg

    viewer_id = current_user.get("primary_user_id") or current_user["id"]

    # Resolve to primary account
    raw = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not raw:
        return HTMLResponse("Пользователь не найден", status_code=404)
    if raw["primary_user_id"]:
        primary = await database.fetch_one(users.select().where(users.c.id == raw["primary_user_id"]))
        if primary:
            raw = primary
    profile_id = raw["id"]

    # Public-safe profile data only
    profile = get_public_user_data(dict(raw))
    apply_token_privacy_for_viewer(profile, viewer_id, profile_id)

    # Unified account family (primary + linked secondary IDs)
    family_rows = await database.fetch_all(
        users.select().with_only_columns(users.c.id).where(
            sa.or_(users.c.id == profile_id, users.c.primary_user_id == profile_id)
        )
    )
    family_ids = sorted({int(r["id"]) for r in family_rows} | {int(profile_id)})

    # Post count & reputation
    post_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_posts)
        .where(community_posts.c.user_id.in_(family_ids))
        .where(community_posts.c.approved == True)
    ) or 0
    rep_emoji, rep_level = _rep_level(post_count)

    # Profile likes count
    profile_likes_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(profile_likes)
        .where(profile_likes.c.liked_user_id == profile_id)
    ) or 0

    # Has current viewer liked this profile?
    viewer_liked_profile = False
    is_following = False
    is_own = viewer_id == profile_id
    if not is_own:
        pl = await database.fetch_one(
            profile_likes.select()
            .where(profile_likes.c.user_id == viewer_id)
            .where(profile_likes.c.liked_user_id == profile_id)
        )
        viewer_liked_profile = pl is not None
        fol = await database.fetch_one(
            community_follows.select()
            .where(community_follows.c.follower_id == viewer_id)
            .where(community_follows.c.following_id == profile_id)
        )
        is_following = fol is not None

    # User's posts
    raw_posts = await database.fetch_all(
        community_posts.select()
        .where(community_posts.c.user_id.in_(family_ids))
        .where(community_posts.c.approved == True)
        .order_by(community_posts.c.pinned.desc(), community_posts.c.created_at.desc())
        .limit(30)
    )

    # Check liked/saved for each post by viewer
    saved_rows = await database.fetch_all(
        community_saved.select().where(community_saved.c.user_id == viewer_id)
    )
    saved_ids = {r["post_id"] for r in saved_rows}

    feed = []
    for p in raw_posts:
        lk = await database.fetch_one(
            community_likes.select()
            .where(community_likes.c.post_id == p["id"])
            .where(community_likes.c.user_id == viewer_id)
        )
        folder_name = None
        if p["folder_id"]:
            fl = await database.fetch_one(
                community_folders.select().where(community_folders.c.id == p["folder_id"])
            )
            folder_name = fl["name"] if fl else None
        feed.append({
            "post": p,
            "liked": lk is not None,
            "saved": p["id"] in saved_ids,
            "folder_name": folder_name,
        })

    # Profile circles (community_folders); пустой список допустим
    circles_raw = await database.fetch_all(
        community_folders.select()
        .where(community_folders.c.user_id == profile_id)
        .order_by(community_folders.c.created_at.asc(), community_folders.c.id.asc())
    )
    circles = []
    for c in circles_raw:
        cover = await database.fetch_one(
            community_posts.select()
            .where(community_posts.c.folder_id == c["id"])
            .where(community_posts.c.user_id.in_(family_ids))
            .where(community_posts.c.approved == True)
            .order_by(community_posts.c.created_at.asc())
            .limit(1)
        )
        count = await database.fetch_val(
            sa.select(sa.func.count()).select_from(community_posts)
            .where(community_posts.c.folder_id == c["id"])
            .where(community_posts.c.user_id.in_(family_ids))
            .where(community_posts.c.approved == True)
        ) or 0
        circles.append({
            "id": c["id"],
            "name": c["name"],
            "count": int(count or 0),
            "cover_image": (cover["image_url"] if cover else None),
            "cover_title": ((cover["title"] or cover["content"]) if cover else None),
        })

    vrow = await database.fetch_one(users.select().where(users.c.id == viewer_id))
    _vwa = (vrow.get("wallet_address") or "").strip() if vrow else ""
    shevelev_auto_sync = _vwa.startswith("0x")

    profile_plan = await check_subscription(profile_id)
    profile_plan_info = PLANS.get(profile_plan, PLANS["free"])

    raw_d = dict(raw)
    profile_ui_theme = (raw_d.get("profile_ui_theme") or "default").strip() or "default"
    if profile_ui_theme not in PROFILE_UI_THEME_IDS:
        profile_ui_theme = "default"

    return templates.TemplateResponse(
        "community_profile.html",
        {
            "request": request,
            "user": current_user,
            "current_user": current_user,
            "viewer_effective_id": viewer_id,
            "profile": profile,
            "profile_id": profile_id,
            "post_count": post_count,
            "rep_emoji": rep_emoji,
            "rep_level": rep_level,
            "profile_likes_count": profile_likes_count,
            "viewer_liked_profile": viewer_liked_profile,
            "is_following": is_following,
            "is_own": is_own,
            "feed": feed,
            "circles": circles,
            "max_profile_circles": MAX_PROFILE_CIRCLES,
            "shevelev_token": shevelev_token_address(),
            "shevelev_auto_sync": shevelev_auto_sync,
            "profile_plan": profile_plan,
            "profile_plan_name": profile_plan_info.get("name") or "",
            "profile_ui_theme": profile_ui_theme,
        },
    )


@router.get("/community/profile/{user_id}/circles")
async def get_profile_circles(request: Request, user_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    raw = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not raw:
        return JSONResponse({"ok": False, "error": "Пользователь не найден"}, status_code=404)
    if raw["primary_user_id"]:
        primary = await database.fetch_one(users.select().where(users.c.id == raw["primary_user_id"]))
        if primary:
            raw = primary
    profile_id = int(raw["id"])
    family_rows = await database.fetch_all(
        users.select().with_only_columns(users.c.id).where(
            sa.or_(users.c.id == profile_id, users.c.primary_user_id == profile_id)
        )
    )
    family_ids = sorted({int(r["id"]) for r in family_rows} | {int(profile_id)})
    circles_raw = await database.fetch_all(
        community_folders.select()
        .where(community_folders.c.user_id == profile_id)
        .order_by(community_folders.c.created_at.asc(), community_folders.c.id.asc())
    )
    circles = []
    for c in circles_raw:
        cover = await database.fetch_one(
            community_posts.select()
            .where(community_posts.c.folder_id == c["id"])
            .where(community_posts.c.user_id.in_(family_ids))
            .where(community_posts.c.approved == True)
            .order_by(community_posts.c.created_at.asc())
            .limit(1)
        )
        circles.append({
            "id": c["id"],
            "name": c["name"],
            "cover_image": (cover["image_url"] if cover else None),
            "cover_title": ((cover["title"] or cover["content"]) if cover else None),
        })
    return JSONResponse({"ok": True, "circles": circles, "max": MAX_PROFILE_CIRCLES})


@router.post("/community/circle")
async def create_community_circle(request: Request, name: str = Form("Кружок")):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    uid = int(current_user.get("primary_user_id") or current_user["id"])
    existing = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_folders).where(community_folders.c.user_id == uid)
    ) or 0
    if int(existing) >= MAX_PROFILE_CIRCLES:
        return JSONResponse({"ok": False, "error": "Не больше 5 кружков"}, status_code=400)
    clean_name = (name or "").strip()[:80] or "Кружок"
    fid = await database.execute(
        community_folders.insert().values(user_id=uid, name=clean_name)
    )
    return JSONResponse({"ok": True, "id": fid, "name": clean_name})


@router.post("/community/circle/{circle_id}/delete")
async def delete_community_circle(request: Request, circle_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    uid = int(current_user.get("primary_user_id") or current_user["id"])
    circle = await database.fetch_one(
        community_folders.select().where(community_folders.c.id == circle_id)
    )
    if not circle:
        return JSONResponse({"ok": False, "error": "Кружок не найден"}, status_code=404)
    if int(circle["user_id"] or 0) != uid:
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    family_ids = await _profile_family_ids(uid)
    await database.execute(
        community_posts.update()
        .where(community_posts.c.folder_id == circle_id)
        .where(community_posts.c.user_id.in_(family_ids))
        .values(folder_id=None)
    )
    await database.execute(
        community_folders.delete().where(community_folders.c.id == circle_id)
    )
    return JSONResponse({"ok": True})


@router.get("/community/circle/{circle_id}/picker-posts")
async def circle_picker_posts(request: Request, circle_id: int, q: str = ""):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    uid = int(current_user.get("primary_user_id") or current_user["id"])
    circle = await database.fetch_one(
        community_folders.select().where(community_folders.c.id == circle_id)
    )
    if not circle:
        return JSONResponse({"ok": False, "error": "Кружок не найден"}, status_code=404)
    if int(circle["user_id"] or 0) != uid:
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    family_ids = await _profile_family_ids(uid)
    query = (
        community_posts.select()
        .where(community_posts.c.user_id.in_(family_ids))
        .where(community_posts.c.approved == True)
        .order_by(community_posts.c.created_at.desc())
        .limit(500)
    )
    search = (q or "").strip()
    if search:
        query = query.where(
            sa.or_(
                community_posts.c.title.ilike(f"%{search}%"),
                community_posts.c.content.ilike(f"%{search}%"),
            )
        )
    rows = await database.fetch_all(query)
    posts = []
    for p in rows:
        posts.append({
            "id": p["id"],
            "title": (p["title"] or (p["content"] or "")[:60] or f"Пост #{p['id']}"),
            "image_url": p.get("image_url"),
            "in_circle": int(p["folder_id"] or 0) == int(circle_id),
        })
    return JSONResponse({"ok": True, "posts": posts})


@router.post("/community/circle/{circle_id}/attach")
async def attach_post_to_circle(request: Request, circle_id: int, post_id: int = Form(...)):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    uid = int(current_user.get("primary_user_id") or current_user["id"])
    circle = await database.fetch_one(
        community_folders.select().where(community_folders.c.id == circle_id)
    )
    if not circle:
        return JSONResponse({"ok": False, "error": "Кружок не найден"}, status_code=404)
    if int(circle["user_id"] or 0) != uid:
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    family_ids = await _profile_family_ids(uid)
    post = await database.fetch_one(
        community_posts.select()
        .where(community_posts.c.id == post_id)
        .where(community_posts.c.user_id.in_(family_ids))
    )
    if not post:
        return JSONResponse({"ok": False, "error": "Пост не найден"}, status_code=404)
    await database.execute(
        community_posts.update()
        .where(community_posts.c.id == post_id)
        .values(folder_id=circle_id)
    )
    return JSONResponse({"ok": True})


@router.post("/community/circle/{circle_id}/save")
async def circle_save_posts(request: Request, circle_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    uid = int(current_user.get("primary_user_id") or current_user["id"])
    circle = await database.fetch_one(
        community_folders.select().where(community_folders.c.id == circle_id)
    )
    if not circle:
        return JSONResponse({"ok": False, "error": "Кружок не найден"}, status_code=404)
    if int(circle["user_id"] or 0) != uid:
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw_ids = body.get("post_ids") if isinstance(body, dict) else None
    if not isinstance(raw_ids, list):
        raw_ids = []
    wanted: set[int] = set()
    for x in raw_ids:
        try:
            wanted.add(int(x))
        except (TypeError, ValueError):
            continue
    family_ids = await _profile_family_ids(uid)
    await database.execute(
        community_posts.update()
        .where(community_posts.c.folder_id == circle_id)
        .where(community_posts.c.user_id.in_(family_ids))
        .values(folder_id=None)
    )
    for pid in wanted:
        post = await database.fetch_one(
            community_posts.select()
            .where(community_posts.c.id == pid)
            .where(community_posts.c.user_id.in_(family_ids))
            .where(community_posts.c.approved == True)
        )
        if post:
            await database.execute(
                community_posts.update()
                .where(community_posts.c.id == pid)
                .values(folder_id=circle_id)
            )
    return JSONResponse({"ok": True})


@router.post("/community/circle/{circle_id}/rename")
async def circle_rename(request: Request, circle_id: int, name: str = Form(...)):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    uid = int(current_user.get("primary_user_id") or current_user["id"])
    circle = await database.fetch_one(
        community_folders.select().where(community_folders.c.id == circle_id)
    )
    if not circle:
        return JSONResponse({"ok": False, "error": "Кружок не найден"}, status_code=404)
    if int(circle["user_id"] or 0) != uid:
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    clean = (name or "").strip()[:80] or "Кружок"
    await database.execute(
        community_folders.update()
        .where(community_folders.c.id == circle_id)
        .values(name=clean)
    )
    return JSONResponse({"ok": True, "name": clean})


@router.get("/community/profile/{user_id}/circle/{circle_id}", response_class=HTMLResponse)
async def community_profile_circle_page(request: Request, user_id: int, circle_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/community/profile/{user_id}/circle/{circle_id}")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg
    viewer_id = int(current_user.get("primary_user_id") or current_user["id"])
    raw = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not raw:
        return HTMLResponse("Пользователь не найден", status_code=404)
    if raw["primary_user_id"]:
        primary = await database.fetch_one(users.select().where(users.c.id == raw["primary_user_id"]))
        if primary:
            raw = primary
    profile_id = int(raw["id"])
    circle = await database.fetch_one(
        community_folders.select()
        .where(community_folders.c.id == circle_id)
        .where(community_folders.c.user_id == profile_id)
    )
    if not circle:
        return HTMLResponse("Кружок не найден", status_code=404)
    family_rows = await database.fetch_all(
        users.select().with_only_columns(users.c.id).where(
            sa.or_(users.c.id == profile_id, users.c.primary_user_id == profile_id)
        )
    )
    family_ids = sorted({int(r["id"]) for r in family_rows} | {int(profile_id)})
    posts = await database.fetch_all(
        community_posts.select()
        .where(community_posts.c.folder_id == circle_id)
        .where(community_posts.c.user_id.in_(family_ids))
        .where(community_posts.c.approved == True)
        .order_by(community_posts.c.created_at.desc())
    )
    profile = get_public_user_data(dict(raw))
    apply_token_privacy_for_viewer(profile, viewer_id, profile_id)
    post_ids_list = [p["id"] for p in posts]
    liked_set = set()
    if post_ids_list:
        lk_rows = await database.fetch_all(
            community_likes.select()
            .where(community_likes.c.post_id.in_(post_ids_list))
            .where(community_likes.c.user_id == viewer_id)
        )
        liked_set = {int(r["post_id"]) for r in lk_rows}
    feed_items = [{"post": p, "liked": p["id"] in liked_set} for p in posts]
    return templates.TemplateResponse(
        "community_circle.html",
        {
            "request": request,
            "user": current_user,
            "profile": profile,
            "profile_id": profile_id,
            "circle": dict(circle),
            "posts": posts,
            "feed_items": feed_items,
            "back_url": f"/community/profile/{profile_id}",
        },
    )


@router.get("/community/post/{post_id}", response_class=HTMLResponse)
async def community_post_page(request: Request, post_id: int, back: str = ""):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/community/post/{post_id}")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg

    viewer_id = current_user.get("primary_user_id") or current_user["id"]
    post = await database.fetch_one(
        community_posts.select()
        .where(community_posts.c.id == post_id)
        .where(community_posts.c.approved == True)
    )
    if not post:
        return HTMLResponse("Пост не найден", status_code=404)

    author = await database.fetch_one(users.select().where(users.c.id == post["user_id"]))
    liked = await database.fetch_one(
        community_likes.select()
        .where(community_likes.c.post_id == post_id)
        .where(community_likes.c.user_id == viewer_id)
    )
    saved = await database.fetch_one(
        community_saved.select()
        .where(community_saved.c.post_id == post_id)
        .where(community_saved.c.user_id == viewer_id)
    )
    is_owner = int(post["user_id"] or 0) == int(viewer_id)
    back_url = (back or "").strip() or request.headers.get("referer") or "/community"

    return templates.TemplateResponse(
        "community_post.html",
        {
            "request": request,
            "user": current_user,
            "post": post,
            "author": author,
            "is_liked": liked is not None,
            "is_saved": saved is not None,
            "is_owner": is_owner,
            "back_url": back_url,
        },
    )


@router.get("/community/post/{post_id}/comments", response_class=HTMLResponse)
async def community_post_comments_page(request: Request, post_id: int, back: str = ""):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/community/post/{post_id}/comments")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg

    post = await database.fetch_one(
        community_posts.select()
        .where(community_posts.c.id == post_id)
        .where(community_posts.c.approved == True)
    )
    if not post:
        return HTMLResponse("Пост не найден", status_code=404)

    author = await database.fetch_one(users.select().where(users.c.id == post["user_id"]))
    back_url = (back or "").strip() or request.headers.get("referer") or "/community"

    return templates.TemplateResponse(
        "community_post_comments.html",
        {
            "request": request,
            "user": current_user,
            "post": post,
            "author": author,
            "back_url": back_url,
        },
    )


@router.get("/community/post/{post_id}/photo", response_class=HTMLResponse)
async def community_post_photo_page(request: Request, post_id: int, back: str = ""):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/community/post/{post_id}/photo")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg
    post = await database.fetch_one(
        community_posts.select()
        .where(community_posts.c.id == post_id)
        .where(community_posts.c.approved == True)
    )
    if not post:
        return HTMLResponse("Пост не найден", status_code=404)
    if not (post.get("image_url") or "").strip():
        return RedirectResponse(f"/community/post/{post_id}")
    back_url = (back or "").strip() or request.headers.get("referer") or "/community"
    return templates.TemplateResponse(
        "community_post_photo.html",
        {
            "request": request,
            "post": post,
            "back_url": back_url,
        },
    )


async def _resolve_community_profile_id(user_id: int):
    """Как на странице профиля: id в URL → основной аккаунт."""
    raw = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not raw:
        return None
    r = dict(raw)
    if r.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == r["primary_user_id"]))
        if primary:
            r = dict(primary)
    return r["id"]


async def _user_for_social_list(user_id: int):
    """Один элемент списка: id для ссылки на /community/profile/{id}, имя, аватар (основной аккаунт)."""
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return None
    r = dict(row)
    if r.get("primary_user_id"):
        p = await database.fetch_one(users.select().where(users.c.id == r["primary_user_id"]))
        if p:
            r = dict(p)
    return {
        "id": r["id"],
        "name": (r.get("name") or "").strip() or "Участник",
        "avatar": r.get("avatar"),
    }


@router.get("/community/profile/{user_id}/followers")
async def api_profile_followers(request: Request, user_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    profile_id = await _resolve_community_profile_id(user_id)
    if profile_id is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    rows = await database.fetch_all(
        community_follows.select()
        .where(community_follows.c.following_id == profile_id)
        .order_by(community_follows.c.created_at.desc())
        .limit(500)
    )
    out = []
    seen = set()
    for row in rows:
        u = await _user_for_social_list(row["follower_id"])
        if u and u["id"] not in seen:
            seen.add(u["id"])
            out.append(u)
    return JSONResponse({"ok": True, "users": out})


@router.get("/community/profile/{user_id}/following")
async def api_profile_following(request: Request, user_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    profile_id = await _resolve_community_profile_id(user_id)
    if profile_id is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    rows = await database.fetch_all(
        community_follows.select()
        .where(community_follows.c.follower_id == profile_id)
        .order_by(community_follows.c.created_at.desc())
        .limit(500)
    )
    out = []
    seen = set()
    for row in rows:
        u = await _user_for_social_list(row["following_id"])
        if u and u["id"] not in seen:
            seen.add(u["id"])
            out.append(u)
    return JSONResponse({"ok": True, "users": out})


@router.get("/community/profile/{user_id}/followers/view", response_class=HTMLResponse)
async def profile_followers_view_page(request: Request, user_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/community/profile/{user_id}/followers/view")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg
    profile_id = await _resolve_community_profile_id(user_id)
    if profile_id is None:
        return HTMLResponse("Не найден", status_code=404)
    rows = await database.fetch_all(
        community_follows.select()
        .where(community_follows.c.following_id == profile_id)
        .order_by(community_follows.c.created_at.desc())
        .limit(500)
    )
    users_list = []
    seen = set()
    for row in rows:
        u = await _user_for_social_list(row["follower_id"])
        if u and u["id"] not in seen:
            seen.add(u["id"])
            users_list.append(u)
    return templates.TemplateResponse(
        "community_social_list.html",
        {
            "request": request,
            "user": current_user,
            "page_title": "Подписчики",
            "people": users_list,
            "profile_id": profile_id,
            "back_href": f"/community/profile/{profile_id}",
        },
    )


@router.get("/community/profile/{user_id}/following/view", response_class=HTMLResponse)
async def profile_following_view_page(request: Request, user_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/community/profile/{user_id}/following/view")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg
    profile_id = await _resolve_community_profile_id(user_id)
    if profile_id is None:
        return HTMLResponse("Не найден", status_code=404)
    rows = await database.fetch_all(
        community_follows.select()
        .where(community_follows.c.follower_id == profile_id)
        .order_by(community_follows.c.created_at.desc())
        .limit(500)
    )
    users_list = []
    seen = set()
    for row in rows:
        u = await _user_for_social_list(row["following_id"])
        if u and u["id"] not in seen:
            seen.add(u["id"])
            users_list.append(u)
    return templates.TemplateResponse(
        "community_social_list.html",
        {
            "request": request,
            "user": current_user,
            "page_title": "Подписки",
            "people": users_list,
            "profile_id": profile_id,
            "back_href": f"/community/profile/{profile_id}",
        },
    )


@router.get("/community/profile/{user_id}/publications/view", response_class=HTMLResponse)
async def profile_publications_view_page(request: Request, user_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/community/profile/{user_id}/publications/view")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg
    raw = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not raw:
        return HTMLResponse("Не найден", status_code=404)
    if raw["primary_user_id"]:
        primary = await database.fetch_one(users.select().where(users.c.id == raw["primary_user_id"]))
        if primary:
            raw = primary
    profile_id = int(raw["id"])
    family_rows = await database.fetch_all(
        users.select().with_only_columns(users.c.id).where(
            sa.or_(users.c.id == profile_id, users.c.primary_user_id == profile_id)
        )
    )
    family_ids = [int(r["id"]) for r in family_rows] + [profile_id]
    family_ids = sorted(set(family_ids))
    posts_rows = await database.fetch_all(
        community_posts.select()
        .where(community_posts.c.user_id.in_(family_ids))
        .where(community_posts.c.approved == True)
        .order_by(community_posts.c.pinned.desc(), community_posts.c.created_at.desc())
        .limit(200)
    )
    posts = [dict(p) for p in posts_rows]
    return templates.TemplateResponse(
        "community_publications_list.html",
        {
            "request": request,
            "user": current_user,
            "profile_id": profile_id,
            "posts": posts,
            "back_href": f"/community/profile/{profile_id}",
        },
    )


# ─── Direct Messages ──────────────────────────────────────────────────────────

async def _get_conversations(user_id: int) -> list:
    """Return list of unique DM partners with last message & unread count."""
    try:
        rows = await database.fetch_all(sa.text("""
            SELECT
                CASE WHEN dm.sender_id = :uid THEN dm.recipient_id ELSE dm.sender_id END AS other_id,
                MAX(dm.id) AS last_id
            FROM direct_messages dm
            WHERE (dm.sender_id = :uid OR dm.recipient_id = :uid)
              AND dm.is_system = false
            GROUP BY other_id
            ORDER BY last_id DESC
            LIMIT 50
        """), {"uid": user_id})

        sys_count = await database.fetch_val(sa.text(
            "SELECT COUNT(*) FROM direct_messages WHERE recipient_id=:uid AND is_system=true AND is_read=false"
        ), {"uid": user_id}) or 0

        convs = []
        if sys_count > 0:
            last_sys = await database.fetch_one(sa.text(
                "SELECT text, created_at FROM direct_messages WHERE recipient_id=:uid AND is_system=true ORDER BY id DESC LIMIT 1"
            ), {"uid": user_id})
            convs.append({
                "other_id": 0,
                "name": "🛡️ Система",
                "avatar": None,
                "last_text": last_sys["text"] if last_sys else "",
                "unread": sys_count,
                "last_time": last_sys["created_at"].isoformat() if last_sys and last_sys.get("created_at") else "",
            })

        for r in rows:
            other_id = r["other_id"]
            if not other_id:
                continue
            other = await database.fetch_one(users.select().where(users.c.id == other_id))
            last_msg = await database.fetch_one(sa.text(
                "SELECT text, created_at FROM direct_messages WHERE id=:lid"
            ), {"lid": r["last_id"]})
            unread = await database.fetch_val(sa.text(
                "SELECT COUNT(*) FROM direct_messages WHERE sender_id=:oid AND recipient_id=:uid AND is_read=false AND is_system=false"
            ), {"oid": other_id, "uid": user_id}) or 0
            convs.append({
                "other_id": other_id,
                "name": other["name"] if other else "Участник",
                "avatar": other["avatar"] if other else None,
                "last_text": last_msg["text"] if last_msg else "",
                "unread": unread,
                "last_time": last_msg["created_at"].isoformat() if last_msg and last_msg.get("created_at") else "",
            })
        return convs
    except Exception as e:
        print(f"[messages] _get_conversations error: {e}")
        return []


@router.get("/messages/unread-count")
async def messages_unread_count(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"count": 0})
    uid = current_user.get("primary_user_id") or current_user["id"]
    try:
        if _is_free_restricted_user(current_user):
            count = await database.fetch_val(sa.text(
                "SELECT COUNT(*) FROM direct_messages WHERE recipient_id=:uid AND is_system=true AND is_read=false"
            ), {"uid": uid}) or 0
        else:
            count = await database.fetch_val(sa.text(
                "SELECT COUNT(*) FROM direct_messages WHERE recipient_id=:uid AND is_read=false"
            ), {"uid": uid}) or 0
        return JSONResponse({"count": count})
    except Exception as e:
        logging.getLogger(__name__).warning("messages unread-count: %s", e)
        return JSONResponse({"count": 0})


@router.get("/messages/conversations")
async def messages_conversations_api(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = current_user.get("primary_user_id") or current_user["id"]
    convs = await _get_conversations(uid)
    if _is_free_restricted_user(current_user):
        convs = [c for c in convs if int(c.get("other_id") or 0) == 0]
    return JSONResponse({"conversations": convs})


@router.get("/messages/dialogs")
async def messages_dialogs_api(request: Request):
    """JSON endpoint for community panel — list of dialogs."""
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = current_user.get("primary_user_id") or current_user["id"]
    convs = await _get_conversations(uid)
    if _is_free_restricted_user(current_user):
        convs = [c for c in convs if int(c.get("other_id") or 0) == 0]
    dialogs = []
    for c in convs:
        dialogs.append({
            "user_id": c["other_id"],
            "name": c["name"],
            "avatar": c["avatar"],
            "last_message": c["last_text"],
            "unread": c["unread"],
            "time": "",
            "is_system": c["other_id"] == 0,
        })
    return JSONResponse({"dialogs": dialogs})


@router.get("/messages/thread/{other_id}")
async def messages_thread_api(request: Request, other_id: int):
    """JSON endpoint for community panel — messages in a thread."""
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = current_user.get("primary_user_id") or current_user["id"]
    if _is_free_restricted_user(current_user) and other_id != 0:
        return JSONResponse({"error": "plan_required", "redirect": "/subscriptions"}, status_code=403)

    if other_id == 0:
        await database.execute(sa.text(
            "UPDATE direct_messages SET is_read=true WHERE recipient_id=:uid AND is_system=true AND is_read=false"
        ), {"uid": uid})
        rows = await database.fetch_all(sa.text(
            "SELECT id, sender_id, text, is_system, created_at FROM direct_messages WHERE recipient_id=:uid AND is_system=true ORDER BY created_at ASC LIMIT 100"
        ), {"uid": uid})
    else:
        await database.execute(sa.text(
            "UPDATE direct_messages SET is_read=true WHERE sender_id=:oid AND recipient_id=:uid AND is_read=false AND is_system=false"
        ), {"oid": other_id, "uid": uid})
        rows = await database.fetch_all(sa.text("""
            SELECT id, sender_id, text, is_system, created_at FROM direct_messages
            WHERE (sender_id=:uid AND recipient_id=:oid)
               OR (sender_id=:oid AND recipient_id=:uid AND is_system=false)
            ORDER BY created_at ASC LIMIT 100
        """), {"uid": uid, "oid": other_id})

    messages = []
    for r in rows:
        messages.append({
            "id": r["id"],
            "is_mine": r["sender_id"] == uid,
            "is_system": r["is_system"],
            "text": r["text"],
            "time": r["created_at"].strftime("%H:%M") if r["created_at"] else "",
        })
    return JSONResponse({"messages": messages})


@router.get("/messages", response_class=HTMLResponse)
async def messages_list(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login?next=/messages")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg
    uid = current_user.get("primary_user_id") or current_user["id"]
    convs = await _get_conversations(uid)
    if _is_free_restricted_user(current_user):
        convs = [c for c in convs if int(c.get("other_id") or 0) == 0]
    return templates.TemplateResponse(
        "messages.html",
        {
            "request": request,
            "user": current_user,
            "conversations": convs,
            "active_user_id": None,
            "chat_messages": [],
            "chat_partner": None,
        },
    )


@router.get("/messages/{other_id}", response_class=HTMLResponse)
async def messages_thread(request: Request, other_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse(f"/login?next=/messages/{other_id}")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg
    uid = current_user.get("primary_user_id") or current_user["id"]
    if _is_free_restricted_user(current_user) and other_id != 0:
        return RedirectResponse("/messages/0")
    try:
        if other_id == 0:
            await database.execute(sa.text(
                "UPDATE direct_messages SET is_read=true WHERE recipient_id=:uid AND is_system=true AND is_read=false"
            ), {"uid": uid})
            chat_partner = {"id": 0, "name": "🛡️ Система", "avatar": None}
            chat_messages_raw = await database.fetch_all(sa.text(
                "SELECT * FROM direct_messages WHERE recipient_id=:uid AND is_system=true ORDER BY created_at ASC LIMIT 100"
            ), {"uid": uid})
        else:
            await database.execute(sa.text(
                "UPDATE direct_messages SET is_read=true WHERE sender_id=:oid AND recipient_id=:uid AND is_read=false AND is_system=false"
            ), {"oid": other_id, "uid": uid})
            partner_row = await database.fetch_one(users.select().where(users.c.id == other_id))
            if not partner_row:
                return RedirectResponse("/messages")
            chat_partner = {"id": other_id, "name": partner_row["name"], "avatar": partner_row["avatar"]}
            chat_messages_raw = await database.fetch_all(sa.text("""
                SELECT * FROM direct_messages
                WHERE (sender_id=:uid AND recipient_id=:oid)
                   OR (sender_id=:oid AND recipient_id=:uid AND is_system=false)
                ORDER BY created_at ASC LIMIT 100
            """), {"uid": uid, "oid": other_id})

        chat_messages = [dict(m) for m in chat_messages_raw]
        convs = await _get_conversations(uid)
        return templates.TemplateResponse(
            "messages.html",
            {
                "request": request,
                "user": current_user,
                "conversations": convs,
                "active_user_id": other_id,
                "chat_messages": chat_messages,
                "chat_partner": chat_partner,
                "current_uid": uid,
            },
        )
    except Exception as e:
        print(f"[messages] thread error: {e}")
        return RedirectResponse("/messages")


@router.get("/messages/poll/{other_id}")
async def poll_messages(request: Request, other_id: int, after: int = 0):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = current_user.get("primary_user_id") or current_user["id"]
    if _is_free_restricted_user(current_user) and other_id != 0:
        return JSONResponse({"messages": []})
    rows = await database.fetch_all(sa.text("""
        SELECT id, sender_id, text, is_read, created_at FROM direct_messages
        WHERE id > :after
          AND ((sender_id=:uid AND recipient_id=:oid) OR (sender_id=:oid AND recipient_id=:uid AND is_system=false))
        ORDER BY id ASC LIMIT 50
    """), {"after": after, "uid": uid, "oid": other_id})
    # Mark as read
    await database.execute(sa.text(
        "UPDATE direct_messages SET is_read=true WHERE sender_id=:oid AND recipient_id=:uid AND is_read=false"
    ), {"oid": other_id, "uid": uid})
    msgs = [{"id": r["id"], "sender_id": r["sender_id"], "text": r["text"],
             "is_read": r["is_read"],
             "created_at": r["created_at"].strftime("%H:%M") if r["created_at"] else ""} for r in rows]
    return JSONResponse({"messages": msgs})


@router.post("/messages/{other_id}")
async def send_message(request: Request, other_id: int):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = current_user.get("primary_user_id") or current_user["id"]
    if _is_free_restricted_user(current_user):
        return JSONResponse({"error": "plan_required", "redirect": "/subscriptions"}, status_code=403)

    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        body = await request.json()
        text = (body.get("text") or "").strip()
    else:
        form = await request.form()
        text = (form.get("text") or "").strip()

    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)

    try:
        row = await database.fetch_one_write(
            sa.text(
                "INSERT INTO direct_messages (sender_id, recipient_id, text, is_read, is_system) "
                "VALUES (:s, :r, :t, false, false) RETURNING id"
            ).bindparams(s=uid, r=other_id, t=text)
        )
    except Exception as e:
        print(f"[messages] send error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    msg_id = row["id"] if row else None
    if other_id and other_id != 0:
        try:
            recipient = await database.fetch_one(users.select().where(users.c.id == other_id))
            if recipient:
                tg_id = recipient.get("tg_id") or recipient.get("linked_tg_id")
                if tg_id:
                    from services.notify_user_stub import notify_user_dm_with_read_button

                    sender_row = await database.fetch_one(users.select().where(users.c.id == uid))
                    nm = (sender_row.get("name") if sender_row else None) or "Участник"
                    await notify_user_dm_with_read_button(tg_id, nm, text, f"/messages/{uid}")
        except Exception:
            pass

    return JSONResponse({
        "ok": True,
        "id": msg_id,
        "text": text,
        "sender_id": uid,
        "created_at": "сейчас",
    })


# ─── Contact / Feedback ───────────────────────────────────────────────────────

@router.post("/contact")
async def contact_feedback(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "empty"}, status_code=400)
    try:
        await notify_new_feedback(text=message, user_label="Гость")
    except Exception:
        pass
    return JSONResponse({"ok": True})


# ─── Community Members Page ────────────────────────────────────────────────────

@router.get("/community/members", response_class=HTMLResponse)
async def community_members(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login?next=/community/members")
    leg = await legal_acceptance_redirect(request, current_user)
    if leg:
        return leg

    viewer_id = current_user.get("primary_user_id") or current_user["id"]

    search = request.query_params.get("q", "").strip()
    level_filter = request.query_params.get("level", "")
    embed = request.query_params.get("embed", "").strip() == "1"
    page = int(request.query_params.get("page", 1))
    per_page = 24
    offset = (page - 1) * per_page

    # Build query for primary accounts only
    base = users.select().where(users.c.primary_user_id == None)
    if search:
        base = base.where(users.c.name.ilike(f"%{search}%"))

    count_q = sa.select(sa.func.count()).select_from(users).where(users.c.primary_user_id == None)
    if search:
        count_q = count_q.where(users.c.name.ilike(f"%{search}%"))
    total = await database.fetch_val(count_q) or 0

    raw_members = await database.fetch_all(
        base.order_by(users.c.followers_count.desc(), users.c.created_at.desc())
        .limit(per_page).offset(offset)
    )

    member_cards = []
    for m in raw_members:
        mid = m["id"]
        post_count = await database.fetch_val(
            sa.select(sa.func.count()).select_from(community_posts)
            .where(community_posts.c.user_id == mid)
            .where(community_posts.c.approved == True)
        ) or 0
        rep_emoji, rep_level_name = _rep_level(post_count)

        # Skip if level filter doesn't match
        if level_filter and rep_level_name != level_filter:
            continue

        is_following = False
        if mid != viewer_id:
            fol = await database.fetch_one(
                community_follows.select()
                .where(community_follows.c.follower_id == viewer_id)
                .where(community_follows.c.following_id == mid)
            )
            is_following = fol is not None

        member_cards.append({
            "id": mid,
            "name": m["name"],
            "avatar": m["avatar"],
            "bio": m.get("bio"),
            "followers_count": m.get("followers_count") or 0,
            "post_count": post_count,
            "rep_emoji": rep_emoji,
            "rep_level": rep_level_name,
            "is_following": is_following,
            "is_own": mid == viewer_id,
        })

    total_pages = (total + per_page - 1) // per_page

    return templates.TemplateResponse(
        "community_members.html",
        {
            "request": request,
            "user": current_user,
            "members": member_cards,
            "search": search,
            "level_filter": level_filter,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "levels": ["Зерно", "Участник", "Адепт", "Мастер", "Легенда"],
            "embed": embed,
        },
    )
