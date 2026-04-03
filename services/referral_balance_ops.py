"""Операции с реферальным бонусным балансом (журнал, переводы, оплата подписки)."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text

from db.database import database, get_engine
from db.models import referral_balance_ledger, users

logger = logging.getLogger(__name__)


def _q2(x: Any) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fmt_money(d: Decimal) -> str:
    return f"{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


class BonusOpError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(self.message)


async def _notify_bonus_receipt(
    user_id: int,
    *,
    body_plain: str,
    telegram_html: str | None = None,
) -> None:
    from services.system_support_delivery import deliver_system_support_notification

    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not row:
        return
    notify_uid = int(row.get("primary_user_id") or user_id)
    try:
        await deliver_system_support_notification(
            recipient_user_id=notify_uid,
            body_plain=body_plain.strip(),
            telegram_html=telegram_html,
        )
    except Exception:
        logger.exception("bonus receipt notify failed uid=%s", user_id)


def _build_receipt_plain(
    *,
    ledger_id: int,
    kind_label: str,
    amount_delta: Decimal,
    balance_after: Decimal,
    detail: str = "",
    counterparty_label: str = "",
) -> str:
    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    sign = "+" if amount_delta >= 0 else ""
    lines = [
        "Внутренний чек · бонусный баланс реферальной программы",
        f"Дата: {ts}",
        f"Операция: {kind_label}",
        f"Изменение баланса: {sign}{_fmt_money(amount_delta)} ₽",
        f"Баланс после операции: {_fmt_money(balance_after)} ₽",
        f"Номер записи в журнале: {ledger_id}",
    ]
    if counterparty_label:
        lines.append(f"Контрагент: {counterparty_label}")
    if detail.strip():
        lines.append(f"Детали: {detail.strip()}")
    lines.append(
        "Бонусы не являются денежными средствами вне платформы; правила — в оферте и на странице реферальной программы."
    )
    return "\n".join(lines)


def _ledger_insert_sync(
    conn,
    *,
    user_id: int,
    amount_delta: Decimal,
    balance_after: Decimal,
    kind: str,
    detail_text: str | None = None,
    counterparty_user_id: int | None = None,
    correlation_id: str | None = None,
    admin_actor_id: int | None = None,
    plan_key: str | None = None,
) -> int:
    row = conn.execute(
        referral_balance_ledger.insert()
        .values(
            user_id=int(user_id),
            amount_delta=float(amount_delta),
            balance_after=float(balance_after),
            kind=(kind or "")[:48],
            detail_text=detail_text,
            counterparty_user_id=counterparty_user_id,
            correlation_id=correlation_id,
            admin_actor_id=admin_actor_id,
            plan_key=(plan_key or None) and (plan_key or "")[:24],
        )
        .returning(referral_balance_ledger.c.id)
    ).fetchone()
    if not row:
        raise RuntimeError("ledger insert returned no id")
    return int(row[0])


def _lock_users_ordered(conn, uid_a: int, uid_b: int) -> None:
    for uid in sorted({int(uid_a), int(uid_b)}):
        row = conn.execute(
            text("SELECT id FROM users WHERE id = :id FOR UPDATE"),
            {"id": uid},
        ).fetchone()
        if not row:
            raise BonusOpError("user_not_found", "Пользователь не найден")


def _get_balance_dec_sync(conn, user_id: int) -> Decimal:
    r = conn.execute(
        text("SELECT COALESCE(referral_balance, 0) AS b FROM users WHERE id = :id"),
        {"id": int(user_id)},
    ).fetchone()
    if not r:
        raise BonusOpError("user_not_found", "Пользователь не найден")
    return _q2(r[0])


def _set_balance_sync(conn, user_id: int, new_bal: Decimal) -> None:
    conn.execute(
        text("UPDATE users SET referral_balance = :b WHERE id = :id"),
        {"b": float(new_bal), "id": int(user_id)},
    )


def _transfer_balances_sync(
    from_id: int,
    to_id: int,
    amount: Decimal,
    *,
    kind_out: str,
    kind_in: str,
    detail_out: str,
    detail_in: str,
    admin_actor_id: int | None,
) -> tuple[int, int, Decimal, Decimal]:
    if int(from_id) == int(to_id):
        raise BonusOpError("self_transfer", "Нельзя перевести самому себе")
    amt = _q2(amount)
    if amt <= 0:
        raise BonusOpError("invalid_amount", "Сумма должна быть больше нуля")
    corr = str(uuid.uuid4())

    def _run():
        with get_engine().begin() as conn:
            _lock_users_ordered(conn, from_id, to_id)
            b_from = _get_balance_dec_sync(conn, from_id)
            b_to = _get_balance_dec_sync(conn, to_id)
            if b_from < amt:
                raise BonusOpError("insufficient_funds", "Недостаточно бонусов на балансе")
            na = b_from - amt
            nb = b_to + amt
            _set_balance_sync(conn, from_id, na)
            _set_balance_sync(conn, to_id, nb)
            lid_out = _ledger_insert_sync(
                conn,
                user_id=from_id,
                amount_delta=-amt,
                balance_after=na,
                kind=kind_out,
                detail_text=detail_out,
                counterparty_user_id=int(to_id),
                correlation_id=corr,
                admin_actor_id=admin_actor_id,
            )
            lid_in = _ledger_insert_sync(
                conn,
                user_id=to_id,
                amount_delta=amt,
                balance_after=nb,
                kind=kind_in,
                detail_text=detail_in,
                counterparty_user_id=int(from_id),
                correlation_id=corr,
                admin_actor_id=admin_actor_id,
            )
            return lid_out, lid_in, na, nb

    return _run()


def _single_adjust_sync(
    user_id: int,
    delta: Decimal,
    *,
    kind: str,
    detail_text: str | None = None,
    counterparty_user_id: int | None = None,
    correlation_id: str | None = None,
    admin_actor_id: int | None = None,
    plan_key: str | None = None,
    allow_negative_balance: bool = False,
) -> tuple[int, Decimal]:
    d = _q2(delta)
    if d == 0:
        raise BonusOpError("invalid_amount", "Сумма не может быть нулевой")

    def _run():
        with get_engine().begin() as conn:
            urow = conn.execute(
                text("SELECT id FROM users WHERE id = :id FOR UPDATE"),
                {"id": int(user_id)},
            ).fetchone()
            if not urow:
                raise BonusOpError("user_not_found", "Пользователь не найден")
            cur = _get_balance_dec_sync(conn, user_id)
            new_bal = cur + d
            if not allow_negative_balance and new_bal < Decimal("-0.001"):
                raise BonusOpError("insufficient_funds", "Недостаточно бонусов на балансе")
            new_bal = _q2(new_bal)
            _set_balance_sync(conn, user_id, new_bal)
            lid = _ledger_insert_sync(
                conn,
                user_id=user_id,
                amount_delta=d,
                balance_after=new_bal,
                kind=kind,
                detail_text=detail_text,
                counterparty_user_id=counterparty_user_id,
                correlation_id=correlation_id,
                admin_actor_id=admin_actor_id,
                plan_key=plan_key,
            )
            return lid, new_bal

    try:
        return _run()
    except BonusOpError:
        raise
    except Exception as e:
        logger.exception("single_adjust_sync failed uid=%s", user_id)
        raise BonusOpError("db_error", str(e)[:200]) from e


async def list_ledger_for_user(user_id: int, limit: int = 80) -> list[dict]:
    lim = max(1, min(500, int(limit)))
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT id, created_at, correlation_id, user_id, counterparty_user_id,
                   amount_delta, balance_after, kind, detail_text, admin_actor_id, plan_key
            FROM referral_balance_ledger
            WHERE user_id = :u
            ORDER BY id DESC
            LIMIT :lim
            """
        ),
        {"u": int(user_id), "lim": lim},
    )
    out = []
    for r in rows or []:
        out.append(
            {
                "id": r.get("id"),
                "created_at": r.get("created_at"),
                "correlation_id": r.get("correlation_id"),
                "amount_delta": float(r.get("amount_delta") or 0),
                "balance_after": float(r.get("balance_after") or 0) if r.get("balance_after") is not None else None,
                "kind": r.get("kind"),
                "detail_text": r.get("detail_text"),
                "counterparty_user_id": r.get("counterparty_user_id"),
                "plan_key": r.get("plan_key"),
            }
        )
    return out


async def list_ledger_recent_global(limit: int = 100) -> list[dict]:
    lim = max(1, min(300, int(limit)))
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT l.id, l.created_at, l.user_id, l.amount_delta, l.balance_after, l.kind, l.detail_text,
                   u.name AS user_name
            FROM referral_balance_ledger l
            LEFT JOIN users u ON u.id = l.user_id
            ORDER BY l.id DESC
            LIMIT :lim
            """
        ),
        {"lim": lim},
    )
    return [dict(r) for r in (rows or [])]


def plan_price_rub_for_bonus_payment(plan_key: str, meta: dict) -> Decimal:
    pk = (plan_key or "").strip().lower()
    if pk == "free":
        raise BonusOpError("invalid_plan", "Тариф бесплатный")
    if bool(meta.get("billing_period_unlimited")):
        raise BonusOpError("plan_not_eligible", "Для этого тарифа оплата бонусами не предусмотрена")
    pr = meta.get("price")
    try:
        p = _q2(pr)
    except Exception:
        raise BonusOpError("invalid_plan", "Некорректная цена тарифа") from None
    if p <= 0:
        raise BonusOpError("invalid_plan", "Некорректная цена тарифа")
    return p


async def user_transfer_bonuses(
    from_user_id: int,
    to_user_id: int,
    amount_rub: Any,
) -> None:
    from services.referral_bonus_program import get_referral_bonus_program_flags

    flags = await get_referral_bonus_program_flags()
    if not flags.get("user_transfer_enabled"):
        raise BonusOpError("feature_disabled", "Перевод бонусов временно отключён")
    min_tr = int(flags.get("min_transfer_rub") or 10)
    amt = _q2(amount_rub)
    if amt < _q2(min_tr):
        raise BonusOpError("below_minimum", f"Минимальная сумма перевода: {min_tr} ₽")

    fr = await database.fetch_one(users.select().where(users.c.id == int(from_user_id)))
    to = await database.fetch_one(users.select().where(users.c.id == int(to_user_id)))
    if not fr or not to:
        raise BonusOpError("user_not_found", "Пользователь не найден")
    if bool(fr.get("is_banned")) or bool(to.get("is_banned")):
        raise BonusOpError("user_blocked", "Операция недоступна")

    tname = (to.get("name") or "").strip() or f"id {to_user_id}"
    fname = (fr.get("name") or "").strip() or f"id {from_user_id}"

    def _run():
        return _transfer_balances_sync(
            int(from_user_id),
            int(to_user_id),
            amt,
            kind_out="user_transfer_out",
            kind_in="user_transfer_in",
            detail_out=f"Перевод пользователю {tname} (id {to_user_id})",
            detail_in=f"Перевод от {fname} (id {from_user_id})",
            admin_actor_id=None,
        )

    try:
        lid_out, lid_in, bal_from, bal_to = await asyncio.to_thread(_run)
    except BonusOpError:
        raise
    except Exception as e:
        logger.exception("user_transfer_bonuses failed")
        raise BonusOpError("db_error", str(e)[:200]) from e

    plain_out = _build_receipt_plain(
        ledger_id=lid_out,
        kind_label="Перевод бонусов другому пользователю (списание)",
        amount_delta=-amt,
        balance_after=bal_from,
        detail=f"Получатель: {tname}, id {to_user_id}",
    )
    await _notify_bonus_receipt(
        from_user_id,
        body_plain=plain_out,
        telegram_html=None,
    )
    # Получателю — отдельный чек (lid_in)
    plain_in = _build_receipt_plain(
        ledger_id=lid_in,
        kind_label="Перевод бонусов от другого пользователя (зачисление)",
        amount_delta=amt,
        balance_after=bal_to,
        detail=f"Отправитель: {fname}, id {from_user_id}",
    )
    await _notify_bonus_receipt(to_user_id, body_plain=plain_in)


async def admin_transfer_bonuses(
    from_user_id: int,
    to_user_id: int,
    amount_rub: Any,
    admin_actor_id: int,
) -> None:
    from services.referral_bonus_program import get_referral_bonus_program_flags

    flags = await get_referral_bonus_program_flags()
    if not flags.get("admin_transfer_enabled"):
        raise BonusOpError("feature_disabled", "Перевод администратором отключён")
    amt = _q2(amount_rub)
    if amt <= 0:
        raise BonusOpError("invalid_amount", "Сумма должна быть больше нуля")

    def _run():
        return _transfer_balances_sync(
            int(from_user_id),
            int(to_user_id),
            amt,
            kind_out="admin_transfer_out",
            kind_in="admin_transfer_in",
            detail_out=f"Перевод администратором → пользователь id {to_user_id}",
            detail_in=f"Зачисление администратором ← пользователь id {from_user_id}",
            admin_actor_id=int(admin_actor_id),
        )

    try:
        lid_out, lid_in, bal_from, bal_to = await asyncio.to_thread(_run)
    except BonusOpError:
        raise
    except Exception as e:
        logger.exception("admin_transfer_bonuses failed")
        raise BonusOpError("db_error", str(e)[:200]) from e

    adm = await database.fetch_one(users.select().where(users.c.id == int(admin_actor_id)))
    aname = (adm.get("name") or "").strip() if adm else ""
    note_adm = f"Операция администратора id {admin_actor_id}" + (f" ({aname})" if aname else "")

    plain_out = _build_receipt_plain(
        ledger_id=lid_out,
        kind_label="Списание бонусов (операция администратора)",
        amount_delta=-amt,
        balance_after=bal_from,
        detail=note_adm,
        counterparty_label=f"Пользователь id {to_user_id}",
    )
    await _notify_bonus_receipt(from_user_id, body_plain=plain_out)

    plain_in = _build_receipt_plain(
        ledger_id=lid_in,
        kind_label="Зачисление бонусов (операция администратора)",
        amount_delta=amt,
        balance_after=bal_to,
        detail=note_adm,
        counterparty_label=f"Пользователь id {from_user_id}",
    )
    await _notify_bonus_receipt(to_user_id, body_plain=plain_in)


async def admin_grant_bonuses(
    user_id: int,
    amount_rub: Any,
    admin_actor_id: int,
    note: str = "",
) -> None:
    from services.referral_bonus_program import get_referral_bonus_program_flags

    flags = await get_referral_bonus_program_flags()
    if not flags.get("admin_grant_enabled"):
        raise BonusOpError("feature_disabled", "Начисление администратором отключено")
    amt = _q2(amount_rub)
    if amt <= 0:
        raise BonusOpError("invalid_amount", "Сумма должна быть больше нуля")
    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not row:
        raise BonusOpError("user_not_found", "Пользователь не найден")

    det = (note or "").strip() or "Начисление администратором"
    try:
        lid, new_bal = await asyncio.to_thread(
            lambda: _single_adjust_sync(
                int(user_id),
                amt,
                kind="admin_grant",
                detail_text=det[:2000],
                admin_actor_id=int(admin_actor_id),
            )
        )
    except BonusOpError:
        raise
    except Exception as e:
        logger.exception("admin_grant_bonuses failed")
        raise BonusOpError("db_error", str(e)[:200]) from e

    plain = _build_receipt_plain(
        ledger_id=lid,
        kind_label="Начисление бонусов администратором",
        amount_delta=amt,
        balance_after=new_bal,
        detail=det,
    )
    await _notify_bonus_receipt(user_id, body_plain=plain)


async def pay_subscription_with_bonuses(
    user_id: int,
    plan_key: str,
    *,
    subscription_event_kind: str,
    ledger_kind: str,
    detail_template: str,
    skip_user_notify: bool = False,
    is_admin_initiated: bool = False,
) -> None:
    from services.referral_bonus_program import get_referral_bonus_program_flags
    from services.payment_plans_catalog import get_effective_plans
    from services.subscription_service import activate_subscription

    flags = await get_referral_bonus_program_flags()
    if is_admin_initiated:
        if not flags.get("admin_pay_subscription_enabled"):
            raise BonusOpError("feature_disabled", "Оплата подписки бонусами (админ) отключена")
    elif not flags.get("user_pay_subscription_enabled"):
        raise BonusOpError("feature_disabled", "Оплата подписки бонусами отключена")

    eff = await get_effective_plans()
    pk = (plan_key or "").strip().lower()
    if pk not in eff:
        raise BonusOpError("invalid_plan", "Неизвестный тариф")
    meta = eff[pk]
    price = plan_price_rub_for_bonus_payment(pk, meta)

    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not row or bool(row.get("is_banned")):
        raise BonusOpError("user_not_found", "Пользователь не найден или заблокирован")

    pname = str(meta.get("name") or pk)
    detail = detail_template.format(plan=pname, plan_key=pk, price=_fmt_money(price))

    try:
        lid, new_bal = await asyncio.to_thread(
            lambda: _single_adjust_sync(
                int(user_id),
                -price,
                kind=ledger_kind,
                detail_text=detail[:2000],
                plan_key=pk,
            )
        )
    except BonusOpError:
        raise
    except Exception as e:
        logger.exception("pay_subscription_with_bonuses debit failed")
        raise BonusOpError("db_error", str(e)[:200]) from e

    ok = await activate_subscription(
        int(user_id),
        pk,
        1,
        paid_price_rub=float(price),
        credit_referrer_bonus=False,
        skip_user_notify=skip_user_notify,
        referral_bonus_payment_channel="referral_balance_internal",
        subscription_event_kind=(subscription_event_kind or "activation")[:32],
    )
    if not ok:
        try:
            await asyncio.to_thread(
                lambda: _single_adjust_sync(
                    int(user_id),
                    price,
                    kind="bonus_pay_refund",
                    detail_text=f"Возврат: не удалось активировать тариф {pname}",
                    correlation_id=str(uuid.uuid4()),
                )
            )
        except Exception:
            logger.exception("bonus pay refund failed uid=%s", user_id)
        raise BonusOpError("activate_failed", "Не удалось активировать подписку; бонусы возвращены")

    plain = _build_receipt_plain(
        ledger_id=lid,
        kind_label="Оплата подписки с бонусного баланса",
        amount_delta=-price,
        balance_after=new_bal,
        detail=f"{detail} · списано {_fmt_money(price)} ₽",
    )
    await _notify_bonus_receipt(user_id, body_plain=plain)


async def user_pay_subscription_with_bonuses(user_id: int, plan_key: str) -> None:
    await pay_subscription_with_bonuses(
        user_id,
        plan_key,
        subscription_event_kind="bonus_pay",
        ledger_kind="user_subscription_pay",
        detail_template="Оплата подписки «{plan}» с бонусного баланса",
        is_admin_initiated=False,
    )


async def admin_pay_subscription_with_user_bonuses(
    target_user_id: int,
    plan_key: str,
    admin_actor_id: int,
) -> None:
    await pay_subscription_with_bonuses(
        int(target_user_id),
        plan_key,
        subscription_event_kind="bonus_pay_admin",
        ledger_kind="admin_subscription_pay",
        detail_template="Оплата подписки «{plan}» с бонуса пользователя (администратор id "
        + str(int(admin_actor_id))
        + ")",
        is_admin_initiated=True,
    )


async def try_renew_subscription_with_bonus_balance(user_id: int, row: Any) -> bool:
    """Продление текущего платного тарифа при истечении срока. Возвращает True, если продлено."""
    from services.referral_bonus_program import get_referral_bonus_program_flags

    flags = await get_referral_bonus_program_flags()
    if not flags.get("user_auto_renew_enabled"):
        return False
    if not bool(row.get("referral_bonus_auto_renew")):
        return False
    if not flags.get("user_pay_subscription_enabled"):
        return False

    stored = (row.get("subscription_plan") or "free").lower()
    if stored == "free":
        return False
    if bool(row.get("subscription_paid_lifetime")):
        return False
    if bool(row.get("subscription_admin_granted")):
        return False

    try:
        await pay_subscription_with_bonuses(
            int(user_id),
            stored,
            subscription_event_kind="bonus_renew",
            ledger_kind="user_subscription_auto_renew",
            detail_template="Автопродление подписки «{plan}» с бонусного баланса",
            skip_user_notify=False,
            is_admin_initiated=False,
        )
    except BonusOpError as e:
        logger.debug("bonus auto-renew skipped uid=%s: %s", user_id, e.code)
        return False
    except Exception:
        logger.exception("bonus auto-renew failed uid=%s", user_id)
        return False
    return True


async def set_user_bonus_auto_renew(user_id: int, enabled: bool) -> None:
    from services.referral_bonus_program import get_referral_bonus_program_flags

    flags = await get_referral_bonus_program_flags()
    if not flags.get("user_auto_renew_enabled"):
        raise BonusOpError("feature_disabled", "Автосписание с бонусов отключено на платформе")
    await database.execute(
        users.update()
        .where(users.c.id == int(user_id))
        .values(referral_bonus_auto_renew=bool(enabled))
    )
