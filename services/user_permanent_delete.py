"""Полное удаление пользователя и связанных данных (как в админке)."""
from __future__ import annotations

import logging

import sqlalchemy as sa

from auth.blocked_identities import unblock_identities_for_user
from db.database import database
from db.models import users

logger = logging.getLogger(__name__)

from auth.owner import is_platform_owner

_CLEANUP_SQL = [
    "UPDATE users SET primary_user_id=NULL WHERE primary_user_id=:uid",
    "UPDATE users SET referred_by=NULL WHERE referred_by=:uid",
    "UPDATE ai_settings SET updated_by=NULL WHERE updated_by=:uid",
    "DELETE FROM admin_permissions WHERE user_id=:uid",
    "DELETE FROM referrals WHERE referrer_id=:uid OR referred_id=:uid",
    "DELETE FROM community_messages WHERE sender_id=:uid OR recipient_id=:uid",
    "UPDATE community_posts SET folder_id=NULL WHERE folder_id IN (SELECT id FROM community_folders WHERE user_id=:uid)",
    "DELETE FROM community_folders WHERE user_id=:uid",
    "DELETE FROM community_likes WHERE post_id IN (SELECT id FROM community_posts WHERE user_id=:uid)",
    "DELETE FROM community_saved WHERE post_id IN (SELECT id FROM community_posts WHERE user_id=:uid)",
    "DELETE FROM community_comments WHERE post_id IN (SELECT id FROM community_posts WHERE user_id=:uid)",
    "DELETE FROM community_posts WHERE user_id=:uid",
    "DELETE FROM shop_product_likes WHERE product_id IN (SELECT id FROM shop_products WHERE seller_id=:uid)",
    "DELETE FROM shop_product_comments WHERE product_id IN (SELECT id FROM shop_products WHERE seller_id=:uid)",
    "DELETE FROM product_questions WHERE product_id IN (SELECT id FROM shop_products WHERE seller_id=:uid)",
    "DELETE FROM product_reviews WHERE product_id IN (SELECT id FROM shop_products WHERE seller_id=:uid)",
    "UPDATE shop_market_order_items SET product_id=NULL WHERE product_id IN (SELECT id FROM shop_products WHERE seller_id=:uid)",
    "DELETE FROM shop_products WHERE seller_id=:uid",
    "DELETE FROM post_likes WHERE user_id=:uid",
    "DELETE FROM posts WHERE user_id=:uid",
    "DELETE FROM subscriptions WHERE user_id=:uid",
    "DELETE FROM subscription_events WHERE subject_user_id=:uid OR counterparty_user_id=:uid",
    "DELETE FROM leads WHERE user_id=:uid",
    "DELETE FROM followups WHERE user_id=:uid",
    "DELETE FROM page_views WHERE user_id=:uid",
    "DELETE FROM direct_messages WHERE sender_id=:uid OR recipient_id=:uid",
    "DELETE FROM moderation_log WHERE user_id=:uid",
    "DELETE FROM community_likes WHERE user_id=:uid",
    "DELETE FROM community_saved WHERE user_id=:uid",
    "DELETE FROM community_reposts WHERE user_id=:uid",
    "DELETE FROM community_comments WHERE user_id=:uid",
    "DELETE FROM community_follows WHERE follower_id=:uid OR following_id=:uid",
    "DELETE FROM profile_likes WHERE user_id=:uid OR liked_user_id=:uid",
    "DELETE FROM community_group_message_likes WHERE user_id=:uid",
    "DELETE FROM community_group_messages WHERE sender_id=:uid",
    "DELETE FROM community_group_join_requests WHERE user_id=:uid",
    "DELETE FROM community_group_members WHERE user_id=:uid",
    "DELETE FROM community_groups WHERE created_by=:uid",
    "DELETE FROM user_block_overrides WHERE user_id=:uid",
    "DELETE FROM community_profiles WHERE user_id=:uid",
    "DELETE FROM plan_upgrade_requests WHERE user_id=:uid",
    "DELETE FROM messages WHERE user_id=:uid",
    "DELETE FROM sessions WHERE user_id=:uid",
    "DELETE FROM orders WHERE user_id=:uid",
    "DELETE FROM shop_market_order_items WHERE order_id IN (SELECT id FROM shop_market_orders WHERE user_id=:uid)",
    "DELETE FROM shop_market_orders WHERE user_id=:uid",
    "DELETE FROM shop_cart_items WHERE user_id=:uid",
    "DELETE FROM product_questions WHERE user_id=:uid OR answered_by=:uid",
    "DELETE FROM support_message_deliveries WHERE recipient_id=:uid OR admin_id=:uid",
    "DELETE FROM feedback WHERE user_id=:uid",
    "DELETE FROM product_reviews WHERE user_id=:uid",
]


def is_protected_super_admin(row: dict) -> bool:
    """Нельзя удалять владельца платформы (email/tg из config + legacy tg)."""
    return is_platform_owner(row)


async def permanently_delete_user(user_id: int) -> tuple[bool, str | None]:
    """Удалить пользователя и связанные строки. Вызывающий проверяет права и роль admin."""
    target = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not target:
        return False, "not_found"
    await unblock_identities_for_user(dict(target))
    for sql in _CLEANUP_SQL:
        try:
            await database.execute(sa.text(sql), {"uid": user_id})
        except Exception as e:
            logger.warning("permanently_delete_user cleanup failed uid=%s: %s", user_id, e)
    try:
        await database.execute(users.delete().where(users.c.id == user_id))
    except Exception as e:
        logger.exception("permanently_delete_user final delete uid=%s", user_id)
        return False, str(e)[:180]
    return True, None
