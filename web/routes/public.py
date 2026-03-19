from fastapi import APIRouter, Request, Depends
from web.templates_utils import Jinja2Templates
from fastapi.responses import HTMLResponse
from db.database import database
from db.models import products, posts, users
from auth.session import get_user_from_request

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


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
async def shop(request: Request, category: str = None):
    current_user = await get_user_from_request(request)
    query = products.select().where(products.c.active == True)
    if category:
        query = query.where(products.c.category == category)
    prods = await database.fetch_all(query)
    categories = await database.fetch_all(
        __import__("sqlalchemy", fromlist=["select"]).select(products.c.category).distinct()
    )
    return templates.TemplateResponse(
        "shop.html",
        {"request": request, "user": current_user, "products": prods, "categories": categories, "selected_category": category},
    )


@router.get("/shop/{product_id}", response_class=HTMLResponse)
async def product_page(request: Request, product_id: int):
    current_user = await get_user_from_request(request)
    product = await database.fetch_one(products.select().where(products.c.id == product_id))
    if not product:
        return HTMLResponse("Товар не найден", status_code=404)
    return templates.TemplateResponse(
        "product.html",
        {"request": request, "user": current_user, "product": product},
    )


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
