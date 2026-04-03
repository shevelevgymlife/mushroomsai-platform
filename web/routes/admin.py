import logging
import math
import os
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Request, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from web.templates_utils import Jinja2Templates
from config import settings
from services.ai_community_bot import (
    apply_ai_community_bot_schema_if_needed,
    bind_ai_community_bot_to_user_id,
    load_bot_settings_row,
)
from services.payment_plans_catalog import get_effective_plans
from services.subscription_service import record_subscription_event, format_admin_subscription_assigned_message
from services.system_support_delivery import deliver_system_support_notification
from services.shop_catalog import extra_image_lines_from_json, extra_image_urls_from_text
from auth.session import get_user_from_request
from auth.blocked_identities import block_identities_for_user, unblock_identities_for_user
from services.user_permanent_delete import permanently_delete_user
from auth.owner import is_platform_owner
from db.database import database
from db.models import (
    users, messages, leads, products, orders, posts,
    page_views, ai_settings, subscriptions, knowledge_base,
    shop_products, feedback, admin_permissions, product_reviews,
    community_posts, community_comments, community_likes, community_saved, community_folders,
    homepage_blocks, dashboard_blocks, user_block_overrides,
    ai_training_posts, ai_training_folders,
    radio_downtempo_tracks,
    platform_settings,
    wellness_journal_entries,
    wellness_scheme_effect_stats,
    platform_ai_feedback,
    ai_community_bot_settings,
)
import sqlalchemy
from datetime import datetime, timedelta, date

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="web/templates")

ADMIN_SECTIONS = [
    ("Панель", "/admin", "can_dashboard"),
    ("AI", "/admin/ai", "can_ai"),
    ("Обучающие посты", "/admin/ai-posts", "can_ai_posts"),
    ("Магазин", "/admin/shop", "can_shop"),
    ("Оплата", "/admin/payment", "can_payment"),
    ("Биржа / ликвидность", "/admin/liquidity", "can_payment"),
    ("Пользователи", "/admin/users", "can_users"),
    ("Обратная связь", "/admin/feedback", "can_feedback"),
    ("Рассылки", "/admin/broadcast", "can_broadcast"),
    ("База знаний", "/admin/knowledge", "can_knowledge"),
    ("Сообщество", "/admin/community", "can_community"),
    ("Группы / Чаты", "/admin/groups-chats", "can_groups"),
    ("Настройки контента", "/admin/content-settings", "can_groups"),
    ("Видеосвязь", "/admin/video-calls", "can_groups"),
    ("Главная сайта", "/admin/homepage", "can_homepage"),
    ("Блоки кабинета", "/admin/dashboard-blocks", "can_dashboard_blocks"),
    ("Радио Down Tempo", "/admin/radio-downtempo", "can_radio_downtempo"),
    ("Реферальная программа", "/admin/referral", "can_users"),
    ("Дневник терапии", "/admin/wellness-journal", "can_users"),
    ("Статистика дневника", "/admin/wellness-journal/insights", "can_users"),
    ("NeuroFungi AI: пожелания", "/admin/platform-ai-feedback", "can_users"),
    ("AI в сообществе", "/admin/ai-community-bot", "can_users"),
]
ADMIN_NAV = [(label, href) for (label, href, _perm) in ADMIN_SECTIONS]
AI_RETRIEVAL_MODES = [
    ("title_first", "Название сначала (рекомендуется)", "Сначала ищет по названию/папке, затем по тексту."),
    ("strict_titles", "Только точные названия", "Почти только заголовки и папки; очень строгий режим."),
    ("balanced", "Сбалансированный", "Равномерно учитывает заголовок и содержание."),
    ("content_deep", "Глубоко по содержанию", "Сильнее ищет по тексту постов, не только по заголовкам."),
    ("hybrid_fts", "Гибрид + FTS", "Like-поиск + полнотекстовый ранжир PostgreSQL."),
    ("broad_recall", "Широкий охват", "Берет больше кандидатов, полезно для расплывчатых вопросов."),
    ("precise_shortlist", "Короткий точный список", "Меньше, но максимально релевантные посты."),
    ("recent_only", "Только свежие", "Игнорирует релевантность, берет последние посты."),
]

PERM_KEYS = list(dict.fromkeys(
    [perm for (_label, _href, perm) in ADMIN_SECTIONS] + ["can_training_bot", "can_ai_unlimited"]
))
PERM_LABELS = {
    "can_dashboard": "Dashboard",
    "can_ai": "AI управление",
    "can_ai_posts": "Обучающие посты",
    "can_shop": "Магазин",
    "can_payment": "Оплата и тарифы",
    "can_users": "Пользователи",
    "can_feedback": "Обратная связь",
    "can_broadcast": "Рассылки",
    "can_knowledge": "База знаний",
    "can_community": "Сообщество",
    "can_groups": "Группы / Чаты",
    "can_homepage": "Главная сайта",
    "can_dashboard_blocks": "Блоки кабинета",
    "can_radio_downtempo": "Радио Down Tempo",
    "can_training_bot": "Бот обучающих постов (Telegram)",
    "can_ai_unlimited": "Безлимит AI (для пользователя)",
}
PERMISSION_ITEMS = [(k, PERM_LABELS.get(k, k)) for k in PERM_KEYS]


from services.referral_shop_prefs import normalize_referral_shop_url_for_save as _normalize_referral_shop_url_for_save


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


def _normalize_visibility_scope(raw: Optional[str]) -> str:
    return "referrals" if (raw or "").strip().lower() in ("referrals", "only_referrals") else "all"


async def require_admin(request: Request):
    """Basic admin check — role=admin|moderator."""
    user = await get_user_from_request(request)
    if not user or user.get("role") not in ("admin", "moderator"):
        return None
    return user


async def require_permission(request: Request, perm: str):
    """Return user only if role grants permission (or platform owner)."""
    user = await get_user_from_request(request)
    if not user or user.get("role") not in ("admin", "moderator"):
        return None
    if is_platform_owner(user):
        return user
    if user.get("role") == "admin":
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
    if is_platform_owner(user):
        return {k: True for k in PERM_KEYS}
    if (user.get("role") or "") == "admin":
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

    ai_community_bot = None
    if perms.get("can_dashboard") or perms.get("can_users"):
        try:
            _ab = await load_bot_settings_row()
            ai_community_bot = dict(_ab) if _ab else None
        except Exception:
            ai_community_bot = None

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
            "ai_community_bot": ai_community_bot,
        },
    )


# ─── AI Settings ──────────────────────────────────────────────────────────────


async def _ai_settings_latest_triple() -> tuple[str, str, int]:
    """Текущие system_prompt, retrieval_mode, retrieval_top_k (для частичного сохранения)."""
    from ai.system_prompt import DEFAULT_SYSTEM_PROMPT

    row = await database.fetch_one(
        ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(1)
    )
    if not row:
        return (DEFAULT_SYSTEM_PROMPT, "title_first", 24)
    r = dict(row)
    sp = (r.get("system_prompt") or "").strip() or DEFAULT_SYSTEM_PROMPT
    mode = (r.get("retrieval_mode") or "title_first").strip() or "title_first"
    try:
        tk = int(r.get("retrieval_top_k") or 24)
    except (TypeError, ValueError):
        tk = 24
    return (sp, mode, tk)


def _ai_normalize_mode_topk(retrieval_mode: str, retrieval_top_k: int) -> tuple[str, int]:
    valid_modes = {m[0] for m in AI_RETRIEVAL_MODES}
    mode = retrieval_mode if retrieval_mode in valid_modes else "title_first"
    top_k = max(6, min(80, int(retrieval_top_k or 24)))
    return mode, top_k


async def _ai_settings_persist(system_prompt: str, retrieval_mode: str, retrieval_top_k: int, admin_id: int) -> None:
    mode, top_k = _ai_normalize_mode_topk(retrieval_mode, retrieval_top_k)
    await database.execute(
        ai_settings.insert().values(
            system_prompt=system_prompt,
            retrieval_mode=mode,
            retrieval_top_k=top_k,
            updated_by=admin_id,
        )
    )


@router.get("/ai", response_class=HTMLResponse)
async def ai_settings_page(request: Request):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return RedirectResponse("/login")

    from ai.system_prompt import DEFAULT_SYSTEM_PROMPT
    row = await database.fetch_one(
        ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(1)
    )
    row_d = dict(row) if row else {}
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
            "ai_retrieval_modes": AI_RETRIEVAL_MODES,
            "current_retrieval_mode": (row_d.get("retrieval_mode") or "title_first"),
            "current_retrieval_top_k": int(row_d.get("retrieval_top_k") or 24),
        },
    )


@router.post("/ai")
async def update_ai_settings(
    request: Request,
    system_prompt: str = Form(...),
    retrieval_mode: str = Form("title_first"),
    retrieval_top_k: int = Form(24),
):
    """Совместимость: одна форма сохраняет и промпт, и RAG (если кто-то шлёт старый запрос)."""
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await _ai_settings_persist(system_prompt.strip(), retrieval_mode, retrieval_top_k, admin["id"])
    return RedirectResponse("/admin/ai?saved=all", status_code=302)


@router.post("/ai/system-prompt")
async def admin_ai_save_system_prompt(request: Request, system_prompt: str = Form(...)):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    sp_old, mode_old, tk_old = await _ai_settings_latest_triple()
    mode, top_k = _ai_normalize_mode_topk(mode_old, tk_old)
    await _ai_settings_persist(system_prompt.strip(), mode, top_k, admin["id"])
    return RedirectResponse("/admin/ai?saved=prompt", status_code=302)


@router.post("/ai/retrieval")
async def admin_ai_save_retrieval(
    request: Request,
    retrieval_mode: str = Form("title_first"),
    retrieval_top_k: int = Form(24),
):
    admin = await require_permission(request, "can_ai")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    sp_old, _, _ = await _ai_settings_latest_triple()
    await _ai_settings_persist(sp_old, retrieval_mode, retrieval_top_k, admin["id"])
    return RedirectResponse("/admin/ai?saved=retrieval", status_code=302)


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
    from services.referral_shop_link_policy import get_referral_shop_link_policy
    from services.shop_referral_hub import get_shop_referral_hub

    hub = await get_shop_referral_hub()
    pol = await get_referral_shop_link_policy()
    ids = hub.get("exclusive_catalog") or {}
    sel_txt = ", ".join(str(x) for x in (ids.get("seller_user_ids") or []))
    tb = hub.get("transition_banner") or {}
    sel_tb_txt = ", ".join(str(x) for x in (tb.get("seller_user_ids") or []))
    return templates.TemplateResponse(
        "dashboard/admin_shop.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "products": all_products,
            "shop_referral_hub": hub,
            "referral_shop_link_policy": pol,
            "shop_hub_selected_ids_text": sel_txt,
            "shop_hub_transition_selected_ids_text": sel_tb_txt,
        },
    )


def _parse_comma_user_ids(text: str) -> list[int]:
    raw_ids: list[int] = []
    for part in (text or "").replace(";", ",").split(","):
        p = part.strip()
        if p.isdigit():
            raw_ids.append(int(p))
    return raw_ids


@router.post("/shop/referral-hub/exclusive")
async def admin_shop_referral_hub_exclusive(
    request: Request,
    exclusive_mode: str = Form("off"),
    seller_user_ids: str = Form(""),
):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return RedirectResponse("/login", status_code=302)
    mode = (exclusive_mode or "off").strip().lower()
    if mode not in ("off", "all_maxi_sellers", "selected"):
        mode = "off"
    from services.shop_referral_hub import set_shop_referral_hub

    await set_shop_referral_hub({"exclusive_catalog": {"mode": mode, "seller_user_ids": _parse_comma_user_ids(seller_user_ids)}})
    return RedirectResponse("/admin/shop?hub_saved=exclusive", status_code=303)


@router.post("/shop/referral-hub/grace")
async def admin_shop_referral_hub_grace(request: Request, grace_days: str = Form("5")):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return RedirectResponse("/login", status_code=302)
    try:
        gd = int(float(str(grace_days).strip() or "5"))
    except (TypeError, ValueError):
        gd = 5
    from services.shop_referral_hub import set_shop_referral_hub

    await set_shop_referral_hub({"grace_days_after_maxi_end": gd})
    return RedirectResponse("/admin/shop?hub_saved=grace", status_code=303)


@router.post("/shop/referral-hub/ai-link")
async def admin_shop_referral_hub_ai_link(request: Request, single_link_ai: str = Form("")):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return RedirectResponse("/login", status_code=302)
    from services.shop_referral_hub import set_shop_referral_hub

    await set_shop_referral_hub(
        {
            "single_link_ai_for_exclusive": str(single_link_ai).strip().lower()
            in ("1", "true", "on", "yes"),
        }
    )
    return RedirectResponse("/admin/shop?hub_saved=ai_link", status_code=303)


@router.post("/shop/referral-hub/transition")
async def admin_shop_referral_hub_transition(
    request: Request,
    transition_enabled: str = Form(""),
    transition_days_after_grace: str = Form("1"),
    transition_scope_mode: str = Form("same_as_exclusive"),
    transition_seller_user_ids: str = Form(""),
):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return RedirectResponse("/login", status_code=302)
    tscope = (transition_scope_mode or "same_as_exclusive").strip().lower()
    if tscope not in ("off", "same_as_exclusive", "all_maxi_sellers", "selected"):
        tscope = "same_as_exclusive"
    try:
        td = int(float(str(transition_days_after_grace).strip() or "1"))
    except (TypeError, ValueError):
        td = 1
    from services.shop_referral_hub import set_shop_referral_hub

    await set_shop_referral_hub(
        {
            "transition_banner": {
                "enabled": str(transition_enabled).strip().lower() in ("1", "true", "on", "yes"),
                "days_after_grace": td,
                "scope_mode": tscope,
                "seller_user_ids": _parse_comma_user_ids(transition_seller_user_ids),
            },
        }
    )
    return RedirectResponse("/admin/shop?hub_saved=transition", status_code=303)


@router.post("/shop/referral-hub-save")
async def admin_shop_referral_hub_save_legacy(
    request: Request,
    exclusive_mode: str = Form("off"),
    seller_user_ids: str = Form(""),
    grace_days: str = Form("5"),
    single_link_ai: str = Form(""),
    transition_enabled: str = Form(""),
    transition_days_after_grace: str = Form("1"),
    transition_scope_mode: str = Form("same_as_exclusive"),
    transition_seller_user_ids: str = Form(""),
):
    """Совместимость: одна форма обновляет весь хаб (скрипты / старые закладки)."""
    admin = await require_permission(request, "can_shop")
    if not admin:
        return RedirectResponse("/login", status_code=302)
    mode = (exclusive_mode or "off").strip().lower()
    if mode not in ("off", "all_maxi_sellers", "selected"):
        mode = "off"
    raw_ids = _parse_comma_user_ids(seller_user_ids)
    try:
        gd = int(float(str(grace_days).strip() or "5"))
    except (TypeError, ValueError):
        gd = 5
    tscope = (transition_scope_mode or "same_as_exclusive").strip().lower()
    if tscope not in ("off", "same_as_exclusive", "all_maxi_sellers", "selected"):
        tscope = "same_as_exclusive"
    t_raw_ids = _parse_comma_user_ids(transition_seller_user_ids)
    try:
        td = int(float(str(transition_days_after_grace).strip() or "1"))
    except (TypeError, ValueError):
        td = 1
    from services.shop_referral_hub import set_shop_referral_hub

    await set_shop_referral_hub(
        {
            "exclusive_catalog": {"mode": mode, "seller_user_ids": raw_ids},
            "grace_days_after_maxi_end": gd,
            "single_link_ai_for_exclusive": str(single_link_ai).strip().lower()
            in ("1", "true", "on", "yes"),
            "transition_banner": {
                "enabled": str(transition_enabled).strip().lower() in ("1", "true", "on", "yes"),
                "days_after_grace": td,
                "scope_mode": tscope,
                "seller_user_ids": t_raw_ids,
            },
        }
    )
    return RedirectResponse("/admin/shop?hub_saved=all", status_code=303)


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
    # Keep response JSON-safe for frontend edit modal
    created_at = p.get("created_at")
    if created_at is not None:
        try:
            p["created_at"] = created_at.isoformat()
        except Exception:
            p["created_at"] = str(created_at)
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
    p["extra_image_lines"] = extra_image_lines_from_json(p.get("image_urls_json"))
    p["visibility_scope"] = _normalize_visibility_scope(p.get("visibility_scope"))
    if p.get("price_old") is not None:
        try:
            p["price_old"] = int(p["price_old"])
        except (TypeError, ValueError):
            p["price_old"] = None
    p["verified_personal"] = bool(p.get("verified_personal"))
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
    brand_name: str = Form(""),
    price_old: str = Form(""),
    extra_image_urls: str = Form(""),
    verified_personal: str = Form(""),
    visibility_scope: str = Form("all"),
):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    price_val = _parse_form_price(price)
    pov = _parse_form_price(price_old) if (price_old or "").strip() else None
    if pov is not None and pov <= 0:
        pov = None
    extra_j = extra_image_urls_from_text(extra_image_urls)
    await database.execute(
        shop_products.insert().values(
            seller_id=None,
            name=name, description=description, price=price_val,
            url=url or None, mushroom_type=mushroom_type or None,
            image_url=image_url or None, category=category or None,
            in_stock=(in_stock == "true"),
            brand_name=(brand_name or "").strip() or None,
            price_old=pov,
            image_urls_json=extra_j,
            verified_personal=(verified_personal == "true"),
            visibility_scope=_normalize_visibility_scope(visibility_scope),
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
    brand_name: str = Form(""),
    price_old: str = Form(""),
    extra_image_urls: str = Form(""),
    verified_personal: str = Form(""),
    visibility_scope: str = Form("all"),
):
    admin = await require_permission(request, "can_shop")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    exists = await database.fetch_one(shop_products.select().where(shop_products.c.id == product_id))
    if not exists:
        return JSONResponse({"ok": False, "error": "Товар не найден"}, status_code=404)

    price_val = _parse_form_price(price)
    pov = _parse_form_price(price_old) if (price_old or "").strip() else None
    if pov is not None and pov <= 0:
        pov = None
    extra_j = extra_image_urls_from_text(extra_image_urls)
    try:
        await database.execute(
            shop_products.update().where(shop_products.c.id == product_id).values(
                name=name, description=description, price=price_val,
                url=url or None, mushroom_type=mushroom_type or None,
                image_url=image_url or None, category=category or None,
                in_stock=(in_stock == "true"),
                brand_name=(brand_name or "").strip() or None,
                price_old=pov,
                image_urls_json=extra_j,
                verified_personal=(verified_personal == "true"),
                visibility_scope=_normalize_visibility_scope(visibility_scope),
            )
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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
async def users_list(request: Request, search: str = "", shop_partners: str = ""):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")

    query = users.select().where(users.c.primary_user_id == None).order_by(users.c.created_at.desc())
    if (shop_partners or "").strip().lower() in ("1", "true", "yes", "on"):
        query = query.where(
            sqlalchemy.or_(
                sqlalchemy.and_(
                    users.c.referral_shop_url.isnot(None),
                    users.c.referral_shop_url != "",
                ),
                users.c.referral_shop_partner_self == True,
            )
        )
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
        d["is_protected_row"] = (d.get("role") == "admin") or is_platform_owner(d)
        enriched_users.append(d)

    plans_eff = await get_effective_plans()
    return templates.TemplateResponse(
        "dashboard/admin_users.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "users": enriched_users,
            "search": search,
            "shop_partners_filter": (shop_partners or "").strip().lower() in ("1", "true", "yes", "on"),
            "msg_counts": msg_counts,
            "now": datetime.utcnow(),
            "plan_labels": {k: v["name"] for k, v in plans_eff.items()},
            "plan_modal_rows": [
                (pk, plans_eff[pk]["name"], plans_eff[pk]["price"])
                for pk in plans_eff.keys()
            ],
            "viewer_is_platform_owner": is_platform_owner(admin),
            "permission_items": PERMISSION_ITEMS,
            "permission_keys": [k for (k, _lbl) in PERMISSION_ITEMS],
        },
    )


@router.post("/users/set-role")
async def set_user_role(request: Request, user_id: int = Form(...), role: str = Form(...)):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    if not is_platform_owner(admin):
        return JSONResponse({"error": "Только главный администратор может назначать роли"}, status_code=403)

    if role not in ("admin", "user", "moderator"):
        return JSONResponse({"error": "invalid role"}, status_code=400)

    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "user not found"}, status_code=404)
    if is_platform_owner(dict(target)):
        return JSONResponse({"error": "Нельзя изменить роль главного администратора"}, status_code=403)

    await database.execute(users.update().where(users.c.id == user_id).values(role=role))

    notify_uid = int(target.get("primary_user_id") or user_id)
    role_label = "Администратор" if role == "admin" else ("Модератор" if role == "moderator" else "Пользователь")
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    role_body = (
        f"Вам назначена роль: {role_label}.\n"
        "Доступ в админке/модерации обновлен.\n\n"
        f"Открыть приложение: {site}"
    )
    try:
        await deliver_system_support_notification(recipient_user_id=notify_uid, body_plain=role_body)
    except Exception:
        logger.exception("Failed to deliver role change notification for user_id=%s", user_id)

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


@router.post("/users/{user_id}/marketplace-visibility")
async def set_marketplace_visibility_scope(request: Request, user_id: int, scope: str = Form("all")):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)
    normalized = _normalize_visibility_scope(scope)
    await database.execute(
        users.update().where(users.c.id == user_id).values(marketplace_visibility_scope=normalized)
    )
    return JSONResponse({"ok": True, "user_id": user_id, "marketplace_visibility_scope": normalized})


@router.post("/users/{user_id}/referral-shop-url")
async def set_user_referral_shop_url(request: Request, user_id: int, url: str = Form("")):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    form = await request.form()
    keep_self = (form.get("keep_partner_self") or "").strip().lower() in ("1", "true", "yes", "on")
    prev_self = bool(target.get("referral_shop_partner_self"))
    try:
        normalized = await _normalize_referral_shop_url_for_save(url, saver_user_id=user_id)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    if not normalized:
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(referral_shop_url=None, referral_shop_partner_self=False)
        )
        return JSONResponse({"ok": True, "user_id": user_id, "referral_shop_url": None})
    new_self = bool(keep_self and prev_self)
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(referral_shop_url=normalized, referral_shop_partner_self=new_self)
    )
    return JSONResponse(
        {"ok": True, "user_id": user_id, "referral_shop_url": normalized, "referral_shop_partner_self": new_self}
    )


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

    if not is_platform_owner(admin):
        return JSONResponse({"error": "Только главный администратор может назначать права"}, status_code=403)

    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)
    if is_platform_owner(dict(target)):
        return JSONResponse({"error": "Нельзя изменить права главного администратора"}, status_code=403)

    body = await request.json()
    existing = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == user_id)
    )
    prev_perms = {k: bool(existing.get(k)) if existing else False for k in PERM_KEYS}
    if body.get("revoke_all"):
        perms = {k: False for k in PERM_KEYS}
        await database.execute(
            admin_permissions.delete().where(admin_permissions.c.user_id == user_id)
        )
        await database.execute(
            users.update().where(users.c.id == user_id).values(role="user")
        )
    else:
        perms = {k: bool(body.get(k, False)) for k in PERM_KEYS}
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
            users.update().where(users.c.id == user_id).values(
                role=sqlalchemy.case(
                    (users.c.role == "user", "moderator"),
                    else_=users.c.role,
                )
            )
        )

    notify_uid = int(target.get("primary_user_id") or user_id)

    added = [k for k in PERM_KEYS if perms.get(k) and not prev_perms.get(k)]
    removed = [k for k in PERM_KEYS if prev_perms.get(k) and not perms.get(k)]
    if added or removed or body.get("revoke_all"):
        role_row = await database.fetch_one(users.select().where(users.c.id == notify_uid))
        role_now = (role_row.get("role") if role_row else target.get("role") or "user")
        role_label = "Администратор" if role_now == "admin" else ("Модератор" if role_now == "moderator" else "Пользователь")
        site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")

        def _label(key: str) -> str:
            for k, lbl in PERMISSION_ITEMS:
                if k == key:
                    return lbl
            return key

        added_text = ", ".join(_label(k) for k in added) if added else "—"
        removed_text = ", ".join(_label(k) for k in removed) if removed else "—"
        perm_body = (
            "Ваши права доступа обновлены.\n"
            f"Добавлено: {added_text}\n"
            f"Удалено: {removed_text}\n"
            f"Роль в приложении: {role_label}.\n\n"
            f"Открыть приложение: {site}"
        )
        try:
            await deliver_system_support_notification(recipient_user_id=notify_uid, body_plain=perm_body)
        except Exception:
            logger.exception("Failed to deliver permissions change notification for user_id=%s", user_id)

    return JSONResponse({"ok": True, "permissions": perms})


@router.post("/users/{user_id}/ban")
async def ban_user(request: Request, user_id: int):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return JSONResponse({"error": "not found"}, status_code=404)
    if is_platform_owner(dict(target)):
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

    plans_eff = await get_effective_plans()
    plan = (plan or "").strip().lower()
    if plan not in plans_eff:
        return JSONResponse({"error": "invalid plan"}, status_code=400)

    sub_end_str = None
    sub_unlimited = False
    try:
        form = await request.form()
        sub_end_str = form.get("subscription_end")
        sub_unlimited = str(form.get("subscription_unlimited") or "").strip().lower() in ("1", "true", "on", "yes")
    except Exception:
        pass

    if plan == "free":
        end_date = None
    elif sub_unlimited:
        end_date = None
    elif sub_end_str:
        try:
            end_date = datetime.strptime(str(sub_end_str), "%Y-%m-%d")
        except ValueError:
            end_date = datetime.utcnow() + timedelta(days=30)
    else:
        end_date = datetime.utcnow() + timedelta(days=30)

    granted = plan != "free"
    row_prev_sub = await database.fetch_one(users.select().where(users.c.id == user_id))
    if (
        plan == "free"
        and row_prev_sub
        and (row_prev_sub.get("subscription_plan") or "").lower() == "maxi"
        and bool(row_prev_sub.get("marketplace_seller"))
    ):
        try:
            from services.shop_referral_hub import schedule_maxi_perks_grace

            await schedule_maxi_perks_grace(int(user_id))
        except Exception:
            pass
    sub_adm_vals = {
        "subscription_plan": plan,
        "subscription_end": end_date,
        "subscription_admin_granted": granted,
        "subscription_paid_lifetime": False,
        "marketplace_seller": (plan == "maxi"),
    }
    if plan == "maxi":
        sub_adm_vals["maxi_perks_grace_until"] = None
        sub_adm_vals["maxi_shop_banner_until"] = None
    await database.execute(users.update().where(users.c.id == user_id).values(**sub_adm_vals))
    now = datetime.utcnow()
    if plan == "free":
        await record_subscription_event(int(user_id), "admin", "free", 0.0, now, None, None)
    else:
        await record_subscription_event(
            int(user_id),
            "admin",
            plan,
            float(plans_eff[plan]["price"]),
            now,
            end_date,
            None,
        )
    try:
        tgt = await database.fetch_one(users.select().where(users.c.id == user_id))
        notify_uid = int(tgt.get("primary_user_id") or user_id) if tgt else user_id
        notice = await format_admin_subscription_assigned_message(
            plan,
            end_date,
            unlimited=bool(granted and sub_unlimited and plan != "free"),
        )
        await deliver_system_support_notification(recipient_user_id=notify_uid, body_plain=notice)
    except Exception:
        logger.exception("subscription admin notify failed user_id=%s", user_id)
    try:
        from services.closed_telegram_access import sync_user_telegram_closed_chats

        await sync_user_telegram_closed_chats(
            int(user_id), notify_reentry=(str(plan).lower() != "free")
        )
    except Exception:
        logger.debug("sync closed tg after admin subscription uid=%s", user_id, exc_info=True)
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
    sub_unlimited = bool(body.get("subscription_unlimited"))
    plans_eff = await get_effective_plans()
    plan = (plan or "").strip().lower()
    if plan not in plans_eff:
        return JSONResponse({"error": "invalid plan"}, status_code=400)

    if plan == "free":
        end_date = None
    elif sub_unlimited:
        end_date = None
    elif plan_expires_at:
        try:
            end_date = datetime.strptime(str(plan_expires_at), "%Y-%m-%d")
        except ValueError:
            end_date = datetime.utcnow() + timedelta(days=30)
    else:
        end_date = datetime.utcnow() + timedelta(days=30)
    granted = plan != "free"
    row_prev_patch = await database.fetch_one(users.select().where(users.c.id == user_id))
    if (
        plan == "free"
        and row_prev_patch
        and (row_prev_patch.get("subscription_plan") or "").lower() == "maxi"
        and bool(row_prev_patch.get("marketplace_seller"))
    ):
        try:
            from services.shop_referral_hub import schedule_maxi_perks_grace

            await schedule_maxi_perks_grace(int(user_id))
        except Exception:
            pass
    patch_plan_vals = {
        "subscription_plan": plan,
        "subscription_end": end_date,
        "subscription_admin_granted": granted,
        "subscription_paid_lifetime": False,
        "marketplace_seller": (plan == "maxi"),
    }
    if plan == "maxi":
        patch_plan_vals["maxi_perks_grace_until"] = None
        patch_plan_vals["maxi_shop_banner_until"] = None
    await database.execute(users.update().where(users.c.id == user_id).values(**patch_plan_vals))
    now = datetime.utcnow()
    if plan == "free":
        await record_subscription_event(int(user_id), "admin", "free", 0.0, now, None, None)
    else:
        await record_subscription_event(
            int(user_id),
            "admin",
            plan,
            float(plans_eff[plan]["price"]),
            now,
            end_date,
            None,
        )
    try:
        tgt = await database.fetch_one(users.select().where(users.c.id == user_id))
        notify_uid = int(tgt.get("primary_user_id") or user_id) if tgt else user_id
        notice = await format_admin_subscription_assigned_message(
            plan,
            end_date,
            unlimited=bool(granted and sub_unlimited and plan != "free"),
        )
        await deliver_system_support_notification(recipient_user_id=notify_uid, body_plain=notice)
    except Exception:
        logger.exception("subscription admin notify (patch) failed user_id=%s", user_id)
    try:
        from services.closed_telegram_access import sync_user_telegram_closed_chats

        await sync_user_telegram_closed_chats(
            int(user_id), notify_reentry=(str(plan).lower() != "free")
        )
    except Exception:
        logger.debug("sync closed tg after admin patch plan uid=%s", user_id, exc_info=True)
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

    return templates.TemplateResponse(
        "dashboard/admin_broadcast.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "error": "Рассылка в Telegram отключена в этой сборке.",
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
    admin = await require_permission(request, "can_community")
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
    admin = await require_permission(request, "can_community")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(community_likes.delete().where(community_likes.c.post_id == post_id))
    await database.execute(community_comments.delete().where(community_comments.c.post_id == post_id))
    await database.execute(community_saved.delete().where(community_saved.c.post_id == post_id))
    await database.execute(community_posts.delete().where(community_posts.c.id == post_id))
    return JSONResponse({"ok": True})


@router.post("/community/posts/{post_id}/pin")
async def pin_community_post(request: Request, post_id: int):
    admin = await require_permission(request, "can_community")
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


# ─── Группы / Чаты (заглушка; старый CRUD /admin/groups снят с продукта) ───────

@router.get("/groups", response_class=HTMLResponse)
async def admin_groups_legacy_redirect(request: Request):
    admin = await require_permission(request, "can_groups")
    if not admin:
        return RedirectResponse("/login")
    return RedirectResponse("/admin/groups-chats", status_code=302)


@router.get("/groups-chats", response_class=HTMLResponse)
async def admin_groups_chats_placeholder(request: Request):
    admin = await require_permission(request, "can_groups")
    if not admin:
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        "dashboard/admin_groups_chats_placeholder.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
        },
    )


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
    if is_platform_owner(dict(target)):
        return JSONResponse({"error": "protected"}, status_code=403)
    if (target.get("role") or "") == "admin":
        return JSONResponse({"error": "Нельзя удалить администратора"}, status_code=403)

    ok, err = await permanently_delete_user(user_id)
    if not ok:
        return JSONResponse(
            {"ok": False, "error": f"Не удалось удалить пользователя: {err or 'unknown'}"},
            status_code=500 if err != "not_found" else 404,
        )
    return JSONResponse({"ok": True})


# ─── AI Training Posts ─────────────────────────────────────────────────────────

@router.get("/ai-posts", response_class=HTMLResponse)
async def ai_posts_page(request: Request):
    admin = await require_permission(request, "can_ai_posts")
    if not admin:
        return RedirectResponse("/login")
    per_page = 10
    try:
        page = max(1, int(request.query_params.get("page") or "1"))
    except (TypeError, ValueError):
        page = 1

    try:
        try:
            await database.execute(ai_training_folders.insert().values(name="Без папки"))
        except Exception:
            pass
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
    folder_order = sorted(set(folder_order), key=lambda x: (0 if x == "Без папки" else 1, x.lower()))
    if "Без папки" not in folder_order:
        folder_order.insert(0, "Без папки")
        posts_by_folder["Без папки"] = []
    folder_options = list(folder_order)
    for fn in list(posts_by_folder.keys()):
        posts_by_folder[fn] = sorted(
            posts_by_folder[fn],
            key=lambda p: ((p.get("title") or "").strip().lower(), str(p.get("id") or "")),
        )

    focus_raw = (request.query_params.get("folder") or "").strip()
    focused_folder: Optional[str] = None
    relocatable_posts: list = []
    if focus_raw:
        match = next((x for x in folder_order if x == focus_raw), None)
        if match is not None:
            focused_folder = match
            for p in posts_list:
                fn = (p.get("folder") or "").strip() or "Без папки"
                if fn != focused_folder:
                    relocatable_posts.append(p)
    relocatable_posts = sorted(
        relocatable_posts,
        key=lambda p: ((p.get("title") or "").strip().lower(), str(p.get("id") or "")),
    )

    paginated_posts: list = []
    total_pages = 1
    page_nums: list[int] = []
    if focused_folder:
        all_in_folder = posts_by_folder.get(focused_folder, [])
        n = len(all_in_folder)
        total_pages = max(1, math.ceil(n / per_page))
        if page > total_pages:
            page = total_pages
        start = (page - 1) * per_page
        paginated_posts = all_in_folder[start : start + per_page]
        page_nums = list(range(1, total_pages + 1))

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
            "focused_folder": focused_folder,
            "relocatable_posts": relocatable_posts,
            "paginated_posts": paginated_posts,
            "page": page,
            "total_pages": total_pages,
            "per_page": per_page,
            "page_nums": page_nums,
        },
    )


@router.get("/ai-posts/{post_id}/edit", response_class=HTMLResponse)
async def ai_post_edit_page(request: Request, post_id: int):
    admin = await require_permission(request, "can_ai_posts")
    if not admin:
        return RedirectResponse("/login")
    row = await database.fetch_one(ai_training_posts.select().where(ai_training_posts.c.id == post_id))
    if not row:
        return RedirectResponse("/admin/ai-posts")
    post = dict(row)
    rf = (request.query_params.get("rf") or "").strip()
    rp = (request.query_params.get("rp") or "1").strip()
    try:
        folder_rows = await database.fetch_all(ai_training_folders.select().order_by(ai_training_folders.c.name))
        extra = [r["name"] for r in folder_rows]
    except Exception:
        extra = []
    opts_set = set(extra)
    opts_set.add("Без папки")
    raw_f = (post.get("folder") or "").strip()
    current_folder_label = "Без папки" if not raw_f else raw_f
    if current_folder_label != "Без папки":
        opts_set.add(current_folder_label)
    folder_edit_options = sorted(opts_set, key=lambda x: (0 if x == "Без папки" else 1, x.lower()))
    folder_is_custom = current_folder_label not in folder_edit_options
    return templates.TemplateResponse(
        "dashboard/admin_ai_post_edit.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": await get_user_permissions(admin),
            "post": post,
            "folder_edit_options": folder_edit_options,
            "current_folder_label": current_folder_label,
            "folder_is_custom": folder_is_custom,
            "return_folder": rf,
            "return_page": rp,
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
    admin = await require_permission(request, "can_ai_posts")
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


@router.post("/ai-posts/bulk-move-folder")
async def bulk_move_ai_posts_to_folder(
    request: Request,
    folder: str = Form(""),
    post_ids: str = Form(""),
):
    admin = await require_permission(request, "can_ai_posts")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    raw = (post_ids or "").replace(" ", "")
    ids: list[int] = []
    for part in raw.split(","):
        if part.isdigit():
            ids.append(int(part))
    if not ids:
        return JSONResponse({"error": "post_ids required"}, status_code=400)
    fn = (folder or "").strip()
    if fn == "Без папки":
        fn = ""
    db_folder = fn or None
    for pid in ids:
        row = await database.fetch_one(ai_training_posts.select().where(ai_training_posts.c.id == pid))
        if not row:
            continue
        await database.execute(
            ai_training_posts.update()
            .where(ai_training_posts.c.id == pid)
            .values(folder=db_folder)
        )
    if db_folder:
        try:
            await database.execute(ai_training_folders.insert().values(name=db_folder))
        except Exception:
            pass
    return JSONResponse({"ok": True, "moved": len(ids)})


@router.get("/ai-posts/{post_id}/one")
async def get_ai_post_one(request: Request, post_id: int):
    admin = await require_permission(request, "can_ai_posts")
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
    admin = await require_permission(request, "can_ai_posts")
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
    admin = await require_permission(request, "can_ai_posts")
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
    admin = await require_permission(request, "can_ai_posts")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    nm = (name or "").strip()
    if not nm:
        return JSONResponse({"error": "name required"}, status_code=400)
    await database.execute(ai_training_folders.delete().where(ai_training_folders.c.name == nm))
    return JSONResponse({"ok": True})


@router.post("/ai-folders/clear")
async def clear_ai_folder_posts(request: Request, name: str = Form(...)):
    admin = await require_permission(request, "can_ai_posts")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    nm = (name or "").strip()
    if not nm or nm == "Без папки":
        return JSONResponse({"error": "folder required"}, status_code=400)
    await database.execute(
        ai_training_posts.update()
        .where(ai_training_posts.c.folder == nm)
        .values(folder=None)
    )
    return JSONResponse({"ok": True})


@router.post("/ai-folders/delete-safe")
async def delete_ai_folder_safe(
    request: Request,
    name: str = Form(...),
    move_posts_to_no_folder: str = Form("true"),
):
    admin = await require_permission(request, "can_ai_posts")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    nm = (name or "").strip()
    if not nm or nm == "Без папки":
        return JSONResponse({"error": "folder required"}, status_code=400)
    do_move = str(move_posts_to_no_folder).lower() in {"1", "true", "yes", "on"}
    if do_move:
        await database.execute(
            ai_training_posts.update()
            .where(ai_training_posts.c.folder == nm)
            .values(folder=None)
        )
    await database.execute(ai_training_folders.delete().where(ai_training_folders.c.name == nm))
    return JSONResponse({"ok": True, "moved": do_move})


@router.delete("/ai-posts/{post_id}")
async def delete_ai_post(request: Request, post_id: int):
    admin = await require_permission(request, "can_ai_posts")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        await database.execute(ai_training_posts.delete().where(ai_training_posts.c.id == post_id))
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.patch("/ai-posts/{post_id}/toggle")
async def toggle_ai_post(request: Request, post_id: int):
    admin = await require_permission(request, "can_ai_posts")
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
    admin = await require_permission(request, "can_homepage")
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
    admin = await require_permission(request, "can_homepage")
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
    admin = await require_permission(request, "can_homepage")
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
    admin = await require_permission(request, "can_dashboard_blocks")
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
    admin = await require_permission(request, "can_dashboard_blocks")
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
    admin = await require_permission(request, "can_dashboard_blocks")
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
    admin = await require_permission(request, "can_dashboard_blocks")
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
    admin = await require_permission(request, "can_dashboard_blocks")
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
    admin = await require_permission(request, "can_dashboard_blocks")
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
    admin = await require_permission(request, "can_dashboard_blocks")
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


# ─── Настройки контента (кликабельность ссылок) ───────────────────────────────

@router.get("/content-settings", response_class=HTMLResponse)
async def admin_content_settings_page(request: Request):
    admin = await require_permission(request, "can_groups")
    if not admin:
        return RedirectResponse("/login")
    row = await database.fetch_one(
        sqlalchemy.text("SELECT value FROM site_settings WHERE key = 'links_clickable_enabled'")
    )
    raw = str((row or {}).get("value") or "").strip().lower()
    enabled = raw not in ("false", "0", "no", "off")
    perms = await get_user_permissions(admin)
    return templates.TemplateResponse(
        "dashboard/admin_content_settings.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "links_clickable_enabled": enabled,
        },
    )


@router.post("/content-settings")
async def admin_content_settings_save(request: Request, enabled: Optional[str] = Form(None)):
    admin = await require_permission(request, "can_groups")
    if not admin:
        return RedirectResponse("/login")
    is_on = enabled in ("1", "on", "true", "yes")
    val = "true" if is_on else "false"
    await database.execute(
        sqlalchemy.text(
            """
            INSERT INTO site_settings (key, value, updated_at)
            VALUES ('links_clickable_enabled', :v, NOW())
            ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = NOW()
            """
        ),
        {"v": val},
    )
    import main as _main

    _main._gsettings_cache["ts"] = 0.0
    _main._gsettings_cache["links_clickable_enabled"] = is_on
    return RedirectResponse("/admin/content-settings?saved=1", status_code=302)


# ─── Видеосвязь (глобальный переключатель) ───────────────────────────────────

@router.get("/video-calls", response_class=HTMLResponse)
async def admin_video_calls_page(request: Request):
    admin = await require_permission(request, "can_groups")
    if not admin:
        return RedirectResponse("/login")
    row = await database.fetch_one(
        sqlalchemy.text("SELECT value FROM site_settings WHERE key = 'video_calls_enabled'")
    )
    raw = str((row or {}).get("value") or "").strip().lower()
    enabled = raw not in ("false", "0", "no", "off")
    perms = await get_user_permissions(admin)
    return templates.TemplateResponse(
        "dashboard/admin_video_calls.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "video_calls_enabled": enabled,
        },
    )


@router.post("/video-calls")
async def admin_video_calls_save(request: Request, enabled: Optional[str] = Form(None)):
    admin = await require_permission(request, "can_groups")
    if not admin:
        return RedirectResponse("/login")
    is_on = enabled in ("1", "on", "true", "yes")
    val = "true" if is_on else "false"
    await database.execute(
        sqlalchemy.text(
            """
            INSERT INTO site_settings (key, value, updated_at)
            VALUES ('video_calls_enabled', :v, NOW())
            ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = NOW()
            """
        ),
        {"v": val},
    )
    import main as _main

    _main._gsettings_cache["ts"] = 0.0
    _main._gsettings_cache["video_calls_enabled"] = is_on
    return RedirectResponse("/admin/video-calls?saved=1", status_code=302)


# ─── Radio Down Tempo ─────────────────────────────────────────────────────────

MAX_RADIO_AUDIO_BYTES = 80 * 1024 * 1024


@router.get("/radio-downtempo", response_class=HTMLResponse)
async def admin_radio_downtempo_page(request: Request):
    admin = await require_permission(request, "can_radio_downtempo")
    if not admin:
        return RedirectResponse("/login")
    from services.radio_downtempo import get_playlist_version, list_tracks_ordered

    tracks = await list_tracks_ordered()
    ver = await get_playlist_version()
    perms = await get_user_permissions(admin)
    return templates.TemplateResponse(
        "dashboard/admin_radio_downtempo.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "tracks": tracks,
            "playlist_version": ver,
        },
    )


@router.post("/radio-downtempo/upload")
async def admin_radio_downtempo_upload(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
):
    admin = await require_permission(request, "can_radio_downtempo")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from services.radio_downtempo import (
        bump_playlist_version,
        normalize_audio_content_type,
        next_sort_order,
        radio_save_dir,
        safe_audio_ext,
        slug_title,
    )

    ct = normalize_audio_content_type(file.content_type, file.filename or "")
    ext = safe_audio_ext(file.filename or "")
    if not ct or not ext:
        return JSONResponse(
            {"error": "Допустимые форматы: MP3, OGG, WAV, FLAC, M4A/AAC"},
            status_code=400,
        )
    data = await file.read()
    if len(data) > MAX_RADIO_AUDIO_BYTES:
        return JSONResponse({"error": "Файл больше 80 МБ"}, status_code=400)
    storage = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(radio_save_dir(), storage)
    with open(save_path, "wb") as f:
        f.write(data)
    ttitle = (title or "").strip() or slug_title(file.filename or storage)
    so = await next_sort_order()
    await database.execute(
        radio_downtempo_tracks.insert().values(title=ttitle, storage_name=storage, sort_order=so)
    )
    v = await bump_playlist_version()
    return JSONResponse({"ok": True, "playlist_version": v})


@router.post("/radio-downtempo/delete/{track_id}")
async def admin_radio_downtempo_delete(request: Request, track_id: int):
    admin = await require_permission(request, "can_radio_downtempo")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from services.radio_downtempo import bump_playlist_version, radio_save_dir

    row = await database.fetch_one(
        radio_downtempo_tracks.select().where(radio_downtempo_tracks.c.id == track_id)
    )
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    path = os.path.join(radio_save_dir(), row["storage_name"])
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    await database.execute(radio_downtempo_tracks.delete().where(radio_downtempo_tracks.c.id == track_id))
    v = await bump_playlist_version()
    return JSONResponse({"ok": True, "playlist_version": v})


@router.post("/radio-downtempo/reorder")
async def admin_radio_downtempo_reorder(request: Request):
    admin = await require_permission(request, "can_radio_downtempo")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from services.radio_downtempo import bump_playlist_version

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return JSONResponse({"error": "invalid"}, status_code=400)
    clean = [int(x) for x in ids]
    for i, tid in enumerate(clean):
        await database.execute(
            radio_downtempo_tracks.update()
            .where(radio_downtempo_tracks.c.id == tid)
            .values(sort_order=i)
        )
    v = await bump_playlist_version()
    return JSONResponse({"ok": True, "playlist_version": v})


@router.post("/radio-downtempo/publish")
async def admin_radio_downtempo_publish(request: Request):
    """Принудительно обновить версию плейлиста у всех слушателей (без изменения треков)."""
    admin = await require_permission(request, "can_radio_downtempo")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from services.radio_downtempo import bump_playlist_version

    v = await bump_playlist_version()
    return JSONResponse({"ok": True, "playlist_version": v})


def _admin_parse_date(s: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d")
        if end_of_day:
            return d.replace(hour=23, minute=59, second=59)
        return d.replace(hour=0, minute=0, second=0)
    except ValueError:
        return None


@router.get("/referral", response_class=HTMLResponse)
async def admin_referral_page(
    request: Request,
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    segment: str = Query("all_referred"),
    plan_filter: Optional[str] = Query(None),
    user_search: Optional[str] = Query(None),
    ref_uid: Optional[int] = Query(None),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    perms = await get_user_permissions(admin)

    d1 = _admin_parse_date(date_from, end_of_day=False)
    d2 = _admin_parse_date(date_to, end_of_day=True)

    from services import referral_admin as ra

    n_refs = await ra.count_referrals_in_period(d1, d2)
    sum_b = await ra.sum_bonuses_in_period(d1, d2)
    top_earn = await ra.top_ambassadors_by_earnings(25, d1, d2)
    top_inv = await ra.top_ambassadors_by_invite_count(25, d1, d2)
    lb_bal = await ra.leaderboard_balance_now(30)
    pend = await ra.pending_withdrawals_list()
    paid_w = await ra.paid_withdrawals_in_period(d1, d2)
    promos = await ra.list_promo_links()
    renew = await ra.renewal_ranking(30)
    bonus_events = await ra.bonus_events_in_period(d1, d2, 240)
    seg_rows, seg_total = await ra.referred_users_segment(segment, d1, d2, plan_filter)
    search_hits = await ra.search_users(user_search or "", 25) if (user_search or "").strip() else []
    ref_tree = await ra.invites_for_referrer(int(ref_uid)) if ref_uid else []

    base = (settings.SITE_URL or "").rstrip("/") or ""
    plans_eff = await get_effective_plans()

    from services.referral_shop_link_policy import get_referral_shop_link_policy
    from services.referral_bonus_settings import (
        get_referral_bonus_line1_global,
        get_referral_bonus_line2_global,
        list_users_with_bonus_override,
    )
    from services.referral_service import (
        get_referral_line_statistics,
        get_referrer_invites_detailed,
        get_second_line_invites_detailed,
    )
    from services.referral_payout_settings import (
        get_referral_min_withdrawal_rub,
        get_referral_wd_moscow_days,
    )

    partner_shop_policy = await get_referral_shop_link_policy()
    bonus_line1_global = await get_referral_bonus_line1_global()
    bonus_line2_global = await get_referral_bonus_line2_global()
    bonus_pct_overrides = await list_users_with_bonus_override(300)
    ref_explore_raw = (request.query_params.get("ref_explore") or "").strip()
    ref_explore_uid: Optional[int] = int(ref_explore_raw) if ref_explore_raw.isdigit() else None
    ref_explore_line_stats = None
    ref_explore_l1: list = []
    ref_explore_l2: list = []
    if ref_explore_uid is not None:
        ref_explore_line_stats = await get_referral_line_statistics(ref_explore_uid)
        ref_explore_l1 = await get_referrer_invites_detailed(ref_explore_uid)
        ref_explore_l2 = await get_second_line_invites_detailed(ref_explore_uid)
    ref_payout_min = await get_referral_min_withdrawal_rub()
    ref_payout_wd_lo, ref_payout_wd_hi = await get_referral_wd_moscow_days()

    from services.referral_bonus_program import get_referral_bonus_program_flags
    from services.referral_balance_ops import list_ledger_recent_global

    bonus_program_flags = await get_referral_bonus_program_flags()
    bonus_ledger_recent = await list_ledger_recent_global(100)

    return templates.TemplateResponse(
        "dashboard/admin_referral.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": perms,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "segment": segment,
            "plan_filter": plan_filter or "",
            "user_search": user_search or "",
            "ref_uid": ref_uid,
            "partner_shop_policy": partner_shop_policy,
            "bonus_line1_global": bonus_line1_global,
            "bonus_line2_global": bonus_line2_global,
            "bonus_percent_overrides": bonus_pct_overrides,
            "ref_explore_uid": ref_explore_uid,
            "ref_explore_line_stats": ref_explore_line_stats,
            "ref_explore_l1": ref_explore_l1,
            "ref_explore_l2": ref_explore_l2,
            "ref_payout_min": ref_payout_min,
            "ref_payout_wd_lo": ref_payout_wd_lo,
            "ref_payout_wd_hi": ref_payout_wd_hi,
            "payout_rules_saved": (request.query_params.get("payout_rules_saved") or "").strip() == "1",
            "payout_rules_err": (request.query_params.get("payout_rules_err") or "").strip(),
            "bonus_pct_saved": (request.query_params.get("bonus_pct_saved") or "").strip(),
            "bonus_pct_err": (request.query_params.get("bonus_pct_err") or "").strip(),
            "shop_policy_saved": (request.query_params.get("shop_policy_saved") or "").strip() == "1",
            "shop_policy_err": (request.query_params.get("shop_policy_err") or "").strip(),
            "wd_ok": (request.query_params.get("wd_ok") or "").strip() == "1",
            "wd_err": (request.query_params.get("wd_err") or "").strip(),
            "n_refs_period": n_refs,
            "sum_bonuses_period": round(sum_b, 2),
            "top_earn": top_earn,
            "top_inv": top_inv,
            "lb_bal": lb_bal,
            "pending_withdrawals": pend,
            "paid_withdrawals": paid_w,
            "promos": promos,
            "renewal_rank": renew,
            "bonus_events": bonus_events,
            "segment_rows": seg_rows,
            "segment_total": seg_total,
            "search_hits": search_hits,
            "ref_tree": ref_tree,
            "plans": plans_eff,
            "site_base": base,
            "bonus_program_flags": bonus_program_flags,
            "bonus_ledger_recent": bonus_ledger_recent,
            "ref_bonus_saved": (request.query_params.get("ref_bonus_saved") or "").strip(),
            "ref_bonus_err": (request.query_params.get("ref_bonus_err") or "").strip(),
        },
    )


@router.post("/referral/bonus-lines-global")
async def admin_referral_bonus_lines_global(
    request: Request,
    line1_percent: str = Form("5"),
    line2_percent: str = Form("5"),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from urllib.parse import quote

    from services.referral_bonus_settings import set_referral_bonus_lines_global

    try:
        v1 = float(str(line1_percent or "5").strip().replace(",", "."))
        v2 = float(str(line2_percent or "5").strip().replace(",", "."))
    except ValueError:
        return RedirectResponse("/admin/referral?bonus_pct_err=" + quote("Некорректное число"), status_code=303)
    await set_referral_bonus_lines_global(v1, v2)
    return RedirectResponse("/admin/referral?bonus_pct_saved=global", status_code=303)


@router.post("/referral/bonus-lines-user")
async def admin_referral_bonus_lines_user(
    request: Request,
    user_id: str = Form(""),
    line1_percent: str = Form(""),
    line2_percent: str = Form(""),
    clear_override: str = Form(""),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from urllib.parse import quote

    from services.referral_bonus_settings import set_user_referral_bonus_line_overrides

    raw_uid = (user_id or "").strip()
    if not raw_uid.isdigit():
        return RedirectResponse("/admin/referral?bonus_pct_err=" + quote("Укажите числовой user id"), status_code=303)
    uid = int(raw_uid)
    clear = (clear_override or "").strip().lower() in ("1", "on", "true", "yes")
    if clear:
        await set_user_referral_bonus_line_overrides(uid, None, None)
        return RedirectResponse("/admin/referral?bonus_pct_saved=user_clear", status_code=303)
    try:
        v1 = float(str(line1_percent or "").strip().replace(",", "."))
        v2 = float(str(line2_percent or "").strip().replace(",", "."))
    except ValueError:
        return RedirectResponse("/admin/referral?bonus_pct_err=" + quote("Некорректный процент"), status_code=303)
    await set_user_referral_bonus_line_overrides(uid, v1, v2)
    return RedirectResponse("/admin/referral?bonus_pct_saved=user", status_code=303)


@router.post("/referral/partner-shop-policy")
async def admin_referral_partner_shop_policy(
    request: Request,
    enforce_prefix: str = Form(""),
    required_prefix: str = Form(""),
):
    """Вкл/выкл проверки префикса партнёрской ссылки магазина + редактируемый префикс."""
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from urllib.parse import quote

    from services.referral_shop_link_policy import set_referral_shop_link_policy

    on = (enforce_prefix or "").strip().lower() in ("1", "on", "true", "yes")
    try:
        await set_referral_shop_link_policy(on, required_prefix)
    except ValueError as e:
        return RedirectResponse(
            "/admin/referral?shop_policy_err=" + quote(str(e), safe=""),
            status_code=303,
        )
    return RedirectResponse("/admin/referral?shop_policy_saved=1", status_code=303)


@router.post("/referral/payout-rules")
async def admin_referral_payout_rules(
    request: Request,
    min_withdrawal_rub: str = Form("5000"),
    moscow_day_from: str = Form("1"),
    moscow_day_to: str = Form("5"),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from urllib.parse import quote

    from services.referral_payout_settings import set_referral_payout_rules

    try:
        mn = int(float(str(min_withdrawal_rub or "5000").strip().replace(",", ".")))
        d1 = int(float(str(moscow_day_from or "1").strip().replace(",", ".")))
        d2 = int(float(str(moscow_day_to or "5").strip().replace(",", ".")))
    except ValueError:
        return RedirectResponse(
            "/admin/referral?payout_rules_err=" + quote("Некорректные числа"),
            status_code=303,
        )
    try:
        await set_referral_payout_rules(min_rub=mn, moscow_day_from=d1, moscow_day_to=d2)
    except Exception as e:
        return RedirectResponse(
            "/admin/referral?payout_rules_err=" + quote(str(e)[:200], safe=""),
            status_code=303,
        )
    return RedirectResponse("/admin/referral?payout_rules_saved=1", status_code=303)


def _form_on(form, key: str) -> bool:
    v = form.get(key)
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("1", "on", "true", "yes")


@router.post("/referral/bonus-program-flags")
async def admin_referral_bonus_program_flags(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from urllib.parse import quote

    from services.referral_bonus_program import set_referral_bonus_program_flags

    form = await request.form()
    flags = {
        "user_transfer_enabled": _form_on(form, "user_transfer_enabled"),
        "user_pay_subscription_enabled": _form_on(form, "user_pay_subscription_enabled"),
        "user_auto_renew_enabled": _form_on(form, "user_auto_renew_enabled"),
        "admin_grant_enabled": _form_on(form, "admin_grant_enabled"),
        "admin_transfer_enabled": _form_on(form, "admin_transfer_enabled"),
        "admin_pay_subscription_enabled": _form_on(form, "admin_pay_subscription_enabled"),
        "min_transfer_rub": (form.get("min_transfer_rub") or "10"),
    }
    await set_referral_bonus_program_flags(flags)
    return RedirectResponse("/admin/referral?ref_bonus_saved=program", status_code=303)


@router.post("/referral/bonus-grant")
async def admin_referral_bonus_grant(
    request: Request,
    user_id: str = Form(""),
    amount_rub: str = Form(""),
    note: str = Form(""),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from urllib.parse import quote

    from services.referral_balance_ops import admin_grant_bonuses, BonusOpError

    aid = int(admin.get("primary_user_id") or admin["id"])
    if not (user_id or "").strip().isdigit():
        return RedirectResponse(
            "/admin/referral?ref_bonus_err=" + quote("Укажите user id"),
            status_code=303,
        )
    try:
        await admin_grant_bonuses(int(user_id), amount_rub, aid, note=(note or "")[:2000])
    except BonusOpError as e:
        return RedirectResponse("/admin/referral?ref_bonus_err=" + quote(e.message, safe=""), status_code=303)
    return RedirectResponse("/admin/referral?ref_bonus_saved=grant", status_code=303)


@router.post("/referral/bonus-admin-transfer")
async def admin_referral_bonus_admin_transfer(
    request: Request,
    from_user_id: str = Form(""),
    to_user_id: str = Form(""),
    amount_rub: str = Form(""),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from urllib.parse import quote

    from services.referral_balance_ops import admin_transfer_bonuses, BonusOpError

    aid = int(admin.get("primary_user_id") or admin["id"])
    if not (from_user_id or "").strip().isdigit() or not (to_user_id or "").strip().isdigit():
        return RedirectResponse(
            "/admin/referral?ref_bonus_err=" + quote("Укажите оба user id"),
            status_code=303,
        )
    try:
        await admin_transfer_bonuses(int(from_user_id), int(to_user_id), amount_rub, aid)
    except BonusOpError as e:
        return RedirectResponse("/admin/referral?ref_bonus_err=" + quote(e.message, safe=""), status_code=303)
    return RedirectResponse("/admin/referral?ref_bonus_saved=transfer", status_code=303)


@router.post("/referral/bonus-pay-subscription")
async def admin_referral_bonus_pay_subscription(
    request: Request,
    user_id: str = Form(""),
    plan_key: str = Form("start"),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from urllib.parse import quote

    from services.referral_balance_ops import admin_pay_subscription_with_user_bonuses, BonusOpError

    aid = int(admin.get("primary_user_id") or admin["id"])
    if not (user_id or "").strip().isdigit():
        return RedirectResponse(
            "/admin/referral?ref_bonus_err=" + quote("Укажите user id"),
            status_code=303,
        )
    try:
        await admin_pay_subscription_with_user_bonuses(int(user_id), (plan_key or "start").strip().lower(), aid)
    except BonusOpError as e:
        return RedirectResponse("/admin/referral?ref_bonus_err=" + quote(e.message, safe=""), status_code=303)
    return RedirectResponse("/admin/referral?ref_bonus_saved=pay", status_code=303)


@router.post("/referral/clear-balance")
async def admin_referral_clear_balance(
    request: Request,
    withdrawal_id: str = Form(""),
    user_id: str = Form(""),
    note: str = Form(""),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from urllib.parse import quote

    from services.referral_service import admin_clear_referral_balance, admin_mark_referral_withdrawal_paid

    wid = (withdrawal_id or "").strip()
    uid = (user_id or "").strip()
    if wid.isdigit():
        ok, msg = await admin_mark_referral_withdrawal_paid(int(wid), note)
        if not ok:
            return RedirectResponse(
                "/admin/referral?wd_err=" + quote(msg or "error", safe=""),
                status_code=303,
            )
        return RedirectResponse("/admin/referral?wd_ok=1", status_code=303)
    if uid.isdigit():
        ok, msg = await admin_clear_referral_balance(int(uid), note)
        if not ok:
            err = "no_pending" if msg == "no_pending" else (msg or "error")
            return RedirectResponse("/admin/referral?wd_err=" + quote(err, safe=""), status_code=303)
        return RedirectResponse("/admin/referral?wd_ok=1", status_code=303)
    return RedirectResponse("/admin/referral?wd_err=" + quote("missing_id", safe=""), status_code=303)


@router.post("/referral/promo-create")
async def admin_referral_promo_create(
    request: Request,
    plan_key: str = Form("start"),
    period_days: int = Form(30),
    max_activations: Optional[str] = Form(None),
    valid_days: Optional[str] = Form(None),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from services.referral_service import create_referral_promo_link

    aid = admin.get("primary_user_id") or admin["id"]
    ma_raw = (max_activations or "").strip()
    max_a: Optional[int] = None
    if ma_raw.isdigit():
        max_a = int(ma_raw)
    vu: Optional[datetime] = None
    if valid_days and str(valid_days).strip().isdigit() and int(valid_days) > 0:
        vu = datetime.utcnow() + timedelta(days=int(valid_days))
    await create_referral_promo_link(
        plan_key=plan_key,
        period_days=period_days,
        max_activations=max_a,
        valid_until=vu,
        created_by=int(aid),
    )
    return RedirectResponse("/admin/referral", status_code=303)


@router.post("/referral/bulk-message")
async def admin_referral_bulk_message(
    request: Request,
    user_ids: str = Form(...),
    text: str = Form(...),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from services.support_delivery import deliver_support_message

    aid = admin.get("primary_user_id") or admin["id"]
    raw = (user_ids or "").replace(",", " ").split()
    ids = []
    for x in raw:
        try:
            ids.append(int(x.strip()))
        except ValueError:
            continue
    ids = list(dict.fromkeys(ids))[:200]
    sent = 0
    for uid in ids:
        r = await deliver_support_message(
            admin_id=int(aid),
            recipient_user_id=uid,
            text=text[:8000],
            feedback_id=None,
        )
        if r.get("ok"):
            sent += 1
    return RedirectResponse(f"/admin/referral?bulk_sent={sent}", status_code=303)


# ─── Дневник терапии (wellness journal) ─────────────────────────────────────


@router.get("/wellness-journal", response_class=HTMLResponse)
async def admin_wellness_journal_page(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    perms = await get_user_permissions(admin)
    from services.wellness_journal_service import top_wellness_responders, wellness_journal_globally_enabled

    global_on = await wellness_journal_globally_enabled()
    top = await top_wellness_responders(10, 30)
    ids = [int(t["id"]) for t in top]
    user_pause_map: dict[int, bool] = {}
    if ids:
        rows = await database.fetch_all(users.select().where(users.c.id.in_(ids)))
        for r in rows:
            user_pause_map[int(r["id"])] = bool(r.get("wellness_journal_admin_paused"))
    admin_uid = int(admin.get("primary_user_id") or admin["id"])
    me_row = await database.fetch_one(users.select().where(users.c.id == admin_uid))
    admin_wellness_ai_silent = bool(me_row.get("wellness_admin_ai_silent")) if me_row else False
    return templates.TemplateResponse(
        "dashboard/admin_wellness_journal.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "global_on": global_on,
            "top_users": top,
            "user_pause_map": user_pause_map,
            "admin_wellness_ai_silent": admin_wellness_ai_silent,
        },
    )


@router.post("/wellness-journal/test-prompt-self")
async def admin_wellness_journal_test_prompt_self(request: Request):
    """Один тестовый промпт дневника в ЛС текущему админу (расписание wellness_next_prompt_at не меняется)."""
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    uid = int(admin.get("primary_user_id") or admin["id"])
    from services.wellness_journal_service import send_wellness_prompt_for_user, user_has_wellness_journal_access

    if not await user_has_wellness_journal_access(uid):
        return RedirectResponse("/admin/wellness-journal?test_prompt=no_access", status_code=303)
    ok = await send_wellness_prompt_for_user(uid, admin_self_test=True)
    if ok:
        return RedirectResponse("/admin/wellness-journal?test_prompt=ok", status_code=303)
    return RedirectResponse("/admin/wellness-journal?test_prompt=fail", status_code=303)


@router.post("/wellness-journal/self-ai-silent")
async def admin_wellness_journal_self_ai_silent(request: Request, silent: str = Form(...)):
    """Лично для админа: без цепочки вопросов в ЛС; разбор в статистику остаётся."""
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    uid = int(admin.get("primary_user_id") or admin["id"])
    me = await database.fetch_one(users.select().where(users.c.id == uid))
    was_silent = bool(me.get("wellness_admin_ai_silent")) if me else False
    from services.wellness_journal_service import (
        kickoff_admin_wellness_chain_after_enable,
        set_wellness_admin_ai_silent,
    )

    on = (silent or "").strip() in ("1", "true", "on", "yes")
    await set_wellness_admin_ai_silent(uid, on)
    if was_silent and not on:
        await kickoff_admin_wellness_chain_after_enable(uid)
    return RedirectResponse("/admin/wellness-journal?saved=silent", status_code=303)


@router.post("/wellness-journal/self-reset-chain")
async def admin_wellness_journal_self_reset_chain(request: Request):
    """Сбросить индекс цепочки вопросов дневника для текущего админа."""
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    uid = int(admin.get("primary_user_id") or admin["id"])
    from services.wellness_journal_service import reset_wellness_admin_chain_index

    await reset_wellness_admin_chain_index(uid)
    return RedirectResponse("/admin/wellness-journal?chain_reset=1", status_code=303)


@router.post("/wellness-journal/global")
async def admin_wellness_journal_global(request: Request, enabled: str = Form(...)):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from services.wellness_journal_service import set_wellness_journal_globally_enabled

    on = (enabled or "").strip() in ("1", "true", "on", "yes")
    await set_wellness_journal_globally_enabled(on)
    return RedirectResponse("/admin/wellness-journal?saved=global", status_code=303)


@router.post("/wellness-journal/user-pause")
async def admin_wellness_journal_user_pause(request: Request, user_id: int = Form(...), paused: str = Form(...)):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    pause = (paused or "").strip() in ("1", "true", "on", "yes")
    await database.execute(
        users.update()
        .where(users.c.id == int(user_id))
        .values(wellness_journal_admin_paused=pause)
    )
    return RedirectResponse("/admin/wellness-journal?saved=pause", status_code=303)


@router.get("/wellness-journal/user/{user_id}", response_class=HTMLResponse)
async def admin_wellness_journal_user(request: Request, user_id: int):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    perms = await get_user_permissions(admin)
    from services.wellness_journal_service import aggregate_entries_for_display

    target = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not target:
        return RedirectResponse("/admin/wellness-journal", status_code=302)
    entries_raw = await database.fetch_all(
        wellness_journal_entries.select()
        .where(wellness_journal_entries.c.user_id == int(user_id))
        .order_by(wellness_journal_entries.c.created_at.desc())
        .limit(120)
    )
    raw_dicts = [dict(e) for e in entries_raw]
    agg = aggregate_entries_for_display(raw_dicts)
    from services.wellness_ai_profile_service import therapy_dashboard_panel

    wellness_therapy_panel = await therapy_dashboard_panel(int(user_id))
    entries = []
    for d in raw_dicts:
        ca = d.get("created_at")
        d2 = dict(d)
        d2["created_at"] = ca.strftime("%d.%m.%Y %H:%M") if ca else ""
        entries.append(d2)
    return templates.TemplateResponse(
        "dashboard/admin_wellness_user.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "target": dict(target),
            "entries": entries,
            "agg": agg,
            "wellness_therapy_panel": wellness_therapy_panel,
        },
    )


@router.get("/wellness-journal/overview", response_class=HTMLResponse)
async def admin_wellness_overview_page(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    perms = await get_user_permissions(admin)
    from services.wellness_journal_service import admin_global_wellness_summary

    summary = await admin_global_wellness_summary()
    return templates.TemplateResponse(
        "dashboard/admin_wellness_overview.html",
        {
            "request": request,
            "user": admin,
            "user_permissions": perms,
            "summary": summary,
            "pipeline_ok": (request.query_params.get("pipeline") or "").strip() == "1",
        },
    )


@router.post("/wellness-journal/overview/run-pipeline")
async def admin_wellness_overview_run_pipeline(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from services.wellness_analytics_pipeline import run_wellness_analytics_pipeline

    await run_wellness_analytics_pipeline()
    return RedirectResponse("/admin/wellness-journal/overview?pipeline=1", status_code=303)


@router.get("/wellness-journal/overview/pdf")
async def admin_wellness_overview_pdf(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from services.wellness_journal_service import admin_global_wellness_summary
    from services.pdf_service import generate_wellness_admin_overview_pdf

    s = await admin_global_wellness_summary()
    mush = s.get("mushroom_top") or []
    mush_txt = "\n".join(f"• {m[0]}: {m[1]}" for m in mush) or "—"
    sections = [
        (
            "Охват",
            f"Пользователей с ответами в дневнике (всего): {s.get('users_with_replies_ever', 0)}\n"
            f"Активных за 30 дней (хотя бы один ответ): {s.get('users_with_replies_30d', 0)}\n"
            f"Ответов всего: {s.get('replies_ever', 0)}\n"
            f"Ответов за 30 дней: {s.get('replies_30d', 0)}\n"
            f"Сообщений AI (напоминаний) за 30 дней: {s.get('prompts_30d', 0)}",
        ),
        (
            "Выборка настроения/энергии (из записей с JSON, последние ~900)",
            f"Среднее настроение 0–10: {s.get('mood_avg_sample') or '—'} (n={s.get('sample_moods_n', 0)})\n"
            f"Средняя энергия 0–10: {s.get('energy_avg_sample') or '—'}",
        ),
        ("Топ упоминаний грибов (по разобранным ответам)", mush_txt),
    ]
    sch = s.get("scheme_effect_rows") or []
    if sch:
        sch_txt = "\n".join(
            f"• {r.get('mushroom_key') or ''} | сегм. {r.get('segment') or ''} | N={r.get('sample_n')} | дельта={r.get('avg_progress_score')}"
            for r in sch[:25]
        )
        sections.append(("Топ эвристики схем (дельта прогресса)", sch_txt))
    sections.append(
        (
            "Профили AI (грибы/связки)",
            f"Пользователей с заполненным профилем: {s.get('therapy_profiles_n', 0)}",
        )
    )
    pdf_bytes = generate_wellness_admin_overview_pdf(sections)
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="wellness-platform-summary.pdf"'},
    )


@router.post("/wellness-journal/user-pdf")
async def admin_wellness_journal_user_pdf(
    request: Request, user_id: int = Form(...), allow_pdf: str = Form(...)
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from services.wellness_journal_service import set_user_wellness_pdf_allowed

    ok = (allow_pdf or "").strip().lower() in ("1", "true", "on", "yes")
    await set_user_wellness_pdf_allowed(int(user_id), ok)
    return RedirectResponse("/admin/wellness-journal?saved=pdf", status_code=303)


@router.get("/wellness-journal/insights", response_class=HTMLResponse)
async def admin_wellness_journal_insights(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    perms = await get_user_permissions(admin)
    from services.wellness_insights_service import (
        admin_user_ids_with_snapshots,
        build_platform_insights_dashboard_context,
        build_user_insights_dashboard_context,
        minimal_admin_user_insights_shell,
    )

    mode = (request.query_params.get("mode") or "platform").strip().lower()
    if mode not in ("platform", "user"):
        mode = "platform"
    range_raw = (request.query_params.get("range") or "w").strip()
    user_list = await admin_user_ids_with_snapshots(200)
    uid_raw = (request.query_params.get("user_id") or "").strip()

    ctx: dict = {
        "request": request,
        "user": admin,
        "user_permissions": perms,
        "insights_mode": mode,
        "user_list": user_list,
        "insights_user_id": "",
        "insights_user_label": "",
        "refreshed": request.query_params.get("refreshed"),
    }

    if mode == "user" and uid_raw.isdigit():
        ctx["insights_user_id"] = uid_raw
        uid_f = int(uid_raw)
        urow = await database.fetch_one(users.select().where(users.c.id == uid_f))
        ctx["insights_user_label"] = (
            (urow.get("name") or "").strip() if urow else ""
        ) or f"id {uid_f}"
        dash = await build_user_insights_dashboard_context(
            uid_f,
            range_raw,
            canvas_prefix=f"adm-u{uid_f}",
            tab_url_base="/admin/wellness-journal/insights",
            tab_extra_query=f"mode=user&user_id={uid_f}",
        )
        ctx.update(dash)
    elif mode == "user":
        ctx.update(minimal_admin_user_insights_shell(range_raw))
    else:
        ctx.update(await build_platform_insights_dashboard_context(range_raw))

    schemes = await database.fetch_all(
        wellness_scheme_effect_stats.select()
        .order_by(wellness_scheme_effect_stats.c.sample_n.desc())
        .limit(80)
    )
    ctx["schemes"] = [dict(x) for x in schemes]

    if request.query_params.get("partial") == "json":
        if mode == "user" and not uid_raw.isdigit():
            return JSONResponse({"error": "user_id required"}, status_code=400)
        from web.templates_utils import template_context_for_request

        html = templates.env.get_template("components/wellness_dashboard_fragment_inner.html").render(
            **template_context_for_request(request, ctx)
        )
        return JSONResponse({"html": html, "charts": ctx.get("user_charts") or []})

    return templates.TemplateResponse("dashboard/admin_wellness_insights.html", ctx)


@router.post("/wellness-journal/insights/refresh-schemes")
async def admin_wellness_journal_insights_refresh_schemes(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from services.wellness_insights_service import refresh_scheme_effect_stats_simple

    await refresh_scheme_effect_stats_simple()
    return RedirectResponse(
        "/admin/wellness-journal/insights?mode=platform&refreshed=1", status_code=303
    )


@router.get("/wellness-analytics", response_class=HTMLResponse)
async def admin_wellness_analytics_legacy_redirect(request: Request):
    """Старая ссылка: перенаправляем на единую страницу статистики."""
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    uid = (request.query_params.get("user_id") or "").strip()
    r = (request.query_params.get("range") or "w").strip()
    if uid.isdigit():
        dest = f"/admin/wellness-journal/insights?mode=user&user_id={uid}&range={r}"
    else:
        dest = f"/admin/wellness-journal/insights?mode=platform&range={r}"
    return RedirectResponse(dest, status_code=302)


@router.post("/wellness-analytics/refresh-schemes")
async def admin_wellness_analytics_refresh_schemes_legacy(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from services.wellness_insights_service import refresh_scheme_effect_stats_simple

    await refresh_scheme_effect_stats_simple()
    return RedirectResponse(
        "/admin/wellness-journal/insights?mode=platform&refreshed=1", status_code=303
    )


# ─── NeuroFungi AI: пожелания к платформе ─────────────────────────────────────


@router.get("/platform-ai-feedback", response_class=HTMLResponse)
async def admin_platform_ai_feedback_page(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from services.platform_ai_feedback import list_platform_ai_feedback

    rows = await list_platform_ai_feedback(400)
    return templates.TemplateResponse(
        "dashboard/admin_platform_ai_feedback.html",
        {"request": request, "rows": rows},
    )


@router.post("/platform-ai-feedback/reply")
async def admin_platform_ai_feedback_reply(
    request: Request,
    feedback_id: int = Form(...),
    reply_text: str = Form(...),
    send_dm: Optional[str] = Form(None),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    from services.platform_ai_feedback import deliver_admin_reply_as_neurofungi_dm, set_admin_reply

    row = await database.fetch_one(
        platform_ai_feedback.select().where(platform_ai_feedback.c.id == int(feedback_id))
    )
    if not row:
        return RedirectResponse("/admin/platform-ai-feedback?err=1", status_code=303)
    ok = await set_admin_reply(int(feedback_id), reply_text)
    if not ok:
        return RedirectResponse("/admin/platform-ai-feedback?err=1", status_code=303)
    if (send_dm or "").strip().lower() in ("1", "true", "on", "yes"):
        await deliver_admin_reply_as_neurofungi_dm(int(row["user_id"]), reply_text)
    return RedirectResponse("/admin/platform-ai-feedback?saved=1", status_code=303)


# ─── AI в сообществе (посты, комментарии, подписки) ───────────────────────────


def _truthy_form(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("1", "true", "on", "yes")


@router.get("/ai-community-bot", response_class=HTMLResponse)
async def admin_ai_community_bot_page(request: Request):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    await apply_ai_community_bot_schema_if_needed()
    row = await load_bot_settings_row()
    cfg = dict(row) if row else {}
    uid = int(cfg["user_id"]) if cfg.get("user_id") else None
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    counts: dict[str, int] = {}
    if uid:
        counts["posts"] = int(
            await database.fetch_val(
                sqlalchemy.text(
                    "SELECT COUNT(*) FROM community_posts WHERE user_id = :u AND created_at >= :s"
                ),
                {"u": uid, "s": start},
            )
            or 0
        )
        counts["comments"] = int(
            await database.fetch_val(
                sqlalchemy.text(
                    "SELECT COUNT(*) FROM community_comments WHERE user_id = :u AND created_at >= :s"
                ),
                {"u": uid, "s": start},
            )
            or 0
        )
        counts["follows"] = int(
            await database.fetch_val(
                sqlalchemy.text(
                    "SELECT COUNT(*) FROM community_follows WHERE follower_id = :u AND created_at >= :s"
                ),
                {"u": uid, "s": start},
            )
            or 0
        )
        counts["replies_own"] = int(
            await database.fetch_val(
                sqlalchemy.text(
                    """
                    SELECT COUNT(*) FROM community_comments c
                    JOIN community_posts p ON p.id = c.post_id
                    WHERE c.user_id = :u AND p.user_id = :u AND c.created_at >= :s
                    """
                ),
                {"u": uid, "s": start},
            )
            or 0
        )
    urow = await database.fetch_one(users.select().where(users.c.id == uid)) if uid else None
    perms = await get_user_permissions(admin)
    return templates.TemplateResponse(
        "dashboard/admin_ai_community_bot.html",
        {
            "request": request,
            "user": admin,
            "nav": ADMIN_NAV,
            "user_permissions": perms,
            "cfg": cfg,
            "counts": counts,
            "uid": uid,
            "urow": urow,
        },
    )


@router.post("/ai-community-bot/master")
async def admin_ai_community_bot_save_master(request: Request, master_enabled: str = Form("0")):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    await apply_ai_community_bot_schema_if_needed()
    await database.execute(
        ai_community_bot_settings.update()
        .where(ai_community_bot_settings.c.id == 1)
        .values(master_enabled=_truthy_form(master_enabled), updated_at=datetime.utcnow())
    )
    return RedirectResponse("/admin/ai-community-bot?saved=master", status_code=303)


@router.post("/ai-community-bot/flags")
async def admin_ai_community_bot_save_flags(
    request: Request,
    allow_posts: str = Form("0"),
    allow_comments: str = Form("0"),
    allow_follow: str = Form("0"),
    allow_unfollow: str = Form("0"),
    allow_reply_to_comments: str = Form("0"),
    allow_profile_thoughts: str = Form("0"),
    allow_photos: str = Form("0"),
    allow_story_posts: str = Form("0"),
    allow_bug_reports: str = Form("0"),
    allow_telegram_channel: str = Form("0"),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    await apply_ai_community_bot_schema_if_needed()
    await database.execute(
        ai_community_bot_settings.update()
        .where(ai_community_bot_settings.c.id == 1)
        .values(
            allow_posts=_truthy_form(allow_posts),
            allow_comments=_truthy_form(allow_comments),
            allow_follow=_truthy_form(allow_follow),
            allow_unfollow=_truthy_form(allow_unfollow),
            allow_reply_to_comments=_truthy_form(allow_reply_to_comments),
            allow_profile_thoughts=_truthy_form(allow_profile_thoughts),
            allow_photos=_truthy_form(allow_photos),
            allow_story_posts=_truthy_form(allow_story_posts),
            allow_bug_reports=_truthy_form(allow_bug_reports),
            allow_telegram_channel=_truthy_form(allow_telegram_channel),
            updated_at=datetime.utcnow(),
        )
    )
    return RedirectResponse("/admin/ai-community-bot?saved=flags", status_code=303)


@router.post("/ai-community-bot/limits")
async def admin_ai_community_bot_save_limits(
    request: Request,
    limit_posts_per_day: int = Form(5),
    limit_comments_per_day: int = Form(30),
    limit_follows_per_day: int = Form(15),
    limit_unfollows_per_day: int = Form(10),
    limit_thoughts_per_day: int = Form(15),
    limit_reply_comments_per_day: int = Form(25),
):
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    await apply_ai_community_bot_schema_if_needed()
    await database.execute(
        ai_community_bot_settings.update()
        .where(ai_community_bot_settings.c.id == 1)
        .values(
            limit_posts_per_day=max(0, min(50, int(limit_posts_per_day or 0))),
            limit_comments_per_day=max(0, min(200, int(limit_comments_per_day or 0))),
            limit_follows_per_day=max(0, min(100, int(limit_follows_per_day or 0))),
            limit_unfollows_per_day=max(0, min(100, int(limit_unfollows_per_day or 0))),
            limit_thoughts_per_day=max(0, min(100, int(limit_thoughts_per_day or 0))),
            limit_reply_comments_per_day=max(0, min(200, int(limit_reply_comments_per_day or 0))),
            updated_at=datetime.utcnow(),
        )
    )
    return RedirectResponse("/admin/ai-community-bot?saved=limits", status_code=303)


@router.post("/ai-community-bot/ensure-user")
async def admin_ai_community_bot_ensure_user(request: Request):
    """Принудительно создать или привязать пользователя NeuroFungi AI (если не сработал старт)."""
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    try:
        from services.ai_community_bot import ensure_ai_community_bot_user

        uid = await ensure_ai_community_bot_user()
        if uid:
            return RedirectResponse(f"/admin/ai-community-bot?ensured={int(uid)}", status_code=303)
    except Exception as e:
        logger.warning("admin_ai_community_bot_ensure_user: %s", e, exc_info=True)
    return RedirectResponse("/admin/ai-community-bot?ensure_err=1", status_code=303)


@router.post("/ai-community-bot/bind-user")
async def admin_ai_community_bot_bind_user(request: Request, bind_user_id: str = Form("")):
    """Ручная привязка: существующий users.id записывается в ai_community_bot_settings."""
    admin = await require_permission(request, "can_users")
    if not admin:
        return RedirectResponse("/login")
    raw = (bind_user_id or "").strip()
    if not raw:
        return RedirectResponse("/admin/ai-community-bot?bind_err=empty", status_code=303)
    try:
        uid = int(raw)
    except ValueError:
        return RedirectResponse("/admin/ai-community-bot?bind_err=nan", status_code=303)
    ok, err = await bind_ai_community_bot_to_user_id(uid)
    if ok:
        return RedirectResponse(f"/admin/ai-community-bot?bound={uid}", status_code=303)
    from urllib.parse import quote

    return RedirectResponse(
        "/admin/ai-community-bot?bind_err=1&bind_msg=" + quote(err[:500], safe=""),
        status_code=303,
    )


from web.routes import admin_payment as _admin_payment_router

router.include_router(_admin_payment_router.router)
