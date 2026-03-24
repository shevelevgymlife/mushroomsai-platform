"""Moderation service: handle violations, bans, system DMs."""
from datetime import datetime, timedelta


async def handle_violation(user_id: int, reason: str, content: str, db) -> dict:
    await db.execute(
        "UPDATE users SET violations_count = violations_count + 1 WHERE id = :id",
        {"id": user_id},
    )
    user = await db.fetch_one(
        "SELECT violations_count, tg_id, linked_tg_id, name FROM users WHERE id = :id",
        {"id": user_id},
    )
    count = user["violations_count"]

    if count == 1:
        action = "warning_1"
        msg = (
            "⚠️ Предупреждение 1 из 3\n\n"
            "Ваш контент нарушает правила сообщества.\n"
            "Причина: " + reason
        )
    elif count == 2:
        action = "warning_2"
        msg = (
            "⚠️ Предупреждение 2 из 3\n\n"
            "При следующем нарушении аккаунт будет заблокирован на 24 часа."
        )
    elif count == 3:
        action = "ban_24h"
        ban_until = datetime.utcnow() + timedelta(hours=24)
        await db.execute(
            "UPDATE users SET ban_until = :b, ban_reason = :r WHERE id = :id",
            {"b": ban_until, "r": reason, "id": user_id},
        )
        msg = "🚫 Аккаунт заблокирован на 24 часа\n\nПричина: " + reason
    else:
        action = "ban_permanent"
        await db.execute(
            "UPDATE users SET is_banned = true, ban_reason = :r WHERE id = :id",
            {"r": reason, "id": user_id},
        )
        try:
            from db.database import database as _db
            from db.models import users as _users
            from auth.blocked_identities import block_identities_for_user

            full = await _db.fetch_one(_users.select().where(_users.c.id == user_id))
            if full:
                await block_identities_for_user(dict(full))
        except Exception:
            pass
        msg = "🚫 Аккаунт заблокирован навсегда\n\nПричина: систематические нарушения правил."

    await db.execute(
        "INSERT INTO direct_messages (sender_id, recipient_id, text, is_system) VALUES (NULL, :uid, :text, true)",
        {"uid": user_id, "text": msg},
    )

    await db.execute(
        "INSERT INTO moderation_log (user_id, content_type, content_text, reason, action_taken) "
        "VALUES (:uid, 'post', :text, :reason, :action)",
        {"uid": user_id, "text": content[:500], "reason": reason, "action": action},
    )

    return {"violation_count": count, "message": msg, "action": action}
