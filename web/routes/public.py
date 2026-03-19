import os
import uuid
import sqlalchemy as sa
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from web.templates_utils import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from db.database import database
from db.models import products, posts, users, shop_products, product_reviews
from auth.session import get_user_from_request

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

MUSHROOM_TYPES = ["Рейши", "Шиитаке", "Кордицепс", "Ежовик", "Красный мухомор", "Пантерный мухомор", "Королевский мухомор"]
CATEGORIES = ["Экстракт", "Плодовое тело", "Капсулы", "Порошок"]


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    current_user = await get_user_from_request(request)
    prods = await database.fetch_all(
        products.select().where(products.c.active == True).limit(6)
    )
    community_posts = await database.fetch_all(
        posts.select()
        .where(posts.c.approved == True)
        .order_by(posts.c.created_at.desc())
        .limit(4)
    )
    await database.execute(
        __import__("db.models", fromlist=["page_views"]).page_views.insert().values(
            path="/", user_id=current_user["id"] if current_user else None
        )
    )
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "user": current_user, "products": prods, "posts": community_posts},
    )


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

    # Fetch avg ratings for all products
    ratings = {}
    for p in prods:
        avg = await database.fetch_val(
            sa.select(sa.func.avg(product_reviews.c.rating))
            .where(product_reviews.c.product_id == p["id"])
        )
        ratings[p["id"]] = round(float(avg), 1) if avg else None

    return templates.TemplateResponse(
        "shop.html",
        {
            "request": request,
            "user": current_user,
            "products": prods,
            "ratings": ratings,
            "mushroom_types": MUSHROOM_TYPES,
            "categories": CATEGORIES,
            "sel_mushroom": mushroom_type,
            "sel_category": category,
            "sort": sort,
            "search": search,
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

    return templates.TemplateResponse(
        "shop_product.html",
        {
            "request": request,
            "user": current_user,
            "product": product,
            "reviews": reviews,
            "avg_rating": avg_rating,
            "similar": similar,
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

    await database.execute(
        product_reviews.insert().values(
            product_id=product_id,
            user_id=current_user["id"],
            rating=rating,
            text=text.strip() or None,
        )
    )
    return RedirectResponse(f"/shop/{product_id}#reviews", status_code=302)


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    current_user = await get_user_from_request(request)
    return templates.TemplateResponse(
        "chat.html",
        {"request": request, "user": current_user},
    )


@router.get("/community", response_class=HTMLResponse)
async def community(request: Request):
    current_user = await get_user_from_request(request)
    all_posts = await database.fetch_all(
        posts.select()
        .where(posts.c.approved == True)
        .order_by(posts.c.created_at.desc())
        .limit(20)
    )
    return templates.TemplateResponse(
        "community.html",
        {"request": request, "user": current_user, "posts": all_posts},
    )


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
