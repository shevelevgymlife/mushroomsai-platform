"""Доступ к боту обучающих постов: админ + галочка can_training_bot (или владелец платформы)."""
from __future__ import annotations

from db.database import database
from db.models import users, admin_permissions
from auth.owner import is_platform_owner
import sqlalchemy as sa


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
    if (u.get("role") or "") != "admin":
        return None, "Нужна роль администратора и право на бот базы знаний."
    if is_platform_owner(u):
        return u, None
    prow = await database.fetch_one(
        admin_permissions.select().where(admin_permissions.c.user_id == u["id"])
    )
    if prow and prow.get("can_training_bot"):
        return u, None
    return None, (
        "Нет права «Бот обучающих постов». "
        "Главный администратор может включить галочку в разделе Пользователи → права доступа."
    )
