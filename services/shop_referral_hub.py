"""
Настройки витрины и внешних ссылок магазина для рефералов продавцов Макси (platform_settings).
Не меняет начисление реферальных бонусов — только отображение каталога, ссылок и подсказок AI.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from db.database import database
from db.models import platform_settings, users

logger = logging.getLogger(__name__)

SHOP_REFERRAL_HUB_KEY = "shop_referral_hub_v1"

DEFAULT_HUB: dict[str, Any] = {
    "exclusive_catalog": {"mode": "off", "seller_user_ids": []},
    "grace_days_after_maxi_end": 5,
    "single_link_ai_for_exclusive": True,
}


def _normalize_hub(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULT_HUB)
    ex = raw.get("exclusive_catalog")
    if isinstance(ex, dict):
        mode = (ex.get("mode") or "off").strip().lower()
        if mode not in ("off", "all_maxi_sellers", "selected"):
            mode = "off"
        ids: list[int] = []
        for x in ex.get("seller_user_ids") or []:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                pass
        out["exclusive_catalog"] = {"mode": mode, "seller_user_ids": sorted(set(ids))}
    try:
        gd = int(raw.get("grace_days_after_maxi_end") or 5)
    except (TypeError, ValueError):
        gd = 5
    out["grace_days_after_maxi_end"] = max(0, min(90, gd))
    out["single_link_ai_for_exclusive"] = bool(raw.get("single_link_ai_for_exclusive", True))
    return out


async def get_shop_referral_hub() -> dict[str, Any]:
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == SHOP_REFERRAL_HUB_KEY)
        )
        if row and (row.get("value") or "").strip():
            data = json.loads(row["value"])
            if isinstance(data, dict):
                return _normalize_hub(data)
    except Exception:
        logger.debug("shop_referral_hub read failed", exc_info=True)
    return dict(DEFAULT_HUB)


async def set_shop_referral_hub(payload: dict[str, Any]) -> None:
    normalized = _normalize_hub(payload)
    val = json.dumps(normalized, ensure_ascii=False)
    row = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == SHOP_REFERRAL_HUB_KEY)
    )
    if row:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == SHOP_REFERRAL_HUB_KEY)
            .values(value=val)
        )
    else:
        await database.execute(
            platform_settings.insert().values(key=SHOP_REFERRAL_HUB_KEY, value=val)
        )


async def schedule_maxi_perks_grace(user_id: int) -> None:
    """Вызывается при сбросе подписки с maxi → free: даём грейс на ссылки/витрину."""
    hub = await get_shop_referral_hub()
    days = int(hub.get("grace_days_after_maxi_end") or 5)
    if days <= 0:
        await database.execute(
            users.update().where(users.c.id == int(user_id)).values(maxi_perks_grace_until=None)
        )
        return
    until = datetime.utcnow() + timedelta(days=days)
    await database.execute(
        users.update().where(users.c.id == int(user_id)).values(maxi_perks_grace_until=until)
    )


async def clear_maxi_perks_grace(user_id: int) -> None:
    await database.execute(
        users.update().where(users.c.id == int(user_id)).values(maxi_perks_grace_until=None)
    )


async def referrer_shop_hub_active(referrer_id: int) -> bool:
    """Продавец-маркетплейс: активный Макси или грейс после него."""
    row = await database.fetch_one(users.select().where(users.c.id == int(referrer_id)))
    if not row or not bool(row.get("marketplace_seller")):
        return False
    gu = row.get("maxi_perks_grace_until")
    if gu and gu > datetime.utcnow():
        return True
    from services.subscription_service import check_subscription

    return (await check_subscription(int(referrer_id))) == "maxi"


async def maxi_marketplace_can_bind_any_shop_url(user_id: int) -> bool:
    """Макси + marketplace_seller и (подписка maxi или грейс) — можно сохранять ссылку без префикса Neurotrops."""
    return await referrer_shop_hub_active(int(user_id))


def _exclusive_mode_applies_to_referrer(hub: dict[str, Any], referrer_id: int) -> bool:
    ex = hub.get("exclusive_catalog") or {}
    mode = (ex.get("mode") or "off").strip().lower()
    if mode == "off":
        return False
    if mode == "all_maxi_sellers":
        return True
    ids = ex.get("seller_user_ids") or []
    return int(referrer_id) in set(int(x) for x in ids if str(x).isdigit() or isinstance(x, int))


async def viewer_exclusive_referrer_id(viewer_uid: int) -> int | None:
    """
    Если для зрителя включена эксклюзивная витрина — ID продавца-реферера, чьи товары показываем одни.
    """
    hub = await get_shop_referral_hub()
    ex = hub.get("exclusive_catalog") or {}
    mode = (ex.get("mode") or "off").strip().lower()
    if mode == "off":
        return None
    row = await database.fetch_one(users.select().where(users.c.id == int(viewer_uid)))
    if not row:
        return None
    uid = int(row.get("primary_user_id") or row["id"])
    if uid != int(viewer_uid):
        row = await database.fetch_one(users.select().where(users.c.id == uid))
    rb = row.get("referred_by") if row else None
    if not rb:
        return None
    rid = int(rb)
    if not await referrer_shop_hub_active(rid):
        return None
    if not _exclusive_mode_applies_to_referrer(hub, rid):
        return None
    return rid


async def referrer_ambassador_shop_visible(referrer_id: int) -> bool:
    """
    Показывать ли приглашённым внешнюю ссылку реферера (как у партнёра Старт+):
    прежнее правило ИЛИ активный хаб Макси с эксклюзивом для этого реферера.
    """
    from services.subscription_service import paid_subscription_for_referral_program

    if await paid_subscription_for_referral_program(int(referrer_id)):
        return True
    if not await referrer_shop_hub_active(int(referrer_id)):
        return False
    hub = await get_shop_referral_hub()
    if not _exclusive_mode_applies_to_referrer(hub, int(referrer_id)):
        return False
    return True


async def single_link_ai_for_exclusive_enabled() -> bool:
    hub = await get_shop_referral_hub()
    return bool(hub.get("single_link_ai_for_exclusive", True))
