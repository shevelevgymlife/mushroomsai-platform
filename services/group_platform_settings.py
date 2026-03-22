"""
Политика создания групповых чатов (хранится в platform_settings) и проверка прав.
"""
from __future__ import annotations

import json
from typing import Any

from db.database import database
from db.models import platform_settings
from services.plan_access import is_platform_operator

GROUP_CREATION_POLICY_KEY = "group_creation_policy"

# По умолчанию как раньше: Про и Макси; админка может сменить на admin_only или другие тарифы.
DEFAULT_GROUP_CREATION_POLICY: dict[str, Any] = {"mode": "by_plan", "plans": ["pro", "maxi"]}


def _normalize_policy(raw: dict[str, Any]) -> dict[str, Any]:
    mode = (raw.get("mode") or "by_plan").strip().lower()
    if mode not in ("admin_only", "by_plan"):
        mode = "by_plan"
    plans = raw.get("plans")
    if not isinstance(plans, list):
        plans = list(DEFAULT_GROUP_CREATION_POLICY["plans"])
    else:
        plans = [str(p).strip().lower() for p in plans if str(p).strip()]
    allowed = ("free", "start", "pro", "maxi")
    plans = [p for p in plans if p in allowed]
    if mode == "by_plan" and not plans:
        plans = list(DEFAULT_GROUP_CREATION_POLICY["plans"])
    return {"mode": mode, "plans": plans}


async def get_group_creation_policy() -> dict[str, Any]:
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == GROUP_CREATION_POLICY_KEY)
        )
        if row and (row.get("value") or "").strip():
            data = json.loads(row["value"])
            if isinstance(data, dict):
                return _normalize_policy(data)
    except Exception:
        pass
    return dict(DEFAULT_GROUP_CREATION_POLICY)


async def set_group_creation_policy(policy: dict[str, Any]) -> None:
    norm = _normalize_policy(policy)
    payload = json.dumps(norm, ensure_ascii=False)
    row = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == GROUP_CREATION_POLICY_KEY)
    )
    if row:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == GROUP_CREATION_POLICY_KEY)
            .values(value=payload)
        )
    else:
        await database.execute(
            platform_settings.insert().values(key=GROUP_CREATION_POLICY_KEY, value=payload)
        )


async def user_can_create_community_group(plan: str | None, user: dict[str, Any] | None) -> bool:
    """Может ли пользователь создать группу: оператор сайта всегда; иначе — по политике из админки."""
    if not user:
        return False
    if is_platform_operator(user):
        return True
    pol = await get_group_creation_policy()
    if pol.get("mode") == "admin_only":
        return False
    p = (plan or "free").lower()
    plans = pol.get("plans") or []
    return p in [str(x).lower() for x in plans]
