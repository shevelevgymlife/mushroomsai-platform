"""Migration v17: notifications system tables."""
import logging
import sqlalchemy as sa
from db.database import database

logger = logging.getLogger(__name__)


async def run():
    """Create notifications and notification_settings tables."""
    try:
        await database.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                type VARCHAR(64) NOT NULL,
                title VARCHAR(255) NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL DEFAULT '',
                from_user_id INTEGER,
                is_read BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        await database.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id)"
        ))
        await database.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notifications(user_id, is_read)"
        ))
        logger.info("DB migration v17: notifications table OK")
    except Exception as e:
        logger.warning("DB migration v17 notifications table: %s", e)

    try:
        await database.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS notification_settings (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE,
                new_follower BOOLEAN NOT NULL DEFAULT true,
                new_like BOOLEAN NOT NULL DEFAULT true,
                new_comment BOOLEAN NOT NULL DEFAULT true,
                new_message BOOLEAN NOT NULL DEFAULT true,
                new_reply BOOLEAN NOT NULL DEFAULT true,
                post_in_group BOOLEAN NOT NULL DEFAULT true,
                mention BOOLEAN NOT NULL DEFAULT true,
                new_post_from_following BOOLEAN NOT NULL DEFAULT false,
                send_to_telegram BOOLEAN NOT NULL DEFAULT true,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        logger.info("DB migration v17: notification_settings table OK")
    except Exception as e:
        logger.warning("DB migration v17 notification_settings table: %s", e)
