import os
import uuid
import sqlalchemy as sa
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from web.templates_utils import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from db.database import database
from db.models import products, posts, users, shop_products, product_reviews, community_posts, community_likes, community_folders, community_follows, community_saved, community_comments, profile_likes, direct_messages
from auth.session import get_user_from_request
from config import settings


def get_public_user_data(row: dict) -> dict:
    """Return only privacy-safe fields from a user row."""
    return {
        "id": row["id"],
        "name": row["name"],
        "avatar": row["avatar"],
        "bio": row.get("bio"),
        "role": row["role"],
        "wallet_address": row.get("wallet_address"),
        "followers_count": row.get("followers_count") or 0,
        "following_count": row.get("following_count") or 0,
        "created_at": row.get("created_at"),
    }

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
    # Users count (primary accounts only)
    users_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(users).where(users.c.primary_user_id == None)
    ) or 0

    # Featured marketplace products with avg ratings
    featured_raw = await database.fetch_all(
        shop_products.select().where(shop_products.c.active == True)
        .order_by(shop_products.c.created_at.desc()).limit(4)
    )
    featured_products = []
    for p in featured_raw:
        avg = await database.fetch_val(
            sa.select(sa.func.avg(product_reviews.c.rating))
            .where(product_reviews.c.product_id == p["id"])
        )
        featured_products.append({
            "id": p["id"],
            "name": p["name"],
            "description": p.get("description") or "",
            "price": p.get("price"),
            "image_url": p.get("image_url"),
            "avg_rating": round(float(avg), 1) if avg else None,
        })

    await database.execute(
        __import__("db.models", fromlist=["page_views"]).page_views.insert().values(
            path="/", user_id=current_user["id"] if current_user else None
        )
    )
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": current_user,
            "products": prods,
            "posts": community_posts,
            "users_count": users_count,
            "featured_products": featured_products,
        },
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

    # Post count & reputation
    post_count = await database.fetch_val(
        sa.select(sa.func.count()).select_from(community_posts)
        .where(community_posts.c.user_id == profile_id)
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
        .where(community_posts.c.user_id == profile_id)
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

    return templates.TemplateResponse(
        "community_profile.html",
        {
            "request": request,
            "current_user": current_user,
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
            "shevelev_token": settings.SHEVELEV_TOKEN_ADDRESS,
        },
    )


# ─── Direct Messages ──────────────────────────────────────────────────────────

async def _get_conversations(user_id: int) -> list:
    """Return list of unique DM partners with last message & unread count."""
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

    # Include system messages (sender_id IS NULL) as a separate "Система" entry
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
        })

    for r in rows:
        other_id = r["other_id"]
        if not other_id:
            continue
        other = await database.fetch_one(users.select().where(users.c.id == other_id))
        last_msg = await database.fetch_one(sa.text(
            "SELECT text FROM direct_messages WHERE id=:lid"
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
        })
    return convs


@router.get("/messages/unread-count")
async def messages_unread_count(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"count": 0})
    uid = current_user.get("primary_user_id") or current_user["id"]
    count = await database.fetch_val(sa.text(
        "SELECT COUNT(*) FROM direct_messages WHERE recipient_id=:uid AND is_read=false"
    ), {"uid": uid}) or 0
    return JSONResponse({"count": count})


@router.get("/messages/conversations")
async def messages_conversations_api(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = current_user.get("primary_user_id") or current_user["id"]
    convs = await _get_conversations(uid)
    return JSONResponse({"conversations": convs})


@router.get("/messages/dialogs")
async def messages_dialogs_api(request: Request):
    """JSON endpoint for community panel — list of dialogs."""
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth required"}, status_code=401)
    uid = current_user.get("primary_user_id") or current_user["id"]
    convs = await _get_conversations(uid)
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
    uid = current_user.get("primary_user_id") or current_user["id"]
    convs = await _get_conversations(uid)
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
    uid = current_user.get("primary_user_id") or current_user["id"]

    # Mark messages from other_id as read
    if other_id == 0:
        # System messages
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


@router.get("/messages/poll/{other_id}")
async def poll_messages(request: Request, other_id: int, after: int = 0):
    current_user = await get_user_from_request(request)
    if not current_user:
        return JSONResponse({"error": "auth"}, status_code=401)
    uid = current_user.get("primary_user_id") or current_user["id"]
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

    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        body = await request.json()
        text = (body.get("text") or "").strip()
    else:
        form = await request.form()
        text = (form.get("text") or "").strip()

    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)

    msg_id = await database.execute(sa.text(
        "INSERT INTO direct_messages (sender_id, recipient_id, text, is_read, is_system) VALUES (:s, :r, :t, false, false) RETURNING id"
    ), {"s": uid, "r": other_id, "t": text})

    # Telegram notify
    recipient = await database.fetch_one(users.select().where(users.c.id == other_id))
    if recipient:
        tg_id = recipient.get("tg_id") or recipient.get("linked_tg_id")
        if tg_id:
            try:
                import httpx
                sender_name = current_user.get("name") or "Пользователь"
                notify_text = f"💬 Новое сообщение от {sender_name}\n{text[:100]}"
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage",
                        json={"chat_id": tg_id, "text": notify_text},
                    )
            except Exception:
                pass

    return JSONResponse({
        "ok": True,
        "id": msg_id,
        "text": text,
        "sender_id": uid,
        "created_at": "сейчас",
    })


# ─── Community Members Page ────────────────────────────────────────────────────

@router.get("/community/members", response_class=HTMLResponse)
async def community_members(request: Request):
    current_user = await get_user_from_request(request)
    if not current_user:
        return RedirectResponse("/login?next=/community/members")

    viewer_id = current_user.get("primary_user_id") or current_user["id"]

    search = request.query_params.get("q", "").strip()
    level_filter = request.query_params.get("level", "")
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
        },
    )
