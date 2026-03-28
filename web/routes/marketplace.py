"""Marketplace router — Wildberries-style marketplace for MushroomsAI."""
import asyncio
import io
import json
import logging
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth.session import get_user_from_request
from db.database import database
from web.templates_utils import Jinja2Templates

_logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

ITEMS_PER_PAGE = 20


# ── Cloudinary helpers ────────────────────────────────────────────────────────

def _get_cloudinary():
    import cloudinary
    import cloudinary.uploader
    cloudinary.config(
        cloud_name="du1aaf27r",
        api_key="189975495191847",
        api_secret="tqEFmI9ED4i5qUSPApDD6bHc9lw",
        secure=True,
    )
    return cloudinary.uploader


async def _upload_photo(file_bytes: bytes, filename: str) -> str:
    def _upload():
        uploader = _get_cloudinary()
        result = uploader.upload(
            io.BytesIO(file_bytes),
            resource_type="image",
            folder="mushroomsai_market",
            unique_filename=True,
        )
        return result["secure_url"]
    return await asyncio.get_event_loop().run_in_executor(None, _upload)


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_product_photos(product_id: int):
    rows = await database.fetch_all(
        sa.text("SELECT * FROM marketplace_photos WHERE product_id=:pid ORDER BY position ASC"),
        {"pid": product_id}
    )
    return [dict(r) for r in rows]


async def _enrich_products(rows):
    products = []
    for r in rows:
        p = dict(r)
        p["photos"] = await _get_product_photos(p["id"])
        products.append(p)
    return products


async def _get_cart_count(user_id: int) -> int:
    val = await database.fetch_val(
        sa.text("SELECT COUNT(*) FROM marketplace_cart WHERE user_id=:uid"),
        {"uid": user_id}
    )
    return int(val or 0)


async def _get_fav_count(user_id: int) -> int:
    val = await database.fetch_val(
        sa.text("SELECT COUNT(*) FROM marketplace_favorites WHERE user_id=:uid"),
        {"uid": user_id}
    )
    return int(val or 0)


async def _get_categories():
    rows = await database.fetch_all(
        sa.text("""
            SELECT category, COUNT(*) as cnt
            FROM marketplace_products
            WHERE is_active=true AND category IS NOT NULL AND category != ''
            GROUP BY category
            ORDER BY cnt DESC
        """)
    )
    return [dict(r) for r in rows]


# ── Public pages ──────────────────────────────────────────────────────────────

@router.get("/marketplace", response_class=HTMLResponse)
async def marketplace_index(
    request: Request,
    page: int = 1,
    sort: str = "popular",
    category: str = "",
    q: str = "",
    min_price: str = "",
    max_price: str = "",
    in_stock: str = "",
    rating: str = "",
):
    user = await get_user_from_request(request)
    offset = (page - 1) * ITEMS_PER_PAGE

    where_clauses = ["p.is_active=true"]
    params: dict = {"limit": ITEMS_PER_PAGE, "offset": offset}

    if category:
        where_clauses.append("p.category=:category")
        params["category"] = category
    if q:
        where_clauses.append("(p.title ILIKE :q OR p.description ILIKE :q)")
        params["q"] = f"%{q}%"
    if min_price:
        try:
            where_clauses.append("p.price >= :min_price")
            params["min_price"] = float(min_price)
        except Exception:
            pass
    if max_price:
        try:
            where_clauses.append("p.price <= :max_price")
            params["max_price"] = float(max_price)
        except Exception:
            pass
    if in_stock == "1":
        where_clauses.append("p.in_stock=true")
    if rating:
        try:
            where_clauses.append("p.rating >= :min_rating")
            params["min_rating"] = float(rating)
        except Exception:
            pass

    where_sql = " AND ".join(where_clauses)

    order_map = {
        "popular": "p.orders_count DESC, p.views_count DESC",
        "new": "p.created_at DESC",
        "price_asc": "p.price ASC",
        "price_desc": "p.price DESC",
        "rating": "p.rating DESC",
    }
    order_sql = order_map.get(sort, "p.orders_count DESC")

    total_val = await database.fetch_val(
        sa.text(f"SELECT COUNT(*) FROM marketplace_products p WHERE {where_sql}"),
        params
    )
    total = int(total_val or 0)

    rows = await database.fetch_all(
        sa.text(f"""
            SELECT p.*, u.name AS seller_name, u.avatar AS seller_avatar
            FROM marketplace_products p
            LEFT JOIN users u ON u.id = p.seller_id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT :limit OFFSET :offset
        """),
        params
    )
    products = await _enrich_products(rows)

    # Featured
    featured_rows = await database.fetch_all(
        sa.text("""
            SELECT p.*, u.name AS seller_name
            FROM marketplace_products p
            LEFT JOIN users u ON u.id = p.seller_id
            WHERE p.is_active=true AND p.is_featured=true
            ORDER BY p.created_at DESC LIMIT 6
        """)
    )
    featured = await _enrich_products(featured_rows)

    categories = await _get_categories()
    cart_count = await _get_cart_count(user["id"]) if user else 0
    fav_count = await _get_fav_count(user["id"]) if user else 0

    fav_ids: set = set()
    if user:
        fav_rows = await database.fetch_all(
            sa.text("SELECT product_id FROM marketplace_favorites WHERE user_id=:uid"),
            {"uid": user["id"]}
        )
        fav_ids = {r["product_id"] for r in fav_rows}

    return templates.TemplateResponse("marketplace/index.html", {
        "request": request,
        "user": user,
        "products": products,
        "featured": featured,
        "categories": categories,
        "total": total,
        "page": page,
        "pages": (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE,
        "sort": sort,
        "q": q,
        "category": category,
        "cart_count": cart_count,
        "fav_count": fav_count,
        "fav_ids": list(fav_ids),
        "min_price": min_price,
        "max_price": max_price,
        "in_stock": in_stock,
        "rating_filter": rating,
    })


@router.get("/marketplace/product/{product_id}", response_class=HTMLResponse)
async def marketplace_product(request: Request, product_id: int):
    user = await get_user_from_request(request)

    row = await database.fetch_one(
        sa.text("""
            SELECT p.*, u.name AS seller_name, u.avatar AS seller_avatar, u.id AS seller_uid
            FROM marketplace_products p
            LEFT JOIN users u ON u.id = p.seller_id
            WHERE p.id=:pid AND p.is_active=true
        """),
        {"pid": product_id}
    )
    if not row:
        return RedirectResponse("/marketplace", status_code=302)

    product = dict(row)

    # Increment views
    await database.execute(
        sa.text("UPDATE marketplace_products SET views_count=views_count+1 WHERE id=:pid"),
        {"pid": product_id}
    )

    photos = await _get_product_photos(product_id)

    reviews_rows = await database.fetch_all(
        sa.text("""
            SELECT r.*, u.name AS reviewer_name, u.avatar AS reviewer_avatar
            FROM marketplace_reviews r
            LEFT JOIN users u ON u.id = r.user_id
            WHERE r.product_id=:pid
            ORDER BY r.created_at DESC
        """),
        {"pid": product_id}
    )
    reviews = [dict(r) for r in reviews_rows]

    questions_rows = await database.fetch_all(
        sa.text("""
            SELECT q.*, u.name AS asker_name, au.name AS answerer_name
            FROM marketplace_questions q
            LEFT JOIN users u ON u.id = q.user_id
            LEFT JOIN users au ON au.id = q.answered_by
            WHERE q.product_id=:pid
            ORDER BY q.created_at DESC
        """),
        {"pid": product_id}
    )
    questions = [dict(q) for q in questions_rows]

    # Related products
    related_rows = await database.fetch_all(
        sa.text("""
            SELECT p.*, u.name AS seller_name
            FROM marketplace_products p
            LEFT JOIN users u ON u.id = p.seller_id
            WHERE p.is_active=true AND p.category=:cat AND p.id != :pid
            ORDER BY p.rating DESC LIMIT 4
        """),
        {"cat": product.get("category") or "", "pid": product_id}
    )
    related = await _enrich_products(related_rows)

    # Rating distribution
    rating_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in reviews:
        rat = r.get("rating") or 0
        if 1 <= rat <= 5:
            rating_dist[rat] += 1

    cart_count = await _get_cart_count(user["id"]) if user else 0
    fav_count = await _get_fav_count(user["id"]) if user else 0

    is_favorited = False
    in_cart = False
    if user:
        fav = await database.fetch_one(
            sa.text("SELECT id FROM marketplace_favorites WHERE user_id=:uid AND product_id=:pid"),
            {"uid": user["id"], "pid": product_id}
        )
        is_favorited = bool(fav)
        cart_item = await database.fetch_one(
            sa.text("SELECT quantity FROM marketplace_cart WHERE user_id=:uid AND product_id=:pid"),
            {"uid": user["id"], "pid": product_id}
        )
        in_cart = bool(cart_item)

    attributes = product.get("attributes") or {}
    if isinstance(attributes, str):
        try:
            attributes = json.loads(attributes)
        except Exception:
            attributes = {}

    return templates.TemplateResponse("marketplace/product.html", {
        "request": request,
        "user": user,
        "product": product,
        "photos": photos,
        "reviews": reviews,
        "questions": questions,
        "related": related,
        "rating_dist": rating_dist,
        "is_favorited": is_favorited,
        "in_cart": in_cart,
        "cart_count": cart_count,
        "fav_count": fav_count,
        "attributes": attributes,
    })


@router.get("/marketplace/category/{slug}", response_class=HTMLResponse)
async def marketplace_category(request: Request, slug: str, page: int = 1, sort: str = "popular"):
    return RedirectResponse(f"/marketplace?category={slug}&page={page}&sort={sort}", status_code=302)


@router.get("/marketplace/search", response_class=HTMLResponse)
async def marketplace_search(request: Request, q: str = "", page: int = 1, sort: str = "popular"):
    return RedirectResponse(f"/marketplace?q={q}&page={page}&sort={sort}", status_code=302)


@router.get("/marketplace/seller/{seller_user_id}", response_class=HTMLResponse)
async def marketplace_seller_public(request: Request, seller_user_id: int, page: int = 1):
    user = await get_user_from_request(request)
    offset = (page - 1) * ITEMS_PER_PAGE

    seller_row = await database.fetch_one(
        sa.text("SELECT id, name, avatar, created_at FROM users WHERE id=:uid"),
        {"uid": seller_user_id}
    )
    if not seller_row:
        return RedirectResponse("/marketplace", status_code=302)
    seller = dict(seller_row)

    total_val = await database.fetch_val(
        sa.text("SELECT COUNT(*) FROM marketplace_products WHERE seller_id=:sid AND is_active=true"),
        {"sid": seller_user_id}
    )
    total = int(total_val or 0)

    rows = await database.fetch_all(
        sa.text("""
            SELECT p.* FROM marketplace_products p
            WHERE p.seller_id=:sid AND p.is_active=true
            ORDER BY p.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"sid": seller_user_id, "limit": ITEMS_PER_PAGE, "offset": offset}
    )
    products = await _enrich_products(rows)

    # Seller rating avg
    avg_val = await database.fetch_val(
        sa.text("""
            SELECT AVG(r.rating)
            FROM marketplace_reviews r
            JOIN marketplace_products p ON p.id = r.product_id
            WHERE p.seller_id=:sid
        """),
        {"sid": seller_user_id}
    )
    seller_rating = round(float(avg_val or 0), 1)

    cart_count = await _get_cart_count(user["id"]) if user else 0
    fav_count = await _get_fav_count(user["id"]) if user else 0

    fav_ids: set = set()
    if user:
        fav_rows = await database.fetch_all(
            sa.text("SELECT product_id FROM marketplace_favorites WHERE user_id=:uid"),
            {"uid": user["id"]}
        )
        fav_ids = {r["product_id"] for r in fav_rows}

    return templates.TemplateResponse("marketplace/seller_public.html", {
        "request": request,
        "user": user,
        "seller": seller,
        "seller_rating": seller_rating,
        "products": products,
        "total": total,
        "page": page,
        "pages": (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE,
        "cart_count": cart_count,
        "fav_count": fav_count,
        "fav_ids": list(fav_ids),
    })


@router.get("/marketplace/favorites", response_class=HTMLResponse)
async def marketplace_favorites_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/marketplace/favorites", status_code=302)
    cart_count = await _get_cart_count(user["id"])
    fav_count = await _get_fav_count(user["id"])
    return templates.TemplateResponse("marketplace/favorites.html", {
        "request": request,
        "user": user,
        "cart_count": cart_count,
        "fav_count": fav_count,
    })


@router.get("/marketplace/cart", response_class=HTMLResponse)
async def marketplace_cart_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/marketplace/cart", status_code=302)
    cart_count = await _get_cart_count(user["id"])
    fav_count = await _get_fav_count(user["id"])
    return templates.TemplateResponse("marketplace/cart.html", {
        "request": request,
        "user": user,
        "cart_count": cart_count,
        "fav_count": fav_count,
    })


# ── Seller cabinet ────────────────────────────────────────────────────────────

@router.get("/marketplace/seller-cabinet", response_class=HTMLResponse)
async def seller_cabinet(request: Request, tab: str = "analytics"):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/marketplace/seller-cabinet", status_code=302)
    cart_count = await _get_cart_count(user["id"])
    fav_count = await _get_fav_count(user["id"])
    categories = await _get_categories()
    return templates.TemplateResponse("marketplace/seller_cabinet.html", {
        "request": request,
        "user": user,
        "tab": tab,
        "cart_count": cart_count,
        "fav_count": fav_count,
        "categories": categories,
    })


@router.get("/marketplace/seller-cabinet/products", response_class=HTMLResponse)
async def seller_cabinet_products(request: Request):
    return RedirectResponse("/marketplace/seller-cabinet?tab=products", status_code=302)


# ── Admin panel ───────────────────────────────────────────────────────────────

@router.get("/marketplace/admin", response_class=HTMLResponse)
async def marketplace_admin(request: Request, page: int = 1):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/marketplace/admin", status_code=302)
    if (user.get("role") or "").lower() not in ("admin", "moderator"):
        return RedirectResponse("/marketplace", status_code=302)

    offset = (page - 1) * 50
    total_val = await database.fetch_val(sa.text("SELECT COUNT(*) FROM marketplace_products"))
    total = int(total_val or 0)

    rows = await database.fetch_all(
        sa.text("""
            SELECT p.*, u.name AS seller_name
            FROM marketplace_products p
            LEFT JOIN users u ON u.id = p.seller_id
            ORDER BY p.created_at DESC
            LIMIT 50 OFFSET :offset
        """),
        {"offset": offset}
    )
    products = [dict(r) for r in rows]
    return templates.TemplateResponse("marketplace/admin.html", {
        "request": request,
        "user": user,
        "products": products,
        "total": total,
        "page": page,
        "pages": (total + 49) // 50,
    })


# ── API: Cart ─────────────────────────────────────────────────────────────────

@router.get("/api/marketplace/cart/count")
async def api_cart_count(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"count": 0})
    count = await _get_cart_count(user["id"])
    return JSONResponse({"count": count})


@router.get("/api/marketplace/cart/items")
async def api_cart_items(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rows = await database.fetch_all(
        sa.text("""
            SELECT c.id AS cart_id, c.quantity, p.id, p.title, p.price, p.old_price,
                   p.in_stock, p.stock_count, u.name AS seller_name, u.id AS seller_id
            FROM marketplace_cart c
            JOIN marketplace_products p ON p.id = c.product_id
            LEFT JOIN users u ON u.id = p.seller_id
            WHERE c.user_id=:uid
            ORDER BY c.created_at DESC
        """),
        {"uid": user["id"]}
    )
    items = []
    for r in rows:
        item = dict(r)
        photos = await _get_product_photos(item["id"])
        item["photo"] = photos[0]["url"] if photos else None
        items.append(item)
    total = sum(float(i["price"] or 0) * int(i["quantity"] or 1) for i in items)
    return JSONResponse({"ok": True, "items": items, "total": round(total, 2)})


@router.post("/api/marketplace/cart/{product_id}")
async def api_cart_add(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    try:
        body = await request.json()
        quantity = int(body.get("quantity", 1))
    except Exception:
        quantity = 1

    existing = await database.fetch_one(
        sa.text("SELECT id, quantity FROM marketplace_cart WHERE user_id=:uid AND product_id=:pid"),
        {"uid": user["id"], "pid": product_id}
    )
    if existing:
        await database.execute(
            sa.text("UPDATE marketplace_cart SET quantity=:q WHERE user_id=:uid AND product_id=:pid"),
            {"q": quantity, "uid": user["id"], "pid": product_id}
        )
    else:
        await database.execute(
            sa.text("INSERT INTO marketplace_cart (user_id, product_id, quantity) VALUES (:uid, :pid, :q)"),
            {"uid": user["id"], "pid": product_id, "q": quantity}
        )
    count = await _get_cart_count(user["id"])
    return JSONResponse({"ok": True, "count": count})


@router.delete("/api/marketplace/cart/{product_id}")
async def api_cart_remove(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    await database.execute(
        sa.text("DELETE FROM marketplace_cart WHERE user_id=:uid AND product_id=:pid"),
        {"uid": user["id"], "pid": product_id}
    )
    count = await _get_cart_count(user["id"])
    return JSONResponse({"ok": True, "count": count})


# ── API: Favorites ────────────────────────────────────────────────────────────

@router.post("/api/marketplace/favorites/{product_id}")
async def api_toggle_favorite(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    existing = await database.fetch_one(
        sa.text("SELECT id FROM marketplace_favorites WHERE user_id=:uid AND product_id=:pid"),
        {"uid": user["id"], "pid": product_id}
    )
    if existing:
        await database.execute(
            sa.text("DELETE FROM marketplace_favorites WHERE user_id=:uid AND product_id=:pid"),
            {"uid": user["id"], "pid": product_id}
        )
        return JSONResponse({"ok": True, "added": False})
    else:
        await database.execute(
            sa.text("INSERT INTO marketplace_favorites (user_id, product_id) VALUES (:uid, :pid)"),
            {"uid": user["id"], "pid": product_id}
        )
        return JSONResponse({"ok": True, "added": True})


@router.get("/api/marketplace/favorites/items")
async def api_favorites_items(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rows = await database.fetch_all(
        sa.text("""
            SELECT p.*, u.name AS seller_name
            FROM marketplace_favorites f
            JOIN marketplace_products p ON p.id = f.product_id
            LEFT JOIN users u ON u.id = p.seller_id
            WHERE f.user_id=:uid AND p.is_active=true
            ORDER BY f.created_at DESC
        """),
        {"uid": user["id"]}
    )
    products = []
    for r in rows:
        p = dict(r)
        p["photos"] = await _get_product_photos(p["id"])
        products.append(p)
    return JSONResponse({"ok": True, "products": products})


# ── API: Reviews ──────────────────────────────────────────────────────────────

@router.post("/api/marketplace/reviews/{product_id}")
async def api_create_review(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    rating = int(body.get("rating", 5))
    if not (1 <= rating <= 5):
        return JSONResponse({"error": "invalid rating"}, status_code=400)

    # Check existing
    existing = await database.fetch_one(
        sa.text("SELECT id FROM marketplace_reviews WHERE user_id=:uid AND product_id=:pid"),
        {"uid": user["id"], "pid": product_id}
    )
    if existing:
        return JSONResponse({"error": "already_reviewed"}, status_code=400)

    await database.execute(
        sa.text("""
            INSERT INTO marketplace_reviews (product_id, user_id, rating, text, pros, cons)
            VALUES (:pid, :uid, :rating, :text, :pros, :cons)
        """),
        {
            "pid": product_id, "uid": user["id"], "rating": rating,
            "text": body.get("text", ""), "pros": body.get("pros", ""), "cons": body.get("cons", ""),
        }
    )

    # Recalculate rating
    avg_row = await database.fetch_one(
        sa.text("SELECT AVG(rating) AS avg, COUNT(*) AS cnt FROM marketplace_reviews WHERE product_id=:pid"),
        {"pid": product_id}
    )
    if avg_row:
        await database.execute(
            sa.text("UPDATE marketplace_products SET rating=:r, reviews_count=:c WHERE id=:pid"),
            {"r": round(float(avg_row["avg"] or 0), 2), "c": int(avg_row["cnt"] or 0), "pid": product_id}
        )

    return JSONResponse({"ok": True})


# ── API: Questions ────────────────────────────────────────────────────────────

@router.post("/api/marketplace/questions/{product_id}")
async def api_create_question(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "empty question"}, status_code=400)

    await database.execute(
        sa.text("INSERT INTO marketplace_questions (product_id, user_id, question) VALUES (:pid, :uid, :q)"),
        {"pid": product_id, "uid": user["id"], "q": question}
    )
    return JSONResponse({"ok": True})


# ── API: Seller products (list) ───────────────────────────────────────────────

@router.get("/api/marketplace/seller/products")
async def api_seller_products(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    rows = await database.fetch_all(
        sa.text("""
            SELECT p.*
            FROM marketplace_products p
            WHERE p.seller_id=:uid
            ORDER BY p.created_at DESC
        """),
        {"uid": user["id"]}
    )
    products = []
    for r in rows:
        p = dict(r)
        photos = await _get_product_photos(p["id"])
        p["photos"] = photos
        p["price"] = str(p["price"]) if p["price"] is not None else None
        p["old_price"] = str(p["old_price"]) if p["old_price"] is not None else None
        p["rating"] = str(p["rating"]) if p["rating"] is not None else "0"
        products.append(p)
    return JSONResponse({"ok": True, "products": products})


# ── API: Seller create product ────────────────────────────────────────────────

@router.post("/api/marketplace/seller/products")
async def api_seller_create_product(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    brand: str = Form(""),
    price: str = Form("0"),
    old_price: str = Form(""),
    stock_count: str = Form("0"),
    photos: list[UploadFile] = File(default=[]),
    attributes: str = Form("{}"),
):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    try:
        price_val = float(price) if price else 0.0
    except Exception:
        price_val = 0.0
    try:
        old_price_val = float(old_price) if old_price else None
    except Exception:
        old_price_val = None
    try:
        stock_val = int(stock_count) if stock_count else 0
    except Exception:
        stock_val = 0

    try:
        attrs = json.loads(attributes) if attributes else {}
    except Exception:
        attrs = {}

    product_id = await database.fetch_val(
        sa.text("""
            INSERT INTO marketplace_products
            (seller_id, title, description, category, brand, price, old_price, stock_count, in_stock, attributes)
            VALUES (:sid, :title, :desc, :cat, :brand, :price, :old_price, :stock, :in_stock, :attrs::jsonb)
            RETURNING id
        """),
        {
            "sid": user["id"], "title": title, "desc": description,
            "cat": category, "brand": brand, "price": price_val,
            "old_price": old_price_val, "stock": stock_val,
            "in_stock": stock_val > 0,
            "attrs": json.dumps(attrs),
        }
    )

    # Upload photos
    if photos:
        for i, photo_file in enumerate(photos):
            if not photo_file or not photo_file.filename:
                continue
            try:
                file_bytes = await photo_file.read()
                if file_bytes:
                    url = await _upload_photo(file_bytes, photo_file.filename)
                    await database.execute(
                        sa.text("INSERT INTO marketplace_photos (product_id, url, position) VALUES (:pid, :url, :pos)"),
                        {"pid": product_id, "url": url, "pos": i}
                    )
            except Exception as e:
                _logger.warning("Photo upload error: %s", e)

    return JSONResponse({"ok": True, "id": product_id})


# ── API: Seller update product ────────────────────────────────────────────────

@router.put("/api/marketplace/seller/products/{product_id}")
async def api_seller_update_product(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    # Verify ownership
    row = await database.fetch_one(
        sa.text("SELECT id FROM marketplace_products WHERE id=:pid AND seller_id=:uid"),
        {"pid": product_id, "uid": user["id"]}
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    allowed = {"title", "description", "category", "brand", "price", "old_price",
               "stock_count", "in_stock", "is_active", "attributes"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        return JSONResponse({"error": "no valid fields"}, status_code=400)

    if "attributes" in updates:
        updates["attributes"] = json.dumps(updates["attributes"]) + "::jsonb"
        set_parts = []
        for k in updates:
            if k == "attributes":
                set_parts.append(f"attributes=:attributes::jsonb")
            else:
                set_parts.append(f"{k}=:{k}")
    else:
        set_parts = [f"{k}=:{k}" for k in updates]

    set_clause = ", ".join(set_parts)
    updates["pid"] = product_id

    if "attributes" in updates and isinstance(updates["attributes"], str) and "::jsonb" in updates["attributes"]:
        updates["attributes"] = updates["attributes"].replace("::jsonb", "")

    await database.execute(
        sa.text(f"UPDATE marketplace_products SET {set_clause} WHERE id=:pid"),
        updates
    )
    return JSONResponse({"ok": True})


# ── API: Seller delete product ────────────────────────────────────────────────

@router.delete("/api/marketplace/seller/products/{product_id}")
async def api_seller_delete_product(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    row = await database.fetch_one(
        sa.text("SELECT id FROM marketplace_products WHERE id=:pid AND seller_id=:uid"),
        {"pid": product_id, "uid": user["id"]}
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    await database.execute(
        sa.text("UPDATE marketplace_products SET is_active=false WHERE id=:pid"),
        {"pid": product_id}
    )
    return JSONResponse({"ok": True})


# ── API: Seller upload photos ─────────────────────────────────────────────────

@router.post("/api/marketplace/seller/products/{product_id}/photos")
async def api_seller_upload_photos(
    request: Request,
    product_id: int,
    photos: list[UploadFile] = File(...),
):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    row = await database.fetch_one(
        sa.text("SELECT id FROM marketplace_products WHERE id=:pid AND seller_id=:uid"),
        {"pid": product_id, "uid": user["id"]}
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Get current max position
    max_pos = await database.fetch_val(
        sa.text("SELECT COALESCE(MAX(position), -1) FROM marketplace_photos WHERE product_id=:pid"),
        {"pid": product_id}
    )
    max_pos = int(max_pos or -1)

    uploaded = []
    for i, photo_file in enumerate(photos):
        if not photo_file or not photo_file.filename:
            continue
        try:
            file_bytes = await photo_file.read()
            if file_bytes:
                url = await _upload_photo(file_bytes, photo_file.filename)
                photo_id = await database.fetch_val(
                    sa.text("INSERT INTO marketplace_photos (product_id, url, position) VALUES (:pid, :url, :pos) RETURNING id"),
                    {"pid": product_id, "url": url, "pos": max_pos + i + 1}
                )
                uploaded.append({"id": photo_id, "url": url})
        except Exception as e:
            _logger.warning("Photo upload error: %s", e)

    return JSONResponse({"ok": True, "photos": uploaded})


# ── API: Seller delete photo ──────────────────────────────────────────────────

@router.delete("/api/marketplace/seller/photos/{photo_id}")
async def api_seller_delete_photo(request: Request, photo_id: int):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    row = await database.fetch_one(
        sa.text("""
            SELECT ph.id FROM marketplace_photos ph
            JOIN marketplace_products p ON p.id = ph.product_id
            WHERE ph.id=:phid AND p.seller_id=:uid
        """),
        {"phid": photo_id, "uid": user["id"]}
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    await database.execute(
        sa.text("DELETE FROM marketplace_photos WHERE id=:phid"),
        {"phid": photo_id}
    )
    return JSONResponse({"ok": True})


# ── API: Seller orders ────────────────────────────────────────────────────────

@router.get("/api/marketplace/seller/orders")
async def api_seller_orders(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    rows = await database.fetch_all(
        sa.text("""
            SELECT oi.*, o.status, o.created_at AS order_date,
                   p.title AS product_title, p.price AS product_price,
                   u.name AS buyer_name
            FROM marketplace_order_items oi
            JOIN marketplace_orders o ON o.id = oi.order_id
            JOIN marketplace_products p ON p.id = oi.product_id
            JOIN users u ON u.id = o.user_id
            WHERE p.seller_id=:uid
            ORDER BY o.created_at DESC
            LIMIT 100
        """),
        {"uid": user["id"]}
    )
    items = []
    for r in rows:
        item = dict(r)
        if item.get("order_date"):
            item["order_date"] = item["order_date"].isoformat()
        items.append(item)
    return JSONResponse({"ok": True, "orders": items})


# ── API: Seller reviews ───────────────────────────────────────────────────────

@router.get("/api/marketplace/seller/reviews")
async def api_seller_reviews(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    rows = await database.fetch_all(
        sa.text("""
            SELECT r.*, p.title AS product_title, u.name AS reviewer_name
            FROM marketplace_reviews r
            JOIN marketplace_products p ON p.id = r.product_id
            LEFT JOIN users u ON u.id = r.user_id
            WHERE p.seller_id=:uid
            ORDER BY r.created_at DESC
            LIMIT 100
        """),
        {"uid": user["id"]}
    )
    items = []
    for r in rows:
        item = dict(r)
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()
        items.append(item)
    return JSONResponse({"ok": True, "reviews": items})


# ── API: Seller questions ─────────────────────────────────────────────────────

@router.get("/api/marketplace/seller/questions")
async def api_seller_questions(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    rows = await database.fetch_all(
        sa.text("""
            SELECT q.*, p.title AS product_title, u.name AS asker_name
            FROM marketplace_questions q
            JOIN marketplace_products p ON p.id = q.product_id
            LEFT JOIN users u ON u.id = q.user_id
            WHERE p.seller_id=:uid
            ORDER BY q.created_at DESC
            LIMIT 100
        """),
        {"uid": user["id"]}
    )
    items = []
    for r in rows:
        item = dict(r)
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()
        items.append(item)
    return JSONResponse({"ok": True, "questions": items})


# ── API: Seller answer question ───────────────────────────────────────────────

@router.patch("/api/marketplace/seller/questions/{question_id}/answer")
async def api_seller_answer_question(request: Request, question_id: int):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    row = await database.fetch_one(
        sa.text("""
            SELECT q.id FROM marketplace_questions q
            JOIN marketplace_products p ON p.id = q.product_id
            WHERE q.id=:qid AND p.seller_id=:uid
        """),
        {"qid": question_id, "uid": user["id"]}
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    answer = (body.get("answer") or "").strip()
    if not answer:
        return JSONResponse({"error": "empty answer"}, status_code=400)

    await database.execute(
        sa.text("UPDATE marketplace_questions SET answer=:ans, answered_by=:uid WHERE id=:qid"),
        {"ans": answer, "uid": user["id"], "qid": question_id}
    )
    return JSONResponse({"ok": True})


# ── API: Seller analytics ─────────────────────────────────────────────────────

@router.get("/api/marketplace/seller/analytics")
async def api_seller_analytics(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "auth required"}, status_code=401)

    products_count = await database.fetch_val(
        sa.text("SELECT COUNT(*) FROM marketplace_products WHERE seller_id=:uid AND is_active=true"),
        {"uid": user["id"]}
    )
    views_total = await database.fetch_val(
        sa.text("SELECT COALESCE(SUM(views_count), 0) FROM marketplace_products WHERE seller_id=:uid"),
        {"uid": user["id"]}
    )
    orders_total = await database.fetch_val(
        sa.text("SELECT COALESCE(SUM(orders_count), 0) FROM marketplace_products WHERE seller_id=:uid"),
        {"uid": user["id"]}
    )
    favorites_total = await database.fetch_val(
        sa.text("""
            SELECT COUNT(*) FROM marketplace_favorites f
            JOIN marketplace_products p ON p.id = f.product_id
            WHERE p.seller_id=:uid
        """),
        {"uid": user["id"]}
    )
    reviews_count = await database.fetch_val(
        sa.text("""
            SELECT COUNT(*) FROM marketplace_reviews r
            JOIN marketplace_products p ON p.id = r.product_id
            WHERE p.seller_id=:uid
        """),
        {"uid": user["id"]}
    )
    avg_rating = await database.fetch_val(
        sa.text("""
            SELECT COALESCE(AVG(r.rating), 0) FROM marketplace_reviews r
            JOIN marketplace_products p ON p.id = r.product_id
            WHERE p.seller_id=:uid
        """),
        {"uid": user["id"]}
    )

    return JSONResponse({
        "ok": True,
        "products": int(products_count or 0),
        "views": int(views_total or 0),
        "orders": int(orders_total or 0),
        "favorites": int(favorites_total or 0),
        "reviews": int(reviews_count or 0),
        "avg_rating": round(float(avg_rating or 0), 2),
    })


# ── API: Admin endpoints ──────────────────────────────────────────────────────

@router.patch("/api/marketplace/admin/products/{product_id}/featured")
async def api_admin_toggle_featured(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user or (user.get("role") or "").lower() not in ("admin", "moderator"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    row = await database.fetch_one(
        sa.text("SELECT is_featured FROM marketplace_products WHERE id=:pid"),
        {"pid": product_id}
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    new_val = not bool(row["is_featured"])
    await database.execute(
        sa.text("UPDATE marketplace_products SET is_featured=:v WHERE id=:pid"),
        {"v": new_val, "pid": product_id}
    )
    return JSONResponse({"ok": True, "is_featured": new_val})


@router.patch("/api/marketplace/admin/products/{product_id}/active")
async def api_admin_toggle_active(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user or (user.get("role") or "").lower() not in ("admin", "moderator"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    row = await database.fetch_one(
        sa.text("SELECT is_active FROM marketplace_products WHERE id=:pid"),
        {"pid": product_id}
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    new_val = not bool(row["is_active"])
    await database.execute(
        sa.text("UPDATE marketplace_products SET is_active=:v WHERE id=:pid"),
        {"v": new_val, "pid": product_id}
    )
    return JSONResponse({"ok": True, "is_active": new_val})


@router.delete("/api/marketplace/admin/products/{product_id}")
async def api_admin_delete_product(request: Request, product_id: int):
    user = await get_user_from_request(request)
    if not user or (user.get("role") or "").lower() not in ("admin", "moderator"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        sa.text("DELETE FROM marketplace_products WHERE id=:pid"),
        {"pid": product_id}
    )
    return JSONResponse({"ok": True})
