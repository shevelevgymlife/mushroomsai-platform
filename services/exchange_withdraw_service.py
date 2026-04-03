"""Вывод токена биржи (Shevelev) на сохранённый адрес Decimal Wallet: заявки, удержание баланса."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text

from config import exchange_token_display_name
from db.database import database, get_engine
from services.internal_exchange_service import ExchangeError, _d, _q_token


def _xtok() -> str:
    return exchange_token_display_name()

logger = logging.getLogger(__name__)

RE_DECIMAL_EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
SETTINGS_MIN_WITHDRAW = "internal_exchange_min_withdraw_nfi"


def normalize_decimal_wallet_address(raw: str) -> str:
    s = (raw or "").strip()
    if not RE_DECIMAL_EVM.match(s):
        raise ExchangeError("bad_address", "Адрес Decimal: формат 0x и 40 шестнадцатеричных символов")
    return s


def mask_address(addr: str) -> str:
    a = (addr or "").strip()
    if len(a) < 12:
        return "—"
    return f"{a[:6]}…{a[-4:]}"


def _min_withdraw_from_conn(conn) -> Decimal:
    row = conn.execute(
        text("SELECT value FROM site_settings WHERE key = :k"),
        {"k": SETTINGS_MIN_WITHDRAW},
    ).fetchone()
    if not row or row[0] is None or str(row[0]).strip() == "":
        return Decimal("0.00000001")
    try:
        m = _q_token(_d(row[0]))
        return m if m > 0 else Decimal("0.00000001")
    except Exception:
        return Decimal("0.00000001")


def save_user_decimal_wallet_sync(user_id: int, address_raw: str) -> str:
    addr = normalize_decimal_wallet_address(address_raw)

    def _run():
        with get_engine().begin() as conn:
            u = conn.execute(
                text("SELECT id FROM users WHERE id = :id FOR UPDATE"),
                {"id": int(user_id)},
            ).fetchone()
            if not u:
                raise ExchangeError("user_not_found", "Пользователь не найден")
            conn.execute(
                text("UPDATE users SET decimal_nfi_wallet_address = :a WHERE id = :id"),
                {"a": addr, "id": int(user_id)},
            )
        return addr

    return _run()


def request_nfi_withdrawal_sync(user_id: int, amount_raw: Decimal) -> dict[str, Any]:
    amt = _q_token(_d(amount_raw))
    if amt <= 0:
        raise ExchangeError("invalid_amount", "Укажите сумму больше нуля")

    def _run():
        with get_engine().begin() as conn:
            min_w = _min_withdraw_from_conn(conn)
            if amt < min_w:
                raise ExchangeError(
                    "below_minimum",
                    f"Минимальная сумма вывода {float(min_w)} {_xtok()}",
                )
            urow = conn.execute(
                text(
                    """
                    SELECT id, token_balance, decimal_nfi_wallet_address
                    FROM users WHERE id = :id FOR UPDATE
                    """
                ),
                {"id": int(user_id)},
            ).fetchone()
            if not urow:
                raise ExchangeError("user_not_found", "Пользователь не найден")
            to_addr = (urow[2] or "").strip()
            if not to_addr or not RE_DECIMAL_EVM.match(to_addr):
                raise ExchangeError(
                    "no_wallet",
                    "Сначала сохраните адрес Decimal Wallet в разделе ниже",
                )
            tb = _q_token(_d(urow[1]))
            if tb < amt:
                raise ExchangeError("insufficient_token", f"Недостаточно {_xtok()} на балансе")
            new_tb = _q_token(tb - amt)
            conn.execute(
                text("UPDATE users SET token_balance = :tb WHERE id = :id"),
                {"tb": float(new_tb), "id": int(user_id)},
            )
            rid = conn.execute(
                text(
                    """
                    INSERT INTO token_withdraw_requests (user_id, amount_token, to_address, status)
                    VALUES (:uid, :amt, :addr, 'pending')
                    RETURNING id
                    """
                ),
                {"uid": int(user_id), "amt": float(amt), "addr": to_addr},
            ).scalar()
            return {
                "request_id": int(rid),
                "amount_token": float(amt),
                "to_address_masked": mask_address(to_addr),
                "token_balance_after": float(new_tb),
            }

    return _run()


def admin_reject_withdrawal_sync(request_id: int, admin_user_id: int, note: str | None) -> None:
    note_s = (note or "").strip()[:2000]

    def _run():
        with get_engine().begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, user_id, amount_token, status
                    FROM token_withdraw_requests WHERE id = :id FOR UPDATE
                    """
                ),
                {"id": int(request_id)},
            ).fetchone()
            if not row:
                raise ExchangeError("not_found", "Заявка не найдена")
            if (row[3] or "").lower() != "pending":
                raise ExchangeError("bad_status", "Заявка уже обработана")
            uid = int(row[1])
            amt = _q_token(_d(row[2]))
            conn.execute(
                text("SELECT id FROM users WHERE id = :id FOR UPDATE"),
                {"id": uid},
            ).fetchone()
            cur_tb = conn.execute(
                text("SELECT token_balance FROM users WHERE id = :id"),
                {"id": uid},
            ).fetchone()
            tb = _q_token(_d(cur_tb[0] if cur_tb else 0))
            new_tb = _q_token(tb + amt)
            conn.execute(
                text("UPDATE users SET token_balance = :tb WHERE id = :id"),
                {"tb": float(new_tb), "id": uid},
            )
            conn.execute(
                text(
                    """
                    UPDATE token_withdraw_requests
                    SET status = 'rejected', admin_note = :n,
                        processed_at = NOW(), processed_by_admin_id = :aid
                    WHERE id = :id
                    """
                ),
                {"n": note_s or None, "aid": int(admin_user_id), "id": int(request_id)},
            )

    return _run()


def admin_complete_withdrawal_sync(
    request_id: int, admin_user_id: int, tx_hash: str
) -> None:
    h = (tx_hash or "").strip()
    if len(h) < 8:
        raise ExchangeError("bad_tx", "Укажите хеш транзакции (минимум 8 символов)")
    h = h[:128]

    def _run():
        with get_engine().begin() as conn:
            row = conn.execute(
                text("SELECT id, status FROM token_withdraw_requests WHERE id = :id FOR UPDATE"),
                {"id": int(request_id)},
            ).fetchone()
            if not row:
                raise ExchangeError("not_found", "Заявка не найдена")
            if (row[1] or "").lower() != "pending":
                raise ExchangeError("bad_status", "Заявка уже обработана")
            conn.execute(
                text(
                    """
                    UPDATE token_withdraw_requests
                    SET status = 'completed', tx_hash = :h,
                        processed_at = NOW(), processed_by_admin_id = :aid
                    WHERE id = :id
                    """
                ),
                {"h": h, "aid": int(admin_user_id), "id": int(request_id)},
            )

    return _run()


async def fetch_user_wallet_row(user_id: int) -> dict[str, Any]:
    row = await database.fetch_one(
        sa.text(
            "SELECT decimal_nfi_wallet_address FROM users WHERE id = :id",
        ),
        {"id": int(user_id)},
    )
    addr = (row["decimal_nfi_wallet_address"] or "").strip() if row else ""
    return {
        "saved": bool(addr and RE_DECIMAL_EVM.match(addr)),
        "masked": mask_address(addr) if addr else "",
    }


async def fetch_user_withdrawals(user_id: int, limit: int = 40) -> list[dict]:
    lim = max(1, min(100, int(limit)))
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT id, amount_token, to_address, status, tx_hash, created_at, processed_at, admin_note
            FROM token_withdraw_requests
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
                "id": r["id"],
                "amount_token": float(_d(r["amount_token"])),
                "to_masked": mask_address(r["to_address"] or ""),
                "status": r["status"],
                "tx_hash": r["tx_hash"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "processed_at": r["processed_at"].isoformat() if r.get("processed_at") else None,
                "admin_note": r["admin_note"],
            }
        )
    return out


async def list_pending_withdrawals_admin(limit: int = 80) -> list[dict]:
    lim = max(1, min(200, int(limit)))
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT w.id, w.user_id, w.amount_token, w.to_address, w.status, w.created_at,
                   u.name, u.email, u.tg_id
            FROM token_withdraw_requests w
            JOIN users u ON u.id = w.user_id
            WHERE w.status = 'pending'
            ORDER BY w.created_at ASC
            LIMIT :lim
            """
        ),
        {"lim": lim},
    )
    out = []
    for r in rows or []:
        out.append(
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "amount_token": float(_d(r["amount_token"])),
                "to_address": r["to_address"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "user_name": r.get("name"),
                "user_email": r.get("email"),
                "tg_id": r.get("tg_id"),
            }
        )
    return out


async def notify_admin_new_nfi_withdrawal(req: dict) -> None:
    try:
        from services.task_notify import notify_status

        details = json.dumps(req, ensure_ascii=False)
        await notify_status(
            stage="task_done",
            summary=(
                f"🔔 Биржа: заявка на вывод #{req.get('request_id')} · "
                f"{req.get('amount_token')} {_xtok()}"
            ),
            details=details[:3500],
            include_email=False,
        )
    except Exception:
        logger.debug("notify_admin_new_nfi_withdrawal failed", exc_info=True)


async def save_user_decimal_wallet(user_id: int, address: str) -> str:
    return await asyncio.to_thread(save_user_decimal_wallet_sync, user_id, address)


async def request_nfi_withdrawal(user_id: int, amount: float) -> dict[str, Any]:
    return await asyncio.to_thread(request_nfi_withdrawal_sync, user_id, _d(amount))


async def admin_reject_withdrawal(request_id: int, admin_user_id: int, note: str | None) -> None:
    await asyncio.to_thread(admin_reject_withdrawal_sync, request_id, admin_user_id, note)


async def admin_complete_withdrawal(request_id: int, admin_user_id: int, tx_hash: str) -> None:
    await asyncio.to_thread(admin_complete_withdrawal_sync, request_id, admin_user_id, tx_hash)
