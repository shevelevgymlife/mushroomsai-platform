"""
Ограничения контента кабинета по тарифу (пересечение с настройками админки dashboard_blocks).
"""
from __future__ import annotations

from typing import Any

# Ключи секций кабинета (как в dashboard_blocks.block_key)
FREE_BLOCKS = frozenset(
    {"ai_chat", "tariffs", "knowledge_base", "referral"}
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
    """Создание групповых чатов — доступно всем авторизованным (в т.ч. free)."""
    if user and user.get("role") == "admin":
        return True
    if not user:
        return False
    return True


def can_use_priority_pin(plan: str | None, user: dict[str, Any] | None) -> bool:
    if user and user.get("role") == "admin":
        return True
    return (plan or "free").lower() in ("pro", "maxi")
