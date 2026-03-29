"""Доступ к боту обучающих постов: запись в training_bot_operators по Telegram ID, либо аккаунт на сайте (владелец / can_training_bot)."""
from __future__ import annotations

from db.database import database
from db.models import users, admin_permissions, training_bot_operators
from auth.owner import is_platform_owner
import sqlalchemy as sa


async def resolve_registered_site_user_by_telegram(tg_id: int) -> dict | None:
    """Аккаунт на сайте (primary), привязанный к этому Telegram — без проверки доступа к боту."""
    row = await database.fetch_one(
        users.select().where(
            sa.or_(users.c.tg_id == tg_id, users.c.linked_tg_id == tg_id)
        )
    )
    if not row:
        return None
    u = dict(row)
    if u.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == u["primary_user_id"]))
        if primary:
            u = dict(primary)
    return u


async def training_bot_access_allowed_by_telegram(tg_id: int) -> bool:
    """Полный функционал бота: одобренный Telegram ID, либо сайт (владелец / право can_training_bot)."""
    op = await database.fetch_one(
        training_bot_operators.select().where(training_bot_operators.c.telegram_id == int(tg_id))
    )
    if op:
        return True
    u = await resolve_registered_site_user_by_telegram(tg_id)
    if not u:
        return False
    if is_platform_owner(u):
        return True
    uid = int(u["id"])
    prow = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == uid)
    )
    if prow and prow.get("can_training_bot"):
        return True
    return False


async def resolve_user_for_training_bot(tg_id: int) -> tuple[dict | None, str | None]:
    """
    Для колбэков и сценариев, где нужен dict пользователя сайта.
    При доступе только по Telegram (без аккаунта на сайте) user будет None — это нормально.
    """
    if not await training_bot_access_allowed_by_telegram(tg_id):
        return None, (
            "Нет доступа к боту обучающих постов. "
            "Нажмите «Получить разрешение на отправку постов» в меню или команду /start."
        )
    u = await resolve_registered_site_user_by_telegram(tg_id)
    return u, None
