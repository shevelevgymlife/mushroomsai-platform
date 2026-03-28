"""Notification service for MushroomsAI."""
import logging
from typing import Optional
import sqlalchemy as sa
from db.database import database

_logger = logging.getLogger(__name__)


async def create_notification(
    user_id: int,
    type: str,
    title: str,
    body: str,
    link: str = "",
    from_user_id: Optional[int] = None,
):
    """Create notification if user has it enabled, optionally send to Telegram."""
    if user_id == from_user_id:
        return  # Don't notify about own actions
    try:
        # Check settings
        row = await database.fetch_one(
            sa.text("SELECT * FROM notification_settings WHERE user_id=:uid"),
            {"uid": user_id}
        )
        type_map = {
            "new_follower": "new_follower",
            "new_like": "new_like",
            "new_comment": "new_comment",
            "new_message": "new_message",
            "new_reply": "new_reply",
            "post_in_group": "post_in_group",
            "mention": "mention",
            "new_post_from_following": "new_post_from_following",
        }
        if row:
            col = type_map.get(type)
            if col and not row[col]:
                return  # disabled

        # Create notification
        await database.execute(
            sa.text("""
                INSERT INTO notifications (user_id, type, title, body, link, from_user_id)
                VALUES (:uid, :type, :title, :body, :link, :fuid)
            """),
            {"uid": user_id, "type": type, "title": title, "body": body,
             "link": link, "fuid": from_user_id}
        )

        # Send to Telegram if enabled
        send_tg = row["send_to_telegram"] if row else True
        if send_tg:
            user_row = await database.fetch_one(
                sa.text("SELECT tg_id FROM users WHERE id=:uid"), {"uid": user_id}
            )
            if user_row and user_row["tg_id"]:
                try:
                    from bot.main_bot import send_telegram_notification
                    msg = f"\U0001f344 {title}\n{body}"
                    if link:
                        msg += f"\n{link}"
                    await send_telegram_notification(int(user_row["tg_id"]), msg)
                except Exception as e:
                    _logger.warning("TG notification failed: %s", e)
    except Exception as e:
        _logger.error("create_notification error: %s", e)
