"""
Ограничения контента кабинета по тарифу (пересечение с настройками админки dashboard_blocks).
"""
from __future__ import annotations

from typing import Any

from config import settings

# Совпадает с web.routes.admin — супер-админ по фиксированному tg_id
SUPER_ADMIN_TG_ID = 742166400


def is_platform_operator(user: dict[str, Any] | None) -> bool:
    """
    Кто считается «администратором» для создания групп и т.п.:
    - role=admin в БД;
    - Telegram ID из .env (ADMIN_TG_ID) — владелец часто не имеет role=admin, но получает уведомления;
    - супер-админ по tg_id (как в /admin).
    """
    if not user:
        return False
    if (user.get("role") or "").lower() == "admin":
        return True
    tg = user.get("tg_id")
    linked = user.get("linked_tg_id")
    aid = int(getattr(settings, "ADMIN_TG_ID", 0) or 0)
    if aid and (tg == aid or linked == aid):
        return True
    if tg == SUPER_ADMIN_TG_ID or linked == SUPER_ADMIN_TG_ID:
        return True
    return False

# Ключи секций кабинета (как в dashboard_blocks.block_key)
FREE_BLOCKS = frozenset(
    {
        "ai_chat",
        "tariffs",
        "knowledge_base",
        "referral",
        # Сообщество и групповые чаты — для всех зарегистрированных (как публичная часть соцсети)
        "community",
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

PRO_EXTRA = frozenset({"pro_telegram", "pro_pin_info"})

MAXI_EXTRA = frozenset({"seller_marketplace"})


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
                "pro_telegram",
                "pro_pin_info",
                "seller_marketplace",
            }
        )

    p = (plan or "free").lower()
    if p == "free":
        return FREE_BLOCKS
    if p == "start":
        return START_BLOCKS
    if p == "pro":
        return START_BLOCKS | PRO_EXTRA
    if p == "maxi":
        u = START_BLOCKS | PRO_EXTRA
        if user and user.get("marketplace_seller"):
            u = u | MAXI_EXTRA
        return u
    return FREE_BLOCKS


def can_create_community_groups(plan: str | None, user: dict[str, Any] | None) -> bool:
    """Создание групп — тарифы Про и Макси, либо оператор/админ платформы (см. is_platform_operator)."""
    if not user:
        return False
    if is_platform_operator(user):
        return True
    p = (plan or "free").lower()
    return p in ("pro", "maxi")


def can_use_priority_pin(plan: str | None, user: dict[str, Any] | None) -> bool:
    if user and user.get("role") == "admin":
        return True
    return (plan or "free").lower() in ("pro", "maxi")
