import os
import uuid
import sqlalchemy as sa
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from web.templates_utils import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from db.database import database
from db.models import products, posts, users, shop_products, product_reviews, community_posts, community_likes, community_folders, community_follows, community_saved
from auth.session import get_user_from_request
from config import settings

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

    if not current_user:
        # Preview for unauthenticated users
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
            "shevelev_token": settings.SHEVELEV_TOKEN_ADDRESS,
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
