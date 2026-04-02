"""Полное удаление пользователя и связанных данных (как в админке)."""
from __future__ import annotations

import logging

import sqlalchemy as sa

from auth.blocked_identities import unblock_identities_for_user
from auth.owner import is_platform_owner
from db.database import database
from db.models import users

logger = logging.getLogger(__name__)


async def _revert_referrer_balances_for_referred_user(referred_id: int) -> None:
    """Снять с рефереров начисления по строкам referrals для удаляемого приглашённого."""
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT referrer_id, COALESCE(referral_bonus_amount, 0) AS bonus
            FROM referrals
            WHERE referred_id = :rid AND bonus_applied IS TRUE
            """
        ),
        {"rid": referred_id},
    )
    for r in rows:
        rid = int(r["referrer_id"])
        b = float(r["bonus"] or 0)
        if b <= 0:
            continue
        await database.execute(
            sa.text(
                """
                UPDATE users SET referral_balance = GREATEST(
                    0, COALESCE(referral_balance, 0) - :b
                ) WHERE id = :refid
                """
            ),
            {"b": b, "refid": rid},
        )


async def _neighbor_user_ids_for_follows(uid: int) -> list[int]:
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT DISTINCT x AS id FROM (
              SELECT follower_id AS x FROM community_follows WHERE following_id = :u
              UNION ALL
              SELECT following_id AS x FROM community_follows WHERE follower_id = :u
            ) q WHERE x IS NOT NULL
            """
        ),
        {"u": uid},
    )
    return [int(r["id"]) for r in rows]


async def _collect_community_post_ids_touched_by_user(uid: int) -> set[int]:
    ids: set[int] = set()
    for q in (
        "SELECT DISTINCT post_id FROM community_comments WHERE user_id = :u",
        "SELECT DISTINCT post_id FROM community_likes WHERE user_id = :u",
        "SELECT DISTINCT post_id FROM community_saved WHERE user_id = :u",
        "SELECT DISTINCT post_id FROM community_reposts WHERE user_id = :u",
    ):
        try:
            rows = await database.fetch_all(sa.text(q), {"u": uid})
            for r in rows:
                if r.get("post_id") is not None:
                    ids.add(int(r["post_id"]))
        except Exception as e:
            logger.debug("collect post ids skip: %s", e)
    return ids


def _sql_int_in(ids: list[int]) -> str:
    return ",".join(str(int(x)) for x in ids)


async def _recalc_community_post_stats(post_ids: list[int]) -> None:
    if not post_ids:
        return
    try:
        inc = _sql_int_in(post_ids)
        await database.execute(
            sa.text(
                f"""
                UPDATE community_posts p SET
                  comments_count = (SELECT COUNT(*)::int FROM community_comments c WHERE c.post_id = p.id),
                  likes_count = (SELECT COUNT(*)::int FROM community_likes l WHERE l.post_id = p.id),
                  saves_count = (SELECT COUNT(*)::int FROM community_saved s WHERE s.post_id = p.id),
                  reposts_count = (SELECT COUNT(*)::int FROM community_reposts r WHERE r.post_id = p.id)
                WHERE p.id IN ({inc})
                """
            )
        )
    except Exception as e:
        logger.warning("recalc community_posts stats: %s", e)


async def _recalc_user_follow_and_profile_counts(uids: list[int]) -> None:
    if not uids:
        return
    inc = _sql_int_in(uids)
    try:
        await database.execute(
            sa.text(
                f"""
                UPDATE users u SET
                  followers_count = (SELECT COUNT(*)::int FROM community_follows f WHERE f.following_id = u.id),
                  following_count = (SELECT COUNT(*)::int FROM community_follows f WHERE f.follower_id = u.id)
                WHERE u.id IN ({inc})
                """
            )
        )
    except Exception as e:
        logger.warning("recalc users follow counts: %s", e)
    try:
        await database.execute(
            sa.text(
                f"""
                UPDATE community_profiles cp SET
                  followers_count = (SELECT COUNT(*)::int FROM community_follows f WHERE f.following_id = cp.user_id),
                  following_count = (SELECT COUNT(*)::int FROM community_follows f WHERE f.follower_id = cp.user_id)
                WHERE cp.user_id IN ({inc})
                """
            )
        )
    except Exception as e:
        logger.warning("recalc community_profiles follow counts: %s", e)


# Порядок важен только там, где есть FK; остальное идемпотентно с try/except в цикле.
_CLEANUP_SQL = [
    "UPDATE users SET primary_user_id=NULL WHERE primary_user_id=:uid",
    "UPDATE users SET referred_by=NULL WHERE referred_by=:uid",
    "UPDATE users SET link_merge_secondary_id=NULL WHERE link_merge_secondary_id=:uid",
    "UPDATE ai_settings SET updated_by=NULL WHERE updated_by=:uid",
    "UPDATE referral_promo_links SET created_by=NULL WHERE created_by=:uid",
    "UPDATE training_bot_operators SET granted_by=NULL WHERE granted_by=:uid",
    "UPDATE community_group_messages SET addressed_user_id=NULL WHERE addressed_user_id=:uid",
    # Мессенджер (личные/групповые чаты)
    "DELETE FROM chat_reactions WHERE user_id=:uid",
    "DELETE FROM chat_messages WHERE user_id=:uid",
    "DELETE FROM chat_group_bans WHERE user_id=:uid OR banned_by=:uid",
    "DELETE FROM chat_group_audit WHERE actor_id=:uid",
    "DELETE FROM dm_user_blocks WHERE blocker_id=:uid OR blocked_id=:uid",
    "DELETE FROM chat_members WHERE user_id=:uid",
    "DELETE FROM chats c WHERE NOT EXISTS (SELECT 1 FROM chat_members m WHERE m.chat_id = c.id)",
    "UPDATE chats SET created_by=NULL WHERE created_by=:uid",
    # Уведомления
    "DELETE FROM in_app_notifications WHERE recipient_id=:uid OR actor_id=:uid",
    "DELETE FROM notifications WHERE user_id=:uid OR from_user_id=:uid",
    "DELETE FROM notification_settings WHERE user_id=:uid",
    "DELETE FROM pending_google_links WHERE user_id=:uid",
    # Автопост из Telegram-канала
    """DELETE FROM channel_autopost_log WHERE channel_chat_id IN (
        SELECT channel_chat_id FROM user_channel_autopost WHERE user_id=:uid
    )""",
    "DELETE FROM user_channel_autopost WHERE user_id=:uid",
    "DELETE FROM referral_withdrawals WHERE user_id=:uid",
    "DELETE FROM admin_permissions WHERE user_id=:uid",
    "DELETE FROM training_bot_operators WHERE user_id=:uid",
    """DELETE FROM training_bot_operators WHERE telegram_id IN (
        SELECT tg_id FROM users WHERE id=:uid AND tg_id IS NOT NULL
        UNION
        SELECT linked_tg_id FROM users WHERE id=:uid AND linked_tg_id IS NOT NULL
    )""",
    """DELETE FROM training_bot_access_requests WHERE requester_tg_id IN (
        SELECT tg_id FROM users WHERE id=:uid AND tg_id IS NOT NULL
        UNION
        SELECT linked_tg_id FROM users WHERE id=:uid AND linked_tg_id IS NOT NULL
    )""",
    """DELETE FROM bot_task_requests WHERE tg_user_id IN (
        SELECT tg_id FROM users WHERE id=:uid AND tg_id IS NOT NULL
        UNION
        SELECT linked_tg_id FROM users WHERE id=:uid AND linked_tg_id IS NOT NULL
    )""",
    "DELETE FROM community_messages WHERE sender_id=:uid OR recipient_id=:uid",
    "UPDATE community_posts SET folder_id=NULL WHERE folder_id IN (SELECT id FROM community_folders WHERE user_id=:uid)",
    "DELETE FROM community_folders WHERE user_id=:uid",
    "DELETE FROM community_likes WHERE post_id IN (SELECT id FROM community_posts WHERE user_id=:uid)",
    "DELETE FROM community_saved WHERE post_id IN (SELECT id FROM community_posts WHERE user_id=:uid)",
    "DELETE FROM community_comments WHERE post_id IN (SELECT id FROM community_posts WHERE user_id=:uid)",
    "DELETE FROM community_reposts WHERE post_id IN (SELECT id FROM community_posts WHERE user_id=:uid)",
    "DELETE FROM community_posts WHERE user_id=:uid",
    "DELETE FROM shop_product_likes WHERE user_id=:uid",
    "DELETE FROM shop_product_comments WHERE user_id=:uid",
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
    "DELETE FROM wellness_bundle_feedback WHERE user_id=:uid",
    "DELETE FROM wellness_experiment_assignments WHERE user_id=:uid",
    "DELETE FROM wellness_user_automation WHERE user_id=:uid",
    "DELETE FROM wellness_user_state_daily WHERE user_id=:uid",
    "DELETE FROM wellness_ai_recommendations WHERE user_id=:uid",
    "DELETE FROM wellness_daily_snapshots WHERE user_id=:uid",
    "DELETE FROM wellness_journal_entries WHERE user_id=:uid",
    "DELETE FROM direct_messages WHERE sender_id=:uid OR recipient_id=:uid",
    "DELETE FROM moderation_log WHERE user_id=:uid",
    "DELETE FROM community_likes WHERE user_id=:uid",
    "DELETE FROM community_saved WHERE user_id=:uid",
    "DELETE FROM community_reposts WHERE user_id=:uid",
    "DELETE FROM community_comments WHERE user_id=:uid",
    "DELETE FROM community_follows WHERE follower_id=:uid OR following_id=:uid",
    "DELETE FROM profile_likes WHERE user_id=:uid OR liked_user_id=:uid",
    "DELETE FROM community_group_message_likes WHERE user_id=:uid",
    "DELETE FROM community_group_member_permissions WHERE user_id=:uid",
    "DELETE FROM community_group_member_bans WHERE user_id=:uid OR banned_by=:uid",
    "DELETE FROM community_group_typing_status WHERE user_id=:uid",
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
    uid = int(user_id)

    neighbor_ids = await _neighbor_user_ids_for_follows(uid)
    touched_posts = list(await _collect_community_post_ids_touched_by_user(uid))

    await unblock_identities_for_user(dict(target))

    await _revert_referrer_balances_for_referred_user(uid)
    try:
        await database.execute(
            sa.text("DELETE FROM referrals WHERE referrer_id=:uid OR referred_id=:uid"),
            {"uid": uid},
        )
    except Exception as e:
        logger.warning("permanently_delete_user referrals uid=%s: %s", uid, e)

    for sql in _CLEANUP_SQL:
        try:
            await database.execute(sa.text(sql), {"uid": uid})
        except Exception as e:
            logger.warning("permanently_delete_user cleanup failed uid=%s: %s", uid, e)

    await _recalc_community_post_stats(touched_posts)
    await _recalc_user_follow_and_profile_counts(neighbor_ids)

    try:
        await database.execute(users.delete().where(users.c.id == uid))
    except Exception as e:
        logger.exception("permanently_delete_user final delete uid=%s", uid)
        return False, str(e)[:180]
    return True, None
