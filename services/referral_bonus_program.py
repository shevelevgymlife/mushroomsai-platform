"""Флаги программы использования реф. бонусов внутри платформы (site_settings JSON)."""
from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa

from db.database import database

logger = logging.getLogger(__name__)

SETTINGS_KEY = "referral_bonus_program"

DEFAULT_FLAGS: dict[str, Any] = {
    "user_transfer_enabled": True,
    "user_pay_subscription_enabled": True,
    "user_auto_renew_enabled": True,
    "admin_grant_enabled": True,
    "admin_transfer_enabled": True,
    "admin_pay_subscription_enabled": True,
    "min_transfer_rub": 10,
}


def _normalize(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULT_FLAGS)
    if not raw or not isinstance(raw, dict):
        return out
    for k in DEFAULT_FLAGS:
        if k not in raw:
            continue
        if k == "min_transfer_rub":
            try:
                out[k] = max(1, min(1_000_000, int(float(raw[k]))))
            except (TypeError, ValueError):
                pass
        else:
            out[k] = bool(raw[k])
    return out


async def get_referral_bonus_program_flags() -> dict[str, Any]:
    try:
        row = await database.fetch_one(
            sa.text("SELECT value FROM site_settings WHERE key = :k"),
            {"k": SETTINGS_KEY},
        )
        if row and row.get("value"):
            return _normalize(json.loads(row["value"]))
    except Exception:
        logger.debug("get_referral_bonus_program_flags failed", exc_info=True)
    return _normalize(None)


async def set_referral_bonus_program_flags(flags: dict[str, Any]) -> None:
    payload = json.dumps(_normalize(flags), ensure_ascii=False)
    await database.execute(
        sa.text(
            """
            INSERT INTO site_settings (key, value, updated_at)
            VALUES (:k, :v, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """
        ),
        {"k": SETTINGS_KEY, "v": payload},
    )
