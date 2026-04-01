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
        os.makedirs(f"{_base}/radio/downtempo", exist_ok=True)
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
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_thoughts TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_thoughts_font VARCHAR(80)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_thoughts_color VARCHAR(16)",
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
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_ui_theme VARCHAR(64)",
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
            "ALTER TABLE admin_permissions ADD COLUMN IF NOT EXISTS can_ai_unlimited BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE admin_permissions ADD COLUMN IF NOT EXISTS can_ai_posts BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE admin_permissions ADD COLUMN IF NOT EXISTS can_community BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE admin_permissions ADD COLUMN IF NOT EXISTS can_groups BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE admin_permissions ADD COLUMN IF NOT EXISTS can_homepage BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE admin_permissions ADD COLUMN IF NOT EXISTS can_dashboard_blocks BOOLEAN NOT NULL DEFAULT false",
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
            """CREATE TABLE IF NOT EXISTS training_bot_operators (
            telegram_id BIGINT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            granted_by_tg_id BIGINT,
            display_label TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            """CREATE TABLE IF NOT EXISTS training_bot_access_requests (
            id SERIAL PRIMARY KEY,
            requester_tg_id BIGINT NOT NULL,
            requester_label TEXT,
            status VARCHAR(24) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW()
        )""",
            "ALTER TABLE training_bot_operators ADD COLUMN IF NOT EXISTS telegram_id BIGINT",
            "ALTER TABLE training_bot_operators ADD COLUMN IF NOT EXISTS granted_by_tg_id BIGINT",
            "ALTER TABLE training_bot_operators ADD COLUMN IF NOT EXISTS display_label TEXT",
            """UPDATE training_bot_operators o SET telegram_id = COALESCE(u.tg_id, u.linked_tg_id)
               FROM users u WHERE u.id = o.user_id AND o.telegram_id IS NULL""",
            "DELETE FROM training_bot_operators WHERE telegram_id IS NULL",
            """DO $$
               BEGIN
                 IF EXISTS (
                   SELECT 1 FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                   WHERE tc.table_schema = 'public' AND tc.table_name = 'training_bot_operators'
                     AND tc.constraint_type = 'PRIMARY KEY' AND kcu.column_name = 'user_id'
                 ) THEN
                   ALTER TABLE training_bot_operators DROP CONSTRAINT training_bot_operators_pkey;
                   ALTER TABLE training_bot_operators ALTER COLUMN user_id DROP NOT NULL;
                   ALTER TABLE training_bot_operators ADD CONSTRAINT training_bot_operators_pkey PRIMARY KEY (telegram_id);
                 END IF;
               END $$""",
            "ALTER TABLE training_bot_access_requests ADD COLUMN IF NOT EXISTS requester_label TEXT",
            "DROP INDEX IF EXISTS uq_tb_access_one_pending",
            "DROP INDEX IF EXISTS idx_tb_access_req_user_status",
            """DO $$
               BEGIN
                 IF EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'training_bot_access_requests' AND column_name = 'user_id'
                 ) THEN
                   ALTER TABLE training_bot_access_requests DROP CONSTRAINT IF EXISTS training_bot_access_requests_user_id_fkey;
                   ALTER TABLE training_bot_access_requests DROP COLUMN user_id;
                 END IF;
               END $$""",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_tb_access_one_pending ON training_bot_access_requests(requester_tg_id) WHERE status = 'pending'",
            "CREATE INDEX IF NOT EXISTS idx_tb_access_req_tg_status ON training_bot_access_requests(requester_tg_id, status)",
            "ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS from_telegram BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS retrieval_mode VARCHAR(64) NOT NULL DEFAULT 'title_first'",
            "ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS retrieval_top_k INTEGER NOT NULL DEFAULT 24",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS start_trial_claimed_at TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS start_trial_until TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS start_trial_end_notified BOOLEAN DEFAULT false",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_admin_granted BOOLEAN DEFAULT false",
            """CREATE TABLE IF NOT EXISTS subscription_events (
                id SERIAL PRIMARY KEY,
                subject_user_id INTEGER NOT NULL REFERENCES users(id),
                kind VARCHAR(32) NOT NULL,
                plan VARCHAR(20) NOT NULL,
                price NUMERIC(12,2) NOT NULL DEFAULT 0,
                valid_from TIMESTAMP,
                valid_to TIMESTAMP,
                counterparty_user_id INTEGER REFERENCES users(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX IF NOT EXISTS ix_subscription_events_subject_created ON subscription_events (subject_user_id, created_at DESC)",
            "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS referral_bonus_amount NUMERIC(12,2)",
            """CREATE TABLE IF NOT EXISTS referral_withdrawals (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                amount_rub NUMERIC(12,2) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                admin_note TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                processed_at TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS referral_promo_links (
                id SERIAL PRIMARY KEY,
                token VARCHAR(64) UNIQUE NOT NULL,
                plan_key VARCHAR(20) NOT NULL,
                period_days INTEGER NOT NULL DEFAULT 30,
                max_activations INTEGER,
                activations_count INTEGER NOT NULL DEFAULT 0,
                valid_until TIMESTAMP,
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            "ALTER TABLE admin_permissions ADD COLUMN IF NOT EXISTS can_radio_downtempo BOOLEAN NOT NULL DEFAULT false",
            """CREATE TABLE IF NOT EXISTS radio_downtempo_tracks (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                storage_name VARCHAR(255) UNIQUE NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            "ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS images_json TEXT",
            "ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS reposts_count INTEGER NOT NULL DEFAULT 0",
            """CREATE TABLE IF NOT EXISTS community_reposts (
                id SERIAL PRIMARY KEY,
                post_id INTEGER NOT NULL REFERENCES community_posts(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(post_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS user_channel_autopost (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                channel_chat_id BIGINT NOT NULL UNIQUE,
                channel_title TEXT,
                channel_username VARCHAR(255),
                autopost_enabled BOOLEAN NOT NULL DEFAULT true,
                channel_social_button_enabled BOOLEAN NOT NULL DEFAULT false,
                linked_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS channel_autopost_log (
                channel_chat_id BIGINT NOT NULL,
                message_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (channel_chat_id, message_id)
            )""",
            "ALTER TABLE user_channel_autopost ADD COLUMN IF NOT EXISTS channel_social_button_enabled BOOLEAN NOT NULL DEFAULT false",
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

        try:
            import migrate_v14

            for s in migrate_v14.STEPS:
                await database.execute(sa.text(s))
            logger.info("Chats messenger tables (migrate_v14) OK")
        except Exception as e:
            logger.warning("migrate_v14 chats tables: %s", e)

        try:
            import migrate_v15_legacy_dm_link as migrate_v15

            for s in migrate_v15.STEPS:
                await database.execute(sa.text(s))
            logger.info("Chat messages legacy_direct_message_id (migrate_v15) OK")
        except Exception as e:
            logger.warning("migrate_v15 legacy dm link: %s", e)

        try:
            import migrate_v16_profile_public_cards as migrate_v16

            for s in migrate_v16.STEPS:
                await database.execute(sa.text(s))
            logger.info("Profile public cards column (migrate_v16) OK")
        except Exception as e:
            logger.warning("migrate_v16 profile_public_cards_json: %s", e)

        try:
            import migrate_v17_in_app_notifications as migrate_v17

            for s in migrate_v17.STEPS:
                await database.execute(sa.text(s))
            logger.info("In-app notifications (migrate_v17) OK")
        except Exception as e:
            logger.warning("migrate_v17 in_app_notifications: %s", e)

        try:
            import migrate_v18_chats_group as migrate_v18

            for s in migrate_v18.STEPS:
                await database.execute(sa.text(s))
            logger.info("Chats group features (migrate_v18) OK")
        except Exception as e:
            logger.warning("migrate_v18 chats group: %s", e)

        try:
            import migrate_v19_dm_blocks as migrate_v19

            for s in migrate_v19.STEPS:
                await database.execute(sa.text(s))
            logger.info("DM blocks & auto-delete TTL (migrate_v19) OK")
        except Exception as e:
            logger.warning("migrate_v19 dm blocks: %s", e)

        try:
            import migrate_v21_referral_shop_url as migrate_v21

            for s in migrate_v21.STEPS:
                await database.execute(sa.text(s))
            logger.info("referral_shop_url column (migrate_v21) OK")
        except Exception as e:
            logger.warning("migrate_v21 referral_shop_url: %s", e)

        try:
            import migrate_v22_wellness_journal as migrate_v22

            for s in migrate_v22.STEPS:
                await database.execute(sa.text(s))
            logger.info("Wellness journal (migrate_v22) OK")
        except Exception as e:
            logger.warning("migrate_v22 wellness_journal: %s", e)

        try:
            import migrate_v23_wellness_coach_pdf as migrate_v23

            for s in migrate_v23.STEPS:
                await database.execute(sa.text(s))
            logger.info("Wellness coach PDF / pause / renewal (migrate_v23) OK")
        except Exception as e:
            logger.warning("migrate_v23 wellness_coach_pdf: %s", e)

        try:
            import migrate_v24_platform_ai_feedback as migrate_v24

            for s in migrate_v24.STEPS:
                await database.execute(sa.text(s))
            logger.info("Platform AI feedback (migrate_v24) OK")
        except Exception as e:
            logger.warning("migrate_v24 platform_ai_feedback: %s", e)

        try:
            import migrate_v25_ai_community_bot as migrate_v25

            for s in migrate_v25.STEPS:
                await database.execute(sa.text(s))
            logger.info("AI community bot settings (migrate_v25) OK")
        except Exception as e:
            logger.warning("migrate_v25 ai_community_bot: %s", e)

        try:
            import migrate_v26_ai_telegram_channel as migrate_v26

            for s in migrate_v26.STEPS:
                await database.execute(sa.text(s))
            logger.info("AI Telegram channel mirror (migrate_v26) OK")
        except Exception as e:
            logger.warning("migrate_v26 ai_telegram_channel: %s", e)

        try:
            import migrate_v27_referral_shop_partner_self as migrate_v27

            for s in migrate_v27.STEPS:
                await database.execute(sa.text(s))
            logger.info("referral_shop_partner_self (migrate_v27) OK")
        except Exception as e:
            logger.warning("migrate_v27 referral_shop_partner_self: %s", e)

        try:
            import migrate_v28_payment_admin as migrate_v28

            for s in migrate_v28.STEPS:
                await database.execute(sa.text(s))
            logger.info("Payment admin / webhook dedup (migrate_v28) OK")
        except Exception as e:
            logger.warning("migrate_v28 payment admin: %s", e)

        try:
            import migrate_v29_subscription_paid_lifetime as migrate_v29

            for s in migrate_v29.STEPS:
                await database.execute(sa.text(s))
            logger.info("subscription_paid_lifetime (migrate_v29) OK")
        except Exception as e:
            logger.warning("migrate_v29 subscription_paid_lifetime: %s", e)

        try:
            import migrate_v30_wellness_stats_confirm as migrate_v30

            for s in migrate_v30.STEPS:
                await database.execute(sa.text(s))
            logger.info("wellness stats confirm (migrate_v30) OK")
        except Exception as e:
            logger.warning("migrate_v30 wellness_stats_confirm: %s", e)

        try:
            import migrate_v31_wellness_which_after_decline as migrate_v31

            for s in migrate_v31.STEPS:
                await database.execute(sa.text(s))
            logger.info("wellness which-after-decline (migrate_v31) OK")
        except Exception as e:
            logger.warning("migrate_v31 wellness_which_after_decline: %s", e)

        try:
            from services.merge_neurofungi_ai_chats import merge_all_neurofungi_ai_personal_chats

            await merge_all_neurofungi_ai_personal_chats()
        except Exception as e:
            logger.warning("merge_neurofungi_ai_chats: %s", e)

        try:
            from services.ai_community_bot import ensure_ai_community_bot_user

            await ensure_ai_community_bot_user()
        except Exception as e:
            logger.warning("ensure_ai_community_bot_user: %s", e)

        start_scheduler()
        app.state.startup_complete = True
        logger.info("Heavy startup complete; full traffic enabled")
    except Exception as e:
        logger.critical("Heavy startup failed: %s", e, exc_info=True)
        app.state.startup_error = str(e)
