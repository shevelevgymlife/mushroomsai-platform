"""
Политика префикса партнёрской ссылки магазина (platform_settings).
При включении: ссылка должна начинаться с заданной строки (по умолчанию deep-link Neurotrops).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from db.database import database
from db.models import platform_settings

logger = logging.getLogger(__name__)

REFERRAL_SHOP_LINK_POLICY_KEY = "referral_shop_link_policy"

# Префикс без реф-кода: дальше может быть любой start=…
DEFAULT_REQUIRED_PREFIX = "https://t.me/neurotrops_rus_bot?start="

DEFAULT_POLICY: dict[str, Any] = {
    "enforce_prefix": False,
    "required_prefix": DEFAULT_REQUIRED_PREFIX,
}


def _normalize_policy_dict(raw: dict[str, Any]) -> dict[str, Any]:
    enforce = bool(raw.get("enforce_prefix"))
    prefix = (raw.get("required_prefix") or DEFAULT_REQUIRED_PREFIX).strip()
    if len(prefix) > 512:
        prefix = prefix[:512]
    if prefix and not (
        prefix.lower().startswith("https://") or prefix.lower().startswith("http://")
    ):
        prefix = DEFAULT_REQUIRED_PREFIX
    if not prefix:
        prefix = DEFAULT_REQUIRED_PREFIX
    return {"enforce_prefix": enforce, "required_prefix": prefix}


async def get_referral_shop_link_policy() -> dict[str, Any]:
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == REFERRAL_SHOP_LINK_POLICY_KEY)
        )
        if row and (row.get("value") or "").strip():
            data = json.loads(row["value"])
            if isinstance(data, dict):
                return _normalize_policy_dict(data)
    except Exception:
        logger.debug("referral_shop_link_policy read failed", exc_info=True)
    return dict(DEFAULT_POLICY)


async def set_referral_shop_link_policy(enforce_prefix: bool, required_prefix: str) -> None:
    prefix = (required_prefix or "").strip()
    if not prefix:
        prefix = DEFAULT_REQUIRED_PREFIX
    if len(prefix) > 512:
        raise ValueError("Префикс не длиннее 512 символов")
    low = prefix.lower()
    if not (low.startswith("https://") or low.startswith("http://")):
        raise ValueError("Префикс должен начинаться с https:// или http://")
    payload = json.dumps(
        {"enforce_prefix": bool(enforce_prefix), "required_prefix": prefix},
        ensure_ascii=False,
    )
    row = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == REFERRAL_SHOP_LINK_POLICY_KEY)
    )
    if row:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == REFERRAL_SHOP_LINK_POLICY_KEY)
            .values(value=payload)
        )
    else:
        await database.execute(
            platform_settings.insert().values(key=REFERRAL_SHOP_LINK_POLICY_KEY, value=payload)
        )


async def assert_partner_shop_url_allowed(normalized_url: str) -> None:
    """Если проверка включена — URL должен начинаться с сохранённого префикса (регистр учитывается)."""
    pol = await get_referral_shop_link_policy()
    if not pol.get("enforce_prefix"):
        return
    prefix = (pol.get("required_prefix") or "").strip()
    if not prefix:
        return
    if not normalized_url.startswith(prefix):
        raise ValueError(
            "Ссылка магазина должна начинаться с:\n"
            f"{prefix}\n"
            "Магазин: каталог → меню → личный кабинет → «Моя ссылка» → копировать."
        )
