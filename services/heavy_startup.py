"""Тяжёлая фаза старта: миграции, сиды, планировщик (uvicorn уже слушает порт — Render health check успевает)."""
from __future__ import annotations

import logging
import os

import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import func, select

from ai.system_prompt import DEFAULT_SYSTEM_PROMPT
from db.database import database, metadata, get_engine
from db.models import ai_settings
from services.deploy_notify import send_deploy_notifications
from services.scheduler import start_scheduler

logger = logging.getLogger(__name__)


async def run_heavy_startup(app: FastAPI) -> None:
    try:
        _commit = (os.environ.get("RENDER_GIT_COMMIT") or "")[:12] or "n/a"
        logger.info("Boot: render_git=%s", _commit)
        await send_deploy_notifications()

        _base = "/data" if os.path.exists("/data") else "./media"
        os.makedirs(f"{_base}/products", exist_ok=True)
        os.makedirs(f"{_base}/community", exist_ok=True)
        os.makedirs(f"{_base}/community/groups/msg", exist_ok=True)
        os.makedirs(f"{_base}/avatars", exist_ok=True)
        logger.info("Media dirs ready under %s", _base)

        try:
            metadata.create_all(get_engine())
            logger.info("Tables created")
        except Exception as e:
            logger.warning("Table creation: %s", e)

        new_columns = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS linked_tg_id BIGINT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS linked_google_id VARCHAR(128)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS apple_id VARCHAR(128)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS linked_apple_id VARCHAR(128)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS primary_user_id INTEGER REFERENCES users(id)",
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS image_url TEXT",
            """CREATE TABLE IF NOT EXISTS feedback (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            message TEXT NOT NULL,
            status VARCHAR(20) DEFAULT 'new',
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            """CREATE TABLE IF NOT EXISTS community_groups (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            """CREATE TABLE IF NOT EXISTS community_group_members (
            id SERIAL PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES community_groups(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            joined_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(group_id, user_id)
        )""",
            """CREATE TABLE IF NOT EXISTS community_group_messages (
            id SERIAL PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES community_groups(id) ON DELETE CASCADE,
            sender_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            "ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS join_mode VARCHAR(20) DEFAULT 'approval'",
            "ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS message_retention_days INTEGER",
            "ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS image_url TEXT",
            "ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS allow_photo BOOLEAN DEFAULT true",
            "ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS allow_audio BOOLEAN DEFAULT true",
            "ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS auto_delete_enabled BOOLEAN DEFAULT false",
            "ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS pinned_message_text TEXT",
            "ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS pinned_message_updated_at TIMESTAMP",
            "ALTER TABLE community_group_members ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMP",
            "ALTER TABLE community_group_members ADD COLUMN IF NOT EXISTS chat_last_seen_at TIMESTAMP",
            "ALTER TABLE community_group_members ADD COLUMN IF NOT EXISTS addressed_last_read_at TIMESTAMP",
            "ALTER TABLE community_group_members ADD COLUMN IF NOT EXISTS notifications_enabled BOOLEAN DEFAULT true",
            """CREATE TABLE IF NOT EXISTS community_group_join_requests (
            id SERIAL PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES community_groups(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(group_id, user_id)
        )""",
            """CREATE TABLE IF NOT EXISTS plan_upgrade_requests (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            requested_plan TEXT NOT NULL,
            note TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS legal_accepted_at TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS legal_docs_version TEXT",
            "CREATE INDEX IF NOT EXISTS idx_cggm_group_time ON community_group_messages(group_id, created_at)",
            """CREATE TABLE IF NOT EXISTS ai_training_folders (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        )""",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS needs_tariff_choice BOOLEAN DEFAULT false",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS marketplace_seller BOOLEAN DEFAULT false",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_balance NUMERIC(12,2) DEFAULT 0",
            "UPDATE users SET needs_tariff_choice = false WHERE needs_tariff_choice IS NULL",
            "ALTER TABLE users ALTER COLUMN needs_tariff_choice SET DEFAULT true",
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS seller_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_link_label TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_link_url TEXT",
            "ALTER TABLE ai_training_posts ADD COLUMN IF NOT EXISTS folder TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_apple_id ON users(apple_id)",
            "ALTER TABLE community_likes ADD COLUMN IF NOT EXISTS seen_by_post_owner BOOLEAN NOT NULL DEFAULT true",
            "ALTER TABLE community_comments ADD COLUMN IF NOT EXISTS seen_by_post_owner BOOLEAN NOT NULL DEFAULT true",
            "ALTER TABLE profile_likes ADD COLUMN IF NOT EXISTS seen_by_owner BOOLEAN NOT NULL DEFAULT true",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS decimal_del_balance TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS decimal_balance_cached_at TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS shevelev_balance_cached TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS shevelev_balance_cached_at TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS show_del_to_public BOOLEAN DEFAULT true",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS show_shev_to_public BOOLEAN DEFAULT true",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_lamp_enabled BOOLEAN DEFAULT true",
            """CREATE TABLE IF NOT EXISTS task_approvals (
            id SERIAL PRIMARY KEY,
            token VARCHAR(64) UNIQUE NOT NULL,
            question TEXT NOT NULL,
            details TEXT,
            requested_by VARCHAR(64),
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            decided_by_tg_id BIGINT,
            decided_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            """CREATE TABLE IF NOT EXISTS shop_product_likes (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES shop_products(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, product_id)
        )""",
            """CREATE TABLE IF NOT EXISTS shop_product_comments (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES shop_products(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            """CREATE TABLE IF NOT EXISTS product_questions (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES shop_products(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            question_text TEXT NOT NULL,
            answer_text TEXT,
            answered_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            answered_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            """CREATE TABLE IF NOT EXISTS shop_cart_items (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES shop_products(id) ON DELETE CASCADE,
            quantity INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, product_id)
        )""",
            """CREATE TABLE IF NOT EXISTS shop_market_orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            status VARCHAR(32) DEFAULT 'new',
            delivery_address TEXT,
            delivery_city TEXT,
            delivery_phone TEXT,
            delivery_comment TEXT,
            total_amount INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            """CREATE TABLE IF NOT EXISTS shop_market_order_items (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES shop_market_orders(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES shop_products(id) ON DELETE SET NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            unit_price INTEGER
        )""",
            """CREATE TABLE IF NOT EXISTS support_message_deliveries (
            id SERIAL PRIMARY KEY,
            admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            feedback_id INTEGER REFERENCES feedback(id) ON DELETE SET NULL,
            message_preview TEXT,
            in_app_delivered BOOLEAN DEFAULT true,
            telegram_attempted BOOLEAN DEFAULT false,
            telegram_ok BOOLEAN DEFAULT false,
            user_was_online BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            "UPDATE dashboard_blocks SET access_level = 'all' WHERE block_key IN ('community', 'posts', 'profile_photo')",
            "ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS title TEXT",
            """CREATE TABLE IF NOT EXISTS community_group_message_likes (
            id SERIAL PRIMARY KEY,
            message_id INTEGER NOT NULL REFERENCES community_group_messages(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(message_id, user_id)
        )""",
            "ALTER TABLE community_group_messages ADD COLUMN IF NOT EXISTS reply_to_id INTEGER REFERENCES community_group_messages(id) ON DELETE SET NULL",
            "ALTER TABLE community_group_messages ADD COLUMN IF NOT EXISTS image_url TEXT",
            "ALTER TABLE community_group_messages ADD COLUMN IF NOT EXISTS audio_url TEXT",
            "ALTER TABLE community_group_messages ADD COLUMN IF NOT EXISTS addressed_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
            """CREATE TABLE IF NOT EXISTS community_group_member_permissions (
            id SERIAL PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES community_groups(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            can_send_messages BOOLEAN DEFAULT true,
            can_send_photo BOOLEAN DEFAULT true,
            can_send_audio BOOLEAN DEFAULT true,
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(group_id, user_id)
        )""",
            """CREATE TABLE IF NOT EXISTS community_group_member_bans (
            id SERIAL PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES community_groups(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            banned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            reason TEXT,
            banned_until TIMESTAMP,
            is_permanent BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(group_id, user_id)
        )""",
            """CREATE TABLE IF NOT EXISTS community_group_typing_status (
            id SERIAL PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES community_groups(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(group_id, user_id)
        )""",
            """CREATE TABLE IF NOT EXISTS task_confirmations (
            id SERIAL PRIMARY KEY,
            request_id VARCHAR(128) UNIQUE NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            """CREATE TABLE IF NOT EXISTS bot_task_requests (
            id SERIAL PRIMARY KEY,
            tg_user_id BIGINT NOT NULL,
            username TEXT,
            full_name TEXT,
            task_text TEXT NOT NULL,
            needs_photo BOOLEAN NOT NULL DEFAULT false,
            photo_file_id TEXT,
            status VARCHAR(32) NOT NULL DEFAULT 'accepted',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )""",
            "ALTER TABLE bot_task_requests ADD COLUMN IF NOT EXISTS autorun_requested BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE bot_task_requests ADD COLUMN IF NOT EXISTS autorun_started_at TIMESTAMP",
            "ALTER TABLE bot_task_requests ADD COLUMN IF NOT EXISTS autorun_result TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS screen_rim_json TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS link_merge_secondary_id INTEGER",
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS image_urls_json TEXT",
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS brand_name TEXT",
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS price_old INTEGER",
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS verified_personal BOOLEAN DEFAULT false",
            "ALTER TABLE admin_permissions ADD COLUMN IF NOT EXISTS can_training_bot BOOLEAN NOT NULL DEFAULT false",
            """CREATE TABLE IF NOT EXISTS pending_google_links (
            id SERIAL PRIMARY KEY,
            token VARCHAR(64) UNIQUE NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            google_id VARCHAR(128) NOT NULL,
            email TEXT,
            name TEXT,
            avatar TEXT,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            "ALTER TABLE ai_training_posts ADD COLUMN IF NOT EXISTS ingest_tg_chat_id BIGINT",
            "ALTER TABLE ai_training_posts ADD COLUMN IF NOT EXISTS ingest_tg_message_id BIGINT",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_training_posts_tg_channel_msg ON ai_training_posts(ingest_tg_chat_id, ingest_tg_message_id) WHERE ingest_tg_chat_id IS NOT NULL AND ingest_tg_message_id IS NOT NULL",
            "ALTER TABLE ai_training_posts ADD COLUMN IF NOT EXISTS image_url TEXT",
            "ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS from_telegram BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS retrieval_mode VARCHAR(64) NOT NULL DEFAULT 'title_first'",
            "ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS retrieval_top_k INTEGER NOT NULL DEFAULT 24",
        ]
        try:
            await database.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_referrals_referred_unique ON referrals(referred_id)"
            )
        except Exception as e:
            logger.warning("referrals unique index: %s", e)
        for sql in new_columns:
            try:
                await database.execute(sql)
            except Exception as e:
                logger.warning("Column migration: %s", e)

        try:
            cnt = await database.fetch_val(sa.text("SELECT COUNT(*) FROM dashboard_blocks"))
            if not cnt:
                blocks = [
                    ("ai_chat", "AI Консультант", 0, "all"),
                    ("messages", "Сообщения", 1, "start"),
                    ("community", "Сообщество", 2, "start"),
                    ("shop", "Магазин", 3, "start"),
                    ("profile_photo", "Фото профиля", 4, "start"),
                    ("posts", "Посты", 5, "start"),
                    ("tariffs", "Тарифы и подписка", 6, "all"),
                    ("referral", "Реферальная программа", 7, "all"),
                    ("knowledge_base", "База знаний", 8, "all"),
                ]
                for key, name, pos, al in blocks:
                    await database.execute(
                        sa.text(
                            "INSERT INTO dashboard_blocks (block_key, block_name, position, is_visible, access_level) "
                            "VALUES (:k, :n, :p, true, :al) ON CONFLICT (block_key) DO NOTHING"
                        ).bindparams(k=key, n=name, p=pos, al=al)
                    )
                logger.info("Seeded dashboard_blocks defaults")
            else:
                await database.execute(
                    sa.text(
                        "UPDATE dashboard_blocks SET is_visible = true "
                        "WHERE block_key IN ('community','messages','shop','posts','profile_photo') "
                        "AND is_visible = false"
                    )
                )
                await database.execute(
                    sa.text(
                        "UPDATE dashboard_blocks SET is_visible = true, access_level = 'all' "
                        "WHERE block_key = 'referral'"
                    )
                )
            for key, name, pos, al in (
                ("pro_pin_info", "Закреп в ленте (Про)", 91, "pro"),
                ("seller_marketplace", "Кабинет продавца (Макси)", 92, "maxi"),
            ):
                await database.execute(
                    sa.text(
                        "INSERT INTO dashboard_blocks (block_key, block_name, position, is_visible, access_level) "
                        "VALUES (:k, :n, :p, true, :al) ON CONFLICT (block_key) DO NOTHING"
                    ).bindparams(k=key, n=name, p=pos, al=al)
                )
        except Exception as e:
            logger.warning("dashboard_blocks seed: %s", e)

        try:
            count = await database.fetch_val(select(func.count()).select_from(ai_settings))
            if not count:
                await database.execute(ai_settings.insert().values(system_prompt=DEFAULT_SYSTEM_PROMPT))
        except Exception as e:
            logger.warning("AI settings init: %s", e)

        start_scheduler()
        app.state.startup_complete = True
        logger.info("Heavy startup complete; full traffic enabled")
    except Exception as e:
        logger.critical("Heavy startup failed: %s", e, exc_info=True)
        app.state.startup_error = str(e)
