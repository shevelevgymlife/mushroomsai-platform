"""Доступ к боту обучающих постов: владелец платформы, запись в training_bot_operators или can_training_bot в admin_permissions."""
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


async def training_bot_access_allowed(u: dict) -> bool:
    """Разрешён ли пользователю (по primary id) полный функционал бота обучающих постов."""
    if is_platform_owner(u):
        return True
    uid = int(u["id"])
    op = await database.fetch_one(
        training_bot_operators.select().where(training_bot_operators.c.user_id == uid)
    )
    if op:
        return True
    prow = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == uid)
    )
    if prow and prow.get("can_training_bot"):
        return True
    return False


async def resolve_user_for_training_bot(tg_id: int) -> tuple[dict | None, str | None]:
    """
    Возвращает (user_dict, error_message).
    user_dict — основной аккаунт (primary), если был привязан вторичный.
    """
    row = await database.fetch_one(
        users.select().where(
            sa.or_(users.c.tg_id == tg_id, users.c.linked_tg_id == tg_id)
        )
    )
    if not row:
        return None, "Сначала войдите на сайте и привяжите этот Telegram к аккаунту."
    u = dict(row)
    if u.get("primary_user_id"):
        primary = await database.fetch_one(users.select().where(users.c.id == u["primary_user_id"]))
        if primary:
            u = dict(primary)
    if await training_bot_access_allowed(u):
        return u, None
    return None, (
        "Нет доступа к боту обучающих постов (@Neuro_fungi_system_info_bot). "
        "Администратор может выдать доступ в кабинете: раздел «Обучающие посты» → доступ к боту."
    )
