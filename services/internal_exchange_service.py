"""Внутренняя биржа: бонусы ↔ токен NFI, пул ликвидности, комиссия 2%, лимит 10% пула."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text

from db.database import database, get_engine

logger = logging.getLogger(__name__)

FEE_MULT = Decimal("0.98")
MAX_POOL_SHARE = Decimal("0.1")
Q_BONUS = Decimal("0.01")
Q_TOKEN = Decimal("0.00000001")

SETTINGS_INCOME_FRAC = "internal_exchange_income_fraction"
SETTINGS_AUTO_GROWTH = "internal_exchange_auto_growth"
SETTINGS_GROWTH_TOKEN = "internal_exchange_growth_token"
SETTINGS_GROWTH_BONUS = "internal_exchange_growth_bonus"
SETTINGS_LAST_UCOUNT = "internal_exchange_last_user_count"
SETTINGS_LOW_ALERT_TS = "internal_exchange_low_coverage_alert_ts"


class ExchangeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _d(x) -> Decimal:
    if x is None:
        return Decimal("0")
    return Decimal(str(x))


def _q_bonus(d: Decimal) -> Decimal:
    return d.quantize(Q_BONUS, rounding=ROUND_DOWN)


def _q_token(d: Decimal) -> Decimal:
    return d.quantize(Q_TOKEN, rounding=ROUND_DOWN)


async def _get_setting(key: str, default: str) -> str:
    try:
        row = await database.fetch_one(
            sa.text("SELECT value FROM site_settings WHERE key = :k"),
            {"k": key},
        )
        if row and row.get("value") is not None:
            return str(row.get("value")).strip()
    except Exception:
        logger.debug("internal_exchange get_setting %s failed", key, exc_info=True)
    return default


async def upsert_site_setting(key: str, value: str) -> None:
    await database.execute(
        sa.text(
            """
            INSERT INTO site_settings (key, value, updated_at)
            VALUES (:k, :v, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """
        ),
        {"k": key, "v": value},
    )


async def get_income_fraction() -> Decimal:
    raw = await _get_setting(SETTINGS_INCOME_FRAC, "0.1")
    try:
        f = Decimal(raw)
        if f < 0 or f > 1:
            return Decimal("0.1")
        return f
    except Exception:
        return Decimal("0.1")


def exchange_buy_sync(user_id: int, bonus_amount_raw: Decimal) -> dict[str, Any]:
    bonus_in = _q_bonus(_d(bonus_amount_raw))
    if bonus_in <= 0:
        raise ExchangeError("invalid_amount", "Сумма должна быть больше нуля")

    def _run():
        with get_engine().begin() as conn:
            prow = conn.execute(
                text("SELECT token_reserve, bonus_reserve FROM liquidity_pool WHERE id = 1 FOR UPDATE")
            ).fetchone()
            if not prow:
                raise ExchangeError("no_pool", "Пул не настроен")
            tr = _d(prow[0])
            br = _d(prow[1])
            if tr <= 0:
                raise ExchangeError("empty_pool", "token_reserve = 0")
            price = br / tr
            token_out = (bonus_in / price) * FEE_MULT
            token_out = _q_token(token_out)
            if token_out <= 0:
                raise ExchangeError("dust", "Слишком малая сумма")
            if token_out > tr * MAX_POOL_SHARE:
                raise ExchangeError("too_big_trade", "Не больше 10% пула токенов за сделку")
            if token_out > tr:
                raise ExchangeError("liquidity", "Недостаточно токенов в пуле")

            urow = conn.execute(
                text(
                    "SELECT id, referral_balance, token_balance FROM users WHERE id = :id FOR UPDATE"
                ),
                {"id": int(user_id)},
            ).fetchone()
            if not urow:
                raise ExchangeError("user_not_found", "Пользователь не найден")
            rb = _q_bonus(_d(urow[1]))
            tb = _q_token(_d(urow[2]))
            if rb < bonus_in:
                raise ExchangeError("insufficient_bonus", "Недостаточно бонусов")

            new_rb = _q_bonus(rb - bonus_in)
            new_tb = _q_token(tb + token_out)
            new_tr = _q_token(tr - token_out)
            new_br = _q_bonus(br + bonus_in)

            conn.execute(
                text(
                    "UPDATE users SET referral_balance = :rb, token_balance = :tb WHERE id = :id"
                ),
                {"rb": float(new_rb), "tb": float(new_tb), "id": int(user_id)},
            )
            conn.execute(
                text(
                    "UPDATE liquidity_pool SET token_reserve = :tr, bonus_reserve = :br, updated_at = NOW() WHERE id = 1"
                ),
                {"tr": float(new_tr), "br": float(new_br)},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO exchange_trades (user_id, type, amount_bonus, amount_token, price)
                    VALUES (:uid, 'buy', :ab, :at, :pr)
                    """
                ),
                {
                    "uid": int(user_id),
                    "ab": float(bonus_in),
                    "at": float(token_out),
                    "pr": float(price),
                },
            )
            return {
                "price": float(price),
                "token_out": float(token_out),
                "bonus_spent": float(bonus_in),
                "token_reserve": float(new_tr),
                "bonus_reserve": float(new_br),
            }

    return _run()


def exchange_sell_sync(user_id: int, token_amount_raw: Decimal) -> dict[str, Any]:
    token_in = _q_token(_d(token_amount_raw))
    if token_in <= 0:
        raise ExchangeError("invalid_amount", "Сумма должна быть больше нуля")

    def _run():
        with get_engine().begin() as conn:
            prow = conn.execute(
                text("SELECT token_reserve, bonus_reserve FROM liquidity_pool WHERE id = 1 FOR UPDATE")
            ).fetchone()
            if not prow:
                raise ExchangeError("no_pool", "Пул не настроен")
            tr = _d(prow[0])
            br = _d(prow[1])
            if tr <= 0:
                raise ExchangeError("empty_pool", "token_reserve = 0")
            price = br / tr
            bonus_out = (token_in * price) * FEE_MULT
            bonus_out = _q_bonus(bonus_out)
            if bonus_out <= 0:
                raise ExchangeError("dust", "Слишком малая сумма")
            if bonus_out > br * MAX_POOL_SHARE:
                raise ExchangeError("too_big_trade", "Не больше 10% пула бонусов за сделку")
            if bonus_out > br:
                raise ExchangeError("liquidity", "Недостаточно бонусов в пуле")

            urow = conn.execute(
                text(
                    "SELECT id, referral_balance, token_balance FROM users WHERE id = :id FOR UPDATE"
                ),
                {"id": int(user_id)},
            ).fetchone()
            if not urow:
                raise ExchangeError("user_not_found", "Пользователь не найден")
            rb = _q_bonus(_d(urow[1]))
            tb = _q_token(_d(urow[2]))
            if tb < token_in:
                raise ExchangeError("insufficient_token", "Недостаточно токенов")

            new_tb = _q_token(tb - token_in)
            new_rb = _q_bonus(rb + bonus_out)
            new_tr = _q_token(tr + token_in)
            new_br = _q_bonus(br - bonus_out)

            conn.execute(
                text(
                    "UPDATE users SET referral_balance = :rb, token_balance = :tb WHERE id = :id"
                ),
                {"rb": float(new_rb), "tb": float(new_tb), "id": int(user_id)},
            )
            conn.execute(
                text(
                    "UPDATE liquidity_pool SET token_reserve = :tr, bonus_reserve = :br, updated_at = NOW() WHERE id = 1"
                ),
                {"tr": float(new_tr), "br": float(new_br)},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO exchange_trades (user_id, type, amount_bonus, amount_token, price)
                    VALUES (:uid, 'sell', :ab, :at, :pr)
                    """
                ),
                {
                    "uid": int(user_id),
                    "ab": float(bonus_out),
                    "at": float(token_in),
                    "pr": float(price),
                },
            )
            return {
                "price": float(price),
                "bonus_out": float(bonus_out),
                "token_sold": float(token_in),
                "token_reserve": float(new_tr),
                "bonus_reserve": float(new_br),
            }

    return _run()


def add_liquidity_sync(token_add: Decimal, bonus_add: Decimal) -> dict[str, Any]:
    ta = _q_token(_d(token_add))
    ba = _q_bonus(_d(bonus_add))
    if ta < 0 or ba < 0:
        raise ExchangeError("invalid_amount", "Отрицательные значения недопустимы")
    if ta == 0 and ba == 0:
        raise ExchangeError("invalid_amount", "Укажите ненулевую ликвидность")

    def _run():
        with get_engine().begin() as conn:
            prow = conn.execute(
                text("SELECT token_reserve, bonus_reserve FROM liquidity_pool WHERE id = 1 FOR UPDATE")
            ).fetchone()
            if not prow:
                raise ExchangeError("no_pool", "Пул не настроен")
            tr = _q_token(_d(prow[0]) + ta)
            br = _q_bonus(_d(prow[1]) + ba)
            conn.execute(
                text(
                    "UPDATE liquidity_pool SET token_reserve = :tr, bonus_reserve = :br, updated_at = NOW() WHERE id = 1"
                ),
                {"tr": float(tr), "br": float(br)},
            )
            return {"token_reserve": float(tr), "bonus_reserve": float(br)}

    return _run()


def pool_credit_bonus_from_payment_sync(amount_rub: Decimal, fraction: Decimal) -> dict[str, Any] | None:
    if amount_rub <= 0 or fraction <= 0:
        return None
    add_b = _q_bonus(amount_rub * fraction)
    if add_b <= 0:
        return None

    def _run():
        with get_engine().begin() as conn:
            prow = conn.execute(
                text("SELECT token_reserve, bonus_reserve FROM liquidity_pool WHERE id = 1 FOR UPDATE")
            ).fetchone()
            if not prow:
                return None
            br = _q_bonus(_d(prow[1]) + add_b)
            tr = _q_token(_d(prow[0]))
            conn.execute(
                text(
                    "UPDATE liquidity_pool SET bonus_reserve = :br, updated_at = NOW() WHERE id = 1"
                ),
                {"br": float(br)},
            )
            return {"bonus_added": float(add_b), "bonus_reserve": float(br), "token_reserve": float(tr)}

    return _run()


async def fetch_pool_public() -> dict[str, float]:
    row = await database.fetch_one(
        sa.text("SELECT token_reserve, bonus_reserve FROM liquidity_pool WHERE id = 1")
    )
    if not row:
        return {"price": 0.0, "token_reserve": 0.0, "bonus_reserve": 0.0}
    tr = _d(row["token_reserve"])
    br = _d(row["bonus_reserve"])
    if tr <= 0:
        return {"price": 0.0, "token_reserve": float(tr), "bonus_reserve": float(br)}
    price = br / tr
    return {
        "price": float(price),
        "token_reserve": float(tr),
        "bonus_reserve": float(br),
    }


async def fetch_user_balances(user_id: int) -> dict[str, float]:
    row = await database.fetch_one(
        sa.text("SELECT referral_balance, token_balance FROM users WHERE id = :id"),
        {"id": int(user_id)},
    )
    if not row:
        return {"bonus": 0.0, "token": 0.0}
    return {
        "bonus": float(_q_bonus(_d(row["referral_balance"]))),
        "token": float(_q_token(_d(row["token_balance"]))),
    }


async def fetch_trade_history_user(user_id: int, limit: int = 100) -> list[dict]:
    lim = max(1, min(200, int(limit)))
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT type, amount_bonus, amount_token, price, created_at
            FROM exchange_trades
            WHERE user_id = :uid
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"uid": int(user_id), "lim": lim},
    )
    out = []
    for r in rows or []:
        out.append(
            {
                "type": r["type"],
                "amount_bonus": float(_d(r["amount_bonus"])),
                "amount_token": float(_d(r["amount_token"])),
                "price": float(_d(r["price"])),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
        )
    return out


async def fetch_price_chart_points(limit: int = 60) -> list[dict]:
    lim = max(10, min(120, int(limit)))
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT price, created_at FROM exchange_trades
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"lim": lim},
    )
    pts = []
    for r in reversed(rows or []):
        pts.append(
            {
                "price": float(_d(r["price"])),
                "t": r["created_at"].isoformat() if r.get("created_at") else None,
            }
        )
    return pts


async def admin_pool_snapshot() -> dict[str, Any]:
    pool = await fetch_pool_public()
    total_bonus_row = await database.fetch_one(
        sa.text("SELECT COALESCE(SUM(referral_balance), 0) AS s FROM users")
    )
    total_bonus = float(_d(total_bonus_row["s"] if total_bonus_row else 0))
    tr = pool["token_reserve"]
    coverage = (tr / total_bonus) if total_bonus > 1e-9 else None
    status = "UNKNOWN"
    if coverage is not None:
        if coverage < 0.2:
            status = "LOW"
        elif coverage > 0.5:
            status = "GOOD"
        else:
            status = "OK"
    ucount_row = await database.fetch_one(
        sa.text(
            "SELECT COUNT(*) AS c FROM users WHERE primary_user_id IS NULL OR primary_user_id = id"
        )
    )
    ucount = int(ucount_row["c"] if ucount_row else 0)
    return {
        **pool,
        "total_bonus_system": total_bonus,
        "coverage": coverage,
        "status": status,
        "primary_users_count": ucount,
    }


async def add_platform_income_to_pool(paid_price_rub: float) -> None:
    if paid_price_rub <= 0:
        return
    frac = await get_income_fraction()
    try:
        await asyncio.to_thread(
            pool_credit_bonus_from_payment_sync, _d(paid_price_rub), frac
        )
    except Exception:
        logger.exception("add_platform_income_to_pool failed")
    try:
        await maybe_notify_low_coverage()
    except Exception:
        logger.debug("maybe_notify_low_coverage after income failed", exc_info=True)


async def maybe_auto_liquidity_on_user_growth() -> dict[str, Any] | None:
    ag = (await _get_setting(SETTINGS_AUTO_GROWTH, "false")).lower() in ("1", "true", "yes", "on")
    if not ag:
        return None
    try:
        gt = _q_token(_d(await _get_setting(SETTINGS_GROWTH_TOKEN, "0")))
        gb = _q_bonus(_d(await _get_setting(SETTINGS_GROWTH_BONUS, "0")))
    except Exception:
        gt = Decimal("0")
        gb = Decimal("0")
    if gt <= 0 and gb <= 0:
        return None
    ucount_row = await database.fetch_one(
        sa.text(
            "SELECT COUNT(*) AS c FROM users WHERE primary_user_id IS NULL OR primary_user_id = id"
        )
    )
    cur = int(ucount_row["c"] if ucount_row else 0)
    try:
        last = int((await _get_setting(SETTINGS_LAST_UCOUNT, "0")) or "0")
    except ValueError:
        last = 0
    if last == 0 and cur > 0:
        await upsert_site_setting(SETTINGS_LAST_UCOUNT, str(cur))
        return None
    if cur <= last:
        return None
    await upsert_site_setting(SETTINGS_LAST_UCOUNT, str(cur))
    try:
        res = await asyncio.to_thread(add_liquidity_sync, gt, gb)
        return res
    except Exception as e:
        logger.exception("auto liquidity on growth failed: %s", e)
        return None


async def maybe_notify_low_coverage() -> None:
    snap = await admin_pool_snapshot()
    cov = snap.get("coverage")
    if cov is None or cov >= 0.2:
        return
    now = int(time.time())
    try:
        raw = await _get_setting(SETTINGS_LOW_ALERT_TS, "0")
        last = int(float(raw or 0))
    except ValueError:
        last = 0
    if now - last < 3600:
        return
    await upsert_site_setting(SETTINGS_LOW_ALERT_TS, str(now))
    try:
        from services.task_notify import notify_status

        details = json.dumps(
            {
                "coverage": round(cov, 6),
                "token_reserve": snap.get("token_reserve"),
                "bonus_reserve": snap.get("bonus_reserve"),
                "total_bonus": snap.get("total_bonus_system"),
            },
            ensure_ascii=False,
        )
        await notify_status(
            stage="task_done",
            summary=f"⚠️ Биржа: низкий coverage {cov:.4f} (нужна ликвидность)",
            details=details[:3500],
            include_email=False,
        )
    except Exception:
        logger.debug("maybe_notify_low_coverage tg failed", exc_info=True)


async def notify_user_exchange_trade(
    user_id: int, kind: str, summary_line: str
) -> None:
    row = await database.fetch_one(
        sa.text("SELECT tg_id, linked_tg_id FROM users WHERE id = :id"),
        {"id": int(user_id)},
    )
    if not row:
        return
    tg = row.get("linked_tg_id") or row.get("tg_id")
    if not tg:
        return
    try:
        from services.tg_notify import notify_user_telegram

        text_html = f"<b>Биржа</b>\n{summary_line}"
        await notify_user_telegram(int(tg), text_html, "HTML")
    except Exception:
        logger.debug("notify_user_exchange_trade failed uid=%s", user_id, exc_info=True)


async def exchange_buy(user_id: int, bonus_amount: float) -> dict[str, Any]:
    res = await asyncio.to_thread(exchange_buy_sync, user_id, _d(bonus_amount))
    await maybe_notify_low_coverage()
    return res


async def exchange_sell(user_id: int, token_amount: float) -> dict[str, Any]:
    res = await asyncio.to_thread(exchange_sell_sync, user_id, _d(token_amount))
    await maybe_notify_low_coverage()
    return res


async def exchange_add_liquidity_admin(token_add: float, bonus_add: float) -> dict[str, Any]:
    return await asyncio.to_thread(
        add_liquidity_sync, _d(token_add), _d(bonus_add)
    )
