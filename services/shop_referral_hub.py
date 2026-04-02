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
    "transition_banner": {
        "enabled": False,
        "days_after_grace": 1,
        "scope_mode": "same_as_exclusive",
        "seller_user_ids": [],
    },
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
    tb = raw.get("transition_banner")
    if isinstance(tb, dict):
        sm = (tb.get("scope_mode") or "same_as_exclusive").strip().lower()
        if sm not in ("off", "same_as_exclusive", "all_maxi_sellers", "selected"):
            sm = "same_as_exclusive"
        tids: list[int] = []
        for x in tb.get("seller_user_ids") or []:
            try:
                tids.append(int(x))
            except (TypeError, ValueError):
                pass
        try:
            bd = int(tb.get("days_after_grace") or 1)
        except (TypeError, ValueError):
            bd = 1
        out["transition_banner"] = {
            "enabled": bool(tb.get("enabled")),
            "days_after_grace": max(0, min(30, bd)),
            "scope_mode": sm,
            "seller_user_ids": sorted(set(tids)),
        }
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
    cur = await get_shop_referral_hub()
    merged = dict(cur)
    merged.update(payload)
    normalized = _normalize_hub(merged)
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


def transition_banner_applies_to_referrer(hub: dict[str, Any], referrer_id: int) -> bool:
    """Продавец попадает под сценарий «грейс → баннер → стандарт», если включено и область совпала."""
    tb = hub.get("transition_banner") or {}
    if not bool(tb.get("enabled")):
        return False
    sm = (tb.get("scope_mode") or "same_as_exclusive").strip().lower()
    if sm == "off":
        return False
    if sm == "same_as_exclusive":
        ex = hub.get("exclusive_catalog") or {}
        if (ex.get("mode") or "off").strip().lower() == "off":
            return False
        return _exclusive_mode_applies_to_referrer(hub, int(referrer_id))
    if sm == "all_maxi_sellers":
        return True
    ids = tb.get("seller_user_ids") or []
    return int(referrer_id) in {int(x) for x in ids if str(x).isdigit() or isinstance(x, int)}


async def schedule_maxi_perks_grace(user_id: int) -> None:
    """Вызывается при сбросе подписки с maxi → free: грейс, опционально фаза баннера до стандарта."""
    hub = await get_shop_referral_hub()
    days = int(hub.get("grace_days_after_maxi_end") or 5)
    if days <= 0:
        await database.execute(
            users.update()
            .where(users.c.id == int(user_id))
            .values(maxi_perks_grace_until=None, maxi_shop_banner_until=None)
        )
        return
    until = datetime.utcnow() + timedelta(days=days)
    banner_until = None
    tb = hub.get("transition_banner") or {}
    if bool(tb.get("enabled")) and transition_banner_applies_to_referrer(hub, int(user_id)):
        try:
            bd = int(tb.get("days_after_grace") or 1)
        except (TypeError, ValueError):
            bd = 1
        bd = max(0, min(30, bd))
        if bd > 0:
            banner_until = until + timedelta(days=bd)
    await database.execute(
        users.update()
        .where(users.c.id == int(user_id))
        .values(maxi_perks_grace_until=until, maxi_shop_banner_until=banner_until)
    )


async def clear_maxi_perks_grace(user_id: int) -> None:
    await database.execute(
        users.update()
        .where(users.c.id == int(user_id))
        .values(maxi_perks_grace_until=None, maxi_shop_banner_until=None)
    )


async def referrer_in_partner_shop_banner_phase(referrer_id: int) -> bool:
    """У продавца закончился грейс, идёт фаза баннера (товары/партнёрские ссылки скрыты до конца окна)."""
    hub = await get_shop_referral_hub()
    if not bool((hub.get("transition_banner") or {}).get("enabled")):
        return False
    if not transition_banner_applies_to_referrer(hub, int(referrer_id)):
        return False
    row = await database.fetch_one(users.select().where(users.c.id == int(referrer_id)))
    if not row or not bool(row.get("marketplace_seller")):
        return False
    now = datetime.utcnow()
    gu = row.get("maxi_perks_grace_until")
    bu = row.get("maxi_shop_banner_until")
    if not gu or not bu:
        return False
    if gu > now:
        return False
    if bu <= now:
        return False
    return True


async def viewer_in_partner_shop_transition_hold(viewer_uid: int) -> bool:
    """Приглашённый реферером, у которого сейчас фаза «магазин не работает» между грейсом и стандартом."""
    row = await database.fetch_one(users.select().where(users.c.id == int(viewer_uid)))
    if not row:
        return False
    uid = int(row.get("primary_user_id") or row["id"])
    if uid != int(viewer_uid):
        row = await database.fetch_one(users.select().where(users.c.id == uid))
    rb = row.get("referred_by") if row else None
    if not rb:
        return False
    return await referrer_in_partner_shop_banner_phase(int(rb))


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
