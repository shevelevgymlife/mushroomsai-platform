import os
import uuid
from typing import Optional

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from auth.session import get_user_from_request
from db.database import database
import sqlalchemy as sa

from db.models import shop_products, users, product_questions
from services.subscription_service import check_subscription
from web.templates_utils import Jinja2Templates

router = APIRouter(prefix="/seller", tags=["seller"])
templates = Jinja2Templates(directory="web/templates")

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024


def _parse_price(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


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


async def require_maxi_seller(request: Request):
    u = await get_user_from_request(request)
    if not u:
        return None
    uid = u.get("primary_user_id") or u["id"]
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row or not row.get("marketplace_seller"):
        return None
    plan = await check_subscription(uid)
    if plan != "maxi":
        return None
    return dict(row)


@router.get("", response_class=HTMLResponse)
async def seller_root():
    return RedirectResponse("/seller/shop", status_code=302)


@router.get("/shop", response_class=HTMLResponse)
async def seller_shop_page(request: Request):
    seller = await require_maxi_seller(request)
    if not seller:
        return RedirectResponse("/dashboard", status_code=302)
    uid = seller["id"]
    rows = await database.fetch_all(
        shop_products.select()
        .where(shop_products.c.seller_id == uid)
        .order_by(shop_products.c.id.desc())
    )
    products = [dict(r) for r in rows]
    pending_q = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(
            product_questions.join(
                shop_products, product_questions.c.product_id == shop_products.c.id
            )
        )
        .where(shop_products.c.seller_id == uid)
        .where(product_questions.c.answer_text.is_(None))
    )
    return templates.TemplateResponse(
        "dashboard/seller_shop.html",
        {
            "request": request,
            "user": seller,
            "products": products,
            "pending_questions_count": int(pending_q or 0),
        },
    )


@router.get("/shop/product/{product_id}")
async def seller_shop_product_json(request: Request, product_id: int):
    """JSON для модалки редактирования (без data-product в HTML — кавычки ломали атрибут)."""
    seller = await require_maxi_seller(request)
    if not seller:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    uid = seller["id"]
    row = await database.fetch_one(
        shop_products.select().where(shop_products.c.id == product_id)
    )
    if not row or row.get("seller_id") != uid:
        return JSONResponse({"error": "not found"}, status_code=404)
    p = dict(row)
    # Возвращаем только поля формы редактирования, чтобы исключить несериализуемые типы
    # (например datetime в created_at), которые ломали JSON и открытие модалки.
    payload = {
        "id": int(p.get("id") or 0),
        "name": p.get("name") or "",
        "description": p.get("description") or "",
        "price": int(p.get("price") or 0),
        "url": p.get("url") or "",
        "mushroom_type": p.get("mushroom_type") or "",
        "image_url": p.get("image_url") or "",
        "category": p.get("category") or "",
        "in_stock": p.get("in_stock") is not False,
    }
    return JSONResponse(payload)


@router.get("/questions", response_class=HTMLResponse)
async def seller_questions_page(request: Request):
    seller = await require_maxi_seller(request)
    if not seller:
        return RedirectResponse("/dashboard", status_code=302)
    uid = seller["id"]
    rows = await database.fetch_all(
        sa.select(product_questions, shop_products.c.name.label("product_name"))
        .select_from(
            product_questions.join(
                shop_products, product_questions.c.product_id == shop_products.c.id
            )
        )
        .where(shop_products.c.seller_id == uid)
        .where(product_questions.c.answer_text.is_(None))
        .order_by(product_questions.c.created_at.asc())
    )
    return templates.TemplateResponse(
        "dashboard/seller_questions.html",
        {"request": request, "user": seller, "questions": rows},
    )


@router.post("/shop/add")
async def seller_add_product(
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
    seller = await require_maxi_seller(request)
    if not seller:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    uid = seller["id"]
    price_val = _parse_price(price)
    await database.execute(
        shop_products.insert().values(
            seller_id=uid,
            name=name,
            description=description,
            price=price_val,
            url=url or None,
            mushroom_type=mushroom_type or None,
            image_url=image_url or None,
            category=category or None,
            in_stock=(in_stock == "true"),
        )
    )
    return RedirectResponse("/seller/shop", status_code=302)


@router.post("/shop/edit/{product_id}")
async def seller_edit_product(
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
    seller = await require_maxi_seller(request)
    if not seller:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    uid = seller["id"]
    row = await database.fetch_one(
        shop_products.select().where(shop_products.c.id == product_id)
    )
    if not row or row.get("seller_id") != uid:
        return JSONResponse({"error": "not found"}, status_code=404)
    price_val = _parse_price(price)
    await database.execute(
        shop_products.update().where(shop_products.c.id == product_id).values(
            name=name,
            description=description,
            price=price_val,
            url=url or None,
            mushroom_type=mushroom_type or None,
            image_url=image_url or None,
            category=category or None,
            in_stock=(in_stock == "true"),
        )
    )
    return JSONResponse({"ok": True})


@router.post("/shop/delete/{product_id}")
async def seller_delete_product(request: Request, product_id: int):
    seller = await require_maxi_seller(request)
    if not seller:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    uid = seller["id"]
    row = await database.fetch_one(
        shop_products.select().where(shop_products.c.id == product_id)
    )
    if not row or row.get("seller_id") != uid:
        return JSONResponse({"error": "not found"}, status_code=404)
    await database.execute(shop_products.delete().where(shop_products.c.id == product_id))
    return JSONResponse({"ok": True})


@router.post("/shop/upload-image")
async def seller_upload_image(request: Request, file: UploadFile = File(...)):
    seller = await require_maxi_seller(request)
    if not seller:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    ct = _normalize_image_content_type(file)
    if not ct:
        return JSONResponse({"error": "Допустимые форматы: JPEG, PNG, WebP, GIF"}, status_code=400)

    data = await file.read()
    if len(data) > MAX_IMAGE_SIZE:
        return JSONResponse({"error": "Файл слишком большой (макс. 5 МБ)"}, status_code=400)

    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(ct, "jpg")
    filename = f"{uuid.uuid4().hex}.{ext}"

    base = "/data" if os.path.exists("/data") else "./media"
    save_dir = os.path.join(base, "products")
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, filename)

    with open(save_path, "wb") as f:
        f.write(data)

    return JSONResponse({"ok": True, "url": f"/media/products/{filename}"})
