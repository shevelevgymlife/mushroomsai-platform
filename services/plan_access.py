"""
Ограничения контента кабинета по тарифу (пересечение с настройками админки dashboard_blocks).
"""
from __future__ import annotations

from typing import Any

from auth.owner import owner_email_effective
from config import settings
from services.payment_plans_catalog import ACCESS_TIERS

# Совпадает с auth.owner — legacy супер-админ по tg_id
SUPER_ADMIN_TG_ID = 742166400


def _tg_equal(a: Any, b: Any) -> bool:
    """Сравнение Telegram ID из БД (int/str) с настройкой .env."""
    if a is None or b is None:
        return False
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def is_platform_operator(user: dict[str, Any] | None) -> bool:
    """
    Кто считается «администратором» для создания групп и т.п.:
    - role=admin в БД;
    - Telegram ID из .env (ADMIN_TG_ID) — сравнение int/str;
    - email = ADMIN_EMAIL (вход через Google без tg_id);
    - супер-админ по tg_id (как в /admin).
    """
    if not user:
        return False
    if (user.get("role") or "").lower() in ("admin", "moderator"):
        return True
    if (user.get("email") or "").strip().lower() == owner_email_effective():
        return True
    tg = user.get("tg_id")
    linked = user.get("linked_tg_id")
    aid = int(getattr(settings, "ADMIN_TG_ID", 0) or 0)
    if aid and (_tg_equal(tg, aid) or _tg_equal(linked, aid)):
        return True
    if _tg_equal(tg, SUPER_ADMIN_TG_ID) or _tg_equal(linked, SUPER_ADMIN_TG_ID):
        return True
    return False

# Ключи секций кабинета (как в dashboard_blocks.block_key)
FREE_BLOCKS = frozenset(
    {
        "ai_chat",
        "tariffs",
        "knowledge_base",
        "referral",
        # Лента и соцсети — с тарифа «Старт», пробного «Старт» или выше
        "posts",
        "profile_photo",
    }
)

START_BLOCKS = frozenset(
    {
        "ai_chat",
        "messages",
        "community",
        "shop",
        "profile_photo",
        "posts",
        "tariffs",
        "referral",
        "knowledge_base",
    }
)

PRO_EXTRA = frozenset({"pro_pin_info"})

MAXI_EXTRA = frozenset({"seller_marketplace"})


def _effective_access_tier(plan: str | None, user: dict[str, Any] | None) -> str:
    """Уровень блоков кабинета: из user.plan_access_tier (сессия) или по legacy-совпадению slug с tier."""
    if user:
        t = (user.get("plan_access_tier") or "").strip().lower()
        if t in ACCESS_TIERS:
            return t
    p = (plan or "free").lower()
    if p in ACCESS_TIERS:
        return p
    return "start" if p != "free" else "free"


def plan_allowed_block_keys(plan: str | None, user: dict[str, Any] | None) -> frozenset[str]:
    """Максимальный набор блоков, разрешённых тарифом (без учёта админских overrides)."""
    if user and user.get("role") == "admin":
        # Админ видит всё, что разрешит compute_visible_blocks
        return frozenset(
            {
                "ai_chat",
                "messages",
                "community",
                "shop",
                "profile_photo",
                "posts",
                "tariffs",
                "referral",
                "knowledge_base",
                "pro_pin_info",
                "seller_marketplace",
            }
        )

    tier = _effective_access_tier(plan, user)
    if tier == "free":
        return FREE_BLOCKS
    if tier == "start":
        return START_BLOCKS
    if tier == "pro":
        return START_BLOCKS | PRO_EXTRA
    if tier == "maxi":
        u = START_BLOCKS | PRO_EXTRA
        if user and user.get("marketplace_seller"):
            u = u | MAXI_EXTRA
        return u
    return FREE_BLOCKS


def can_create_community_groups(plan: str | None, user: dict[str, Any] | None) -> bool:
    """Устаревшая синхронная проверка без БД. Реальная политика — async user_can_create_community_group (админка «Группы»)."""
    if not user:
        return False
    if is_platform_operator(user):
        return True
    return _effective_access_tier(plan, user) in ("pro", "maxi")


def can_use_priority_pin(plan: str | None, user: dict[str, Any] | None) -> bool:
    if user and user.get("role") == "admin":
        return True
    return _effective_access_tier(plan, user) in ("pro", "maxi")


def can_use_community_group_chats(user: dict[str, Any] | None, plan: str | None) -> bool:
    """Групповые чаты: тариф Старт и выше, либо роли admin/moderator."""
    if not user:
        return False
    if (user.get("role") or "user").lower() in ("admin", "moderator"):
        return True
    return _effective_access_tier(plan, user) != "free"
