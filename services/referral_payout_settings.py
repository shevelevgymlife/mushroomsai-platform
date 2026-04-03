"""Порог и окно дат вывода реф. баланса: site_settings с fallback на config."""
from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from config import settings
from db.database import database

logger = logging.getLogger(__name__)

KEY_MIN = "referral_min_withdrawal_rub"
KEY_FROM = "referral_wd_moscow_day_from"
KEY_TO = "referral_wd_moscow_day_to"


def _clamp_day(v: Any, default: int) -> int:
    try:
        d = int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default
    return max(1, min(31, d))


def _clamp_min_rub(v: Any, default: int) -> int:
    try:
        n = int(float(str(v).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return default
    return max(1, min(10_000_000, n))


async def _get_setting(key: str) -> str | None:
    try:
        row = await database.fetch_one(
            sa.text("SELECT value FROM site_settings WHERE key = :k"),
            {"k": key},
        )
        if row and row.get("value") is not None:
            s = str(row["value"]).strip()
            return s if s != "" else None
    except Exception:
        logger.debug("referral_payout_settings read %s failed", key, exc_info=True)
    return None


async def get_referral_min_withdrawal_rub() -> int:
    raw = await _get_setting(KEY_MIN)
    if raw is not None:
        return _clamp_min_rub(raw, int(getattr(settings, "REFERRAL_MIN_WITHDRAWAL_RUB", 5000) or 5000))
    return int(getattr(settings, "REFERRAL_MIN_WITHDRAWAL_RUB", 5000) or 5000)


async def get_referral_wd_moscow_days() -> tuple[int, int]:
    raw_lo = await _get_setting(KEY_FROM)
    raw_hi = await _get_setting(KEY_TO)
    def_lo = int(getattr(settings, "REFERRAL_WITHDRAW_MOSCOW_DAY_FROM", 1) or 1)
    def_hi = int(getattr(settings, "REFERRAL_WITHDRAW_MOSCOW_DAY_TO", 5) or 5)
    lo = _clamp_day(raw_lo, def_lo) if raw_lo is not None else _clamp_day(def_lo, 1)
    hi = _clamp_day(raw_hi, def_hi) if raw_hi is not None else _clamp_day(def_hi, 5)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


async def set_referral_payout_rules(*, min_rub: int, moscow_day_from: int, moscow_day_to: int) -> None:
    mr = _clamp_min_rub(min_rub, 5000)
    lo = _clamp_day(moscow_day_from, 1)
    hi = _clamp_day(moscow_day_to, 5)
    if lo > hi:
        lo, hi = hi, lo
    for k, v in (
        (KEY_MIN, str(mr)),
        (KEY_FROM, str(lo)),
        (KEY_TO, str(hi)),
    ):
        await database.execute(
            sa.text(
                """
                INSERT INTO site_settings (key, value, updated_at)
                VALUES (:k, :v, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """
            ),
            {"k": k, "v": v},
        )


def moscow_calendar_day_in_window(day: int, day_from: int, day_to: int) -> bool:
    """Чистая функция для тестов: day — число месяца 1–31."""
    d = int(day)
    lo = int(day_from)
    hi = int(day_to)
    if lo > hi:
        lo, hi = hi, lo
    return lo <= d <= hi
