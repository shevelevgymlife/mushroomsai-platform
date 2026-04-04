"""Флаг включения внутренней биржи (site_settings)."""
from __future__ import annotations

import logging

import sqlalchemy as sa

from db.database import database

logger = logging.getLogger(__name__)

SETTINGS_ENABLED_KEY = "internal_exchange_enabled"


async def is_internal_exchange_enabled() -> bool:
    """По умолчанию включено; выключение: false/0/off в site_settings."""
    try:
        row = await database.fetch_one(
            sa.text("SELECT value FROM site_settings WHERE key = :k"),
            {"k": SETTINGS_ENABLED_KEY},
        )
        if not row or row.get("value") is None:
            return True
        raw = str(row.get("value") or "").strip().lower()
        if raw in ("", "1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return True
    except Exception:
        logger.debug("is_internal_exchange_enabled read failed", exc_info=True)
        return True


async def set_internal_exchange_enabled(enabled: bool) -> None:
    from services.internal_exchange_service import upsert_site_setting

    await upsert_site_setting(SETTINGS_ENABLED_KEY, "true" if enabled else "false")
    try:
        import main as _main

        if hasattr(_main, "invalidate_global_settings_cache"):
            _main.invalidate_global_settings_cache()
    except Exception:
        pass
