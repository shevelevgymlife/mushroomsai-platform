"""Страница /exchange и API /api/exchange/* (тариф Старт+)."""
from __future__ import annotations

import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from auth.session import get_user_from_request, attach_subscription_effective
from db.database import database
from db.models import users
from services.internal_exchange_service import (
    ExchangeError,
    admin_pool_snapshot,
    exchange_add_liquidity_admin,
    exchange_buy,
    exchange_sell,
    fetch_pool_public,
    fetch_price_chart_points,
    fetch_trade_history_user,
    fetch_user_balances,
    maybe_auto_liquidity_on_user_growth,
    notify_user_exchange_trade,
)
from services.legal import legal_acceptance_redirect
from services.subscription_service import check_subscription
from web.routes.user import compute_visible_blocks
from web.templates_utils import Jinja2Templates
from auth.ui_prefs import attach_screen_rim_prefs

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="web/templates")

exchange_page_router = APIRouter(tags=["exchange"])
exchange_api_router = APIRouter(prefix="/api/exchange", tags=["exchange-api"])
withdraw_alias_router = APIRouter(tags=["exchange-withdraw-stub"])


def _eff_uid(user: dict) -> int:
    return int(user.get("primary_user_id") or user["id"])


async def _require_start_plus(request: Request) -> tuple[dict, int] | None:
    user = await get_user_from_request(request)
    if not user:
        return None
    uid = _eff_uid(user)
    plan = await check_subscription(uid)
    if plan == "free":
        return None
    return user, uid


@exchange_page_router.get("/exchange", response_class=HTMLResponse)
async def exchange_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login?next=/exchange")
    leg = await legal_acceptance_redirect(request, user)
    if leg:
        return leg
    uid = _eff_uid(user)
    plan = await check_subscription(uid)
    if plan == "free":
        nxt = "/exchange"
        from urllib.parse import quote

        return RedirectResponse(
            "/subscriptions?locked=1&next=" + quote(nxt, safe=""), status_code=302
        )
    display_user = user
    if user.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == uid))
        if primary:
            display_user = dict(primary)
            attach_screen_rim_prefs(display_user)
            await attach_subscription_effective(display_user)
    visible_block_keys = await compute_visible_blocks(uid, plan)
    pool = await fetch_pool_public()
    bal = await fetch_user_balances(uid)
    chart = await fetch_price_chart_points(72)
    return templates.TemplateResponse(
        "exchange.html",
        {
            "request": request,
            "user": display_user,
            "visible_block_keys": visible_block_keys,
            "exchange_pool_initial": pool,
            "exchange_bal_initial": bal,
            "exchange_chart_initial": chart,
        },
    )


@exchange_api_router.get("/price")
async def api_exchange_price(request: Request):
    auth = await _require_start_plus(request)
    if not auth:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    pool = await fetch_pool_public()
    return JSONResponse(pool)


class AmountBody(BaseModel):
    amount: float = Field(..., gt=0)


@exchange_api_router.post("/buy")
async def api_exchange_buy(request: Request, body: AmountBody):
    auth = await _require_start_plus(request)
    if not auth:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    _u, uid = auth
    try:
        res = await exchange_buy(uid, body.amount)
    except ExchangeError as e:
        return JSONResponse({"error": e.code, "message": e.message}, status_code=400)
    except Exception:
        logger.exception("exchange buy")
        return JSONResponse({"error": "server"}, status_code=500)
    await notify_user_exchange_trade(
        uid,
        "buy",
        f"Куплено ~{res.get('token_out')} NFI за {res.get('bonus_spent')} бонусов.",
    )
    return JSONResponse({"ok": True, **res})


@exchange_api_router.post("/sell")
async def api_exchange_sell(request: Request, body: AmountBody):
    auth = await _require_start_plus(request)
    if not auth:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    _u, uid = auth
    try:
        res = await exchange_sell(uid, body.amount)
    except ExchangeError as e:
        return JSONResponse({"error": e.code, "message": e.message}, status_code=400)
    except Exception:
        logger.exception("exchange sell")
        return JSONResponse({"error": "server"}, status_code=500)
    await notify_user_exchange_trade(
        uid,
        "sell",
        f"Продано {res.get('token_sold')} NFI, получено ~{res.get('bonus_out')} бонусов.",
    )
    return JSONResponse({"ok": True, **res})


@exchange_api_router.get("/history")
async def api_exchange_history(request: Request):
    auth = await _require_start_plus(request)
    if not auth:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    _u, uid = auth
    trades = await fetch_trade_history_user(uid, 100)
    chart = await fetch_price_chart_points(80)
    return JSONResponse({"trades": trades, "chart": chart})


@exchange_api_router.post("/withdraw")
async def api_exchange_withdraw_stub(request: Request):
    auth = await _require_start_plus(request)
    if not auth:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse({"status": "pending"})


@withdraw_alias_router.post("/api/withdraw")
async def api_withdraw_stub_alias(request: Request):
    """Заглушка по ТЗ; реальный вывод на Decimal — позже."""
    auth = await _require_start_plus(request)
    if not auth:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse({"status": "pending"})


class AddLiquidityBody(BaseModel):
    token: float = Field(0, ge=0)
    bonus: float = Field(0, ge=0)


def register_admin_exchange_routes(admin_router: APIRouter) -> None:
    from web.routes.admin import require_permission

    @admin_router.get("/liquidity", response_class=HTMLResponse)
    async def admin_liquidity_page(request: Request):
        admin = await require_permission(request, "can_payment")
        if not admin:
            return RedirectResponse("/admin", status_code=302)
        snap = await admin_pool_snapshot()
        await maybe_auto_liquidity_on_user_growth()
        snap2 = await admin_pool_snapshot()
        from services.internal_exchange_service import (
            SETTINGS_AUTO_GROWTH,
            SETTINGS_GROWTH_BONUS,
            SETTINGS_GROWTH_TOKEN,
        )
        import sqlalchemy as sa

        async def _gv(k: str, d: str) -> str:
            row = await database.fetch_one(
                sa.text("SELECT value FROM site_settings WHERE key = :k"), {"k": k}
            )
            return str(row["value"]).strip() if row and row.get("value") is not None else d

        auto_growth = (await _gv(SETTINGS_AUTO_GROWTH, "false")).lower() in (
            "1",
            "true",
            "yes",
        )
        growth_t = await _gv(SETTINGS_GROWTH_TOKEN, "0")
        growth_b = await _gv(SETTINGS_GROWTH_BONUS, "0")
        return templates.TemplateResponse(
            "dashboard/admin_liquidity.html",
            {
                "request": request,
                "user": admin,
                "snap": snap2,
                "snap_before_growth": snap,
                "auto_growth": auto_growth,
                "growth_token": growth_t,
                "growth_bonus": growth_b,
            },
        )

    @admin_router.post("/add-liquidity")
    async def admin_add_liquidity_json(request: Request):
        admin = await require_permission(request, "can_payment")
        if not admin:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            data = await request.json()
        except Exception:
            data = {}
        try:
            body = AddLiquidityBody(
                token=float(data.get("token") or 0),
                bonus=float(data.get("bonus") or 0),
            )
        except Exception:
            return JSONResponse({"error": "bad_body"}, status_code=400)
        try:
            res = await exchange_add_liquidity_admin(body.token, body.bonus)
        except ExchangeError as e:
            return JSONResponse({"error": e.code, "message": e.message}, status_code=400)
        except Exception:
            logger.exception("admin add liquidity")
            return JSONResponse({"error": "server"}, status_code=500)
        return JSONResponse({"ok": True, **res})

    @admin_router.post("/liquidity/settings")
    async def admin_liquidity_settings(request: Request):
        admin = await require_permission(request, "can_payment")
        if not admin:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            data = await request.json()
        except Exception:
            data = {}
        from services.internal_exchange_service import (
            SETTINGS_AUTO_GROWTH,
            SETTINGS_GROWTH_BONUS,
            SETTINGS_GROWTH_TOKEN,
            upsert_site_setting,
        )

        if "auto_growth" in data:
            v = data.get("auto_growth")
            on = str(v).lower() in ("1", "true", "yes", "on")
            await upsert_site_setting(SETTINGS_AUTO_GROWTH, "true" if on else "false")
        if "growth_token" in data:
            await upsert_site_setting(SETTINGS_GROWTH_TOKEN, str(data.get("growth_token") or "0"))
        if "growth_bonus" in data:
            await upsert_site_setting(SETTINGS_GROWTH_BONUS, str(data.get("growth_bonus") or "0"))
        return JSONResponse({"ok": True})
