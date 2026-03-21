"""Постоянная блокировка по tg_id / google_id / email (отдельно от удаления аккаунта)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from db.database import database


def collect_identities(user: dict) -> list[tuple[str, str]]:
    """Все идентификаторы строки users для записи в blocked_identities."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []

    def add(id_type: str, raw: str | None) -> None:
        if not raw:
            return
        s = raw.strip()
        if not s:
            return
        key = (id_type, s)
        if key not in seen:
            seen.add(key)
            out.append(key)

    for col in ("tg_id", "linked_tg_id"):
        v = user.get(col)
        if v is not None:
            try:
                add("tg_id", str(int(v)))
            except (TypeError, ValueError):
                pass

    gid = user.get("google_id")
    if gid is not None:
        add("google_id", str(gid))

    em = user.get("email")
    if em:
        add("email", str(em).strip().lower())

    return out


async def block_identities_for_user(user: dict) -> None:
    for id_type, id_value in collect_identities(user):
        await database.execute(
            text(
                "INSERT INTO blocked_identities (id_type, id_value) VALUES (:t, :v) "
                "ON CONFLICT (id_type, id_value) DO NOTHING"
            ),
            {"t": id_type, "v": id_value},
        )


async def unblock_identities_for_user(user: dict) -> None:
    for id_type, id_value in collect_identities(user):
        await database.execute(
            text("DELETE FROM blocked_identities WHERE id_type = :t AND id_value = :v"),
            {"t": id_type, "v": id_value},
        )


def _normalize_identity(id_type: str, raw_value: str) -> str | None:
    if raw_value is None:
        return None
    s = str(raw_value).strip()
    if not s:
        return None
    if id_type == "email":
        return s.lower()
    if id_type == "tg_id":
        try:
            return str(int(s))
        except ValueError:
            return None
    return s


async def is_identity_blocked(id_type: str, raw_value: str | None) -> bool:
    v = _normalize_identity(id_type, raw_value or "")
    if not v:
        return False
    row = await database.fetch_one(
        text(
            "SELECT 1 AS x FROM blocked_identities WHERE id_type = :t AND id_value = :v LIMIT 1"
        ),
        {"t": id_type, "v": v},
    )
    return row is not None


def login_denied_for_user_row_sync(row: dict) -> bool:
    """Бан по флагу в users (без запросов в БД) — для каждого запроса с сессией."""
    if row.get("is_banned"):
        return True
    bu = row.get("ban_until")
    if bu is not None and isinstance(bu, datetime) and bu > datetime.utcnow():
        return True
    return False


async def login_denied_for_user_row(row: dict) -> bool:
    """Полная проверка при входе: бан + таблица blocked_identities."""
    if login_denied_for_user_row_sync(row):
        return True
    for id_type, id_value in collect_identities(row):
        if await is_identity_blocked(id_type, id_value):
            return True
    return False
