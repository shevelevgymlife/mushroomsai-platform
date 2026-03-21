"""Moderation service: handle violations, bans, system DMs."""
from datetime import datetime, timedelta

SUPER_ADMIN_TG_ID = 742166400


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

    # System DM
    await db.execute(
        "INSERT INTO direct_messages (sender_id, recipient_id, text, is_system) VALUES (NULL, :uid, :text, true)",
        {"uid": user_id, "text": msg},
    )

    # Telegram notify
    tg_id = user.get("tg_id") or user.get("linked_tg_id")
    if tg_id:
        try:
            from config import settings
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": tg_id, "text": msg},
                )
        except Exception as e:
            print(f"TG notify error: {e}")

    # Notify admin
    try:
        from config import settings
        import httpx
        admin_msg = (
            f"⚠️ Нарушение #{count}\n"
            f"Пользователь: {user['name']} (id={user_id})\n"
            f"Причина: {reason}\n"
            f"Действие: {action}\n"
            f"Контент: {content[:100]}"
        )
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": SUPER_ADMIN_TG_ID, "text": admin_msg},
            )
    except Exception as e:
        print(f"Admin notify error: {e}")

    # Moderation log
    await db.execute(
        "INSERT INTO moderation_log (user_id, content_type, content_text, reason, action_taken) "
        "VALUES (:uid, 'post', :text, :reason, :action)",
        {"uid": user_id, "text": content[:500], "reason": reason, "action": action},
    )

    return {"violation_count": count, "message": msg, "action": action}
