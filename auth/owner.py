"""Владелец платформы: полный доступ в админку (email / tg из config + legacy super tg)."""
from __future__ import annotations

from typing import Any

from config import settings
from db.database import database
from db.models import users

# Совпадает с историческим супер-админом в проекте (tg)
SUPER_ADMIN_TG_ID = 742166400

# Если в .env задан пустой ADMIN_EMAIL, pydantic перезаписывает default — оставляем явный fallback.
DEFAULT_OWNER_EMAIL = "shevelevgymlife@gmail.com"


def owner_email_effective() -> str:
    raw = (getattr(settings, "ADMIN_EMAIL", "") or "").strip().lower()
    return raw or DEFAULT_OWNER_EMAIL


def _tg_eq(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def is_platform_owner(user: dict | None) -> bool:
    """
    Главный владелец (полные права в /admin как у legacy tg-супера):
    - фиксированный tg_id (SUPER_ADMIN_TG_ID);
    - Telegram ID из ADMIN_TG_ID в .env;
    - email = ADMIN_EMAIL (вход через Google и т.д.).
    """
    if not user:
        return False
    if _tg_eq(user.get("tg_id"), SUPER_ADMIN_TG_ID) or _tg_eq(user.get("linked_tg_id"), SUPER_ADMIN_TG_ID):
        return True
    aid = int(getattr(settings, "ADMIN_TG_ID", 0) or 0)
    if aid and (_tg_eq(user.get("tg_id"), aid) or _tg_eq(user.get("linked_tg_id"), aid)):
        return True
    em = (user.get("email") or "").strip().lower()
    if em and em == owner_email_effective():
        return True
    return False


async def sync_owner_admin_role(user_dict: dict) -> None:
    """Если пользователь — владелец, выставить role=admin в БД и в объекте сессии."""
    if not user_dict or not is_platform_owner(user_dict):
        return
    if (user_dict.get("role") or "").lower() == "admin":
        return
    uid = user_dict.get("id")
    if not uid:
        return
    await database.execute(users.update().where(users.c.id == uid).values(role="admin"))
    user_dict["role"] = "admin"
