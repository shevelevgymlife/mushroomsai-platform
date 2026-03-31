import sqlalchemy
from db.database import metadata

users = sqlalchemy.Table(
    "users",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("tg_id", sqlalchemy.BigInteger, unique=True, nullable=True),
    sqlalchemy.Column("google_id", sqlalchemy.String(128), unique=True, nullable=True),
    sqlalchemy.Column("email", sqlalchemy.String(255), unique=True, nullable=True),
    sqlalchemy.Column("password_hash", sqlalchemy.String(255), nullable=True),
    sqlalchemy.Column("name", sqlalchemy.String(255), nullable=True),
    sqlalchemy.Column("avatar", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("role", sqlalchemy.String(20), default="user", server_default="user"),
    sqlalchemy.Column("language", sqlalchemy.String(10), default="ru", server_default="ru"),
    sqlalchemy.Column("subscription_plan", sqlalchemy.String(20), default="free", server_default="free"),
    sqlalchemy.Column("subscription_end", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("subscription_admin_granted", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("referral_code", sqlalchemy.String(20), unique=True, nullable=True),
    sqlalchemy.Column("referred_by", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("linked_tg_id", sqlalchemy.BigInteger, nullable=True),
    sqlalchemy.Column("linked_google_id", sqlalchemy.String(128), nullable=True),
    sqlalchemy.Column("apple_id", sqlalchemy.String(128), unique=True, nullable=True),
    sqlalchemy.Column("linked_apple_id", sqlalchemy.String(128), nullable=True),
    sqlalchemy.Column("primary_user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("daily_questions", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("daily_recipes", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("last_reset", sqlalchemy.Date, nullable=True),
    sqlalchemy.Column("bio", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("profile_link_label", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("profile_link_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("profile_thoughts", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("profile_thoughts_font", sqlalchemy.String(80), nullable=True),
    sqlalchemy.Column("profile_thoughts_color", sqlalchemy.String(16), nullable=True),
    sqlalchemy.Column("followers_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("following_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("wallet_address", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("violations_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("is_banned", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("ban_until", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("ban_reason", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.Column("needs_tariff_choice", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("marketplace_seller", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("referral_shop_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column(
        "referral_shop_partner_self",
        sqlalchemy.Boolean,
        nullable=False,
        server_default="false",
    ),
    sqlalchemy.Column("referral_balance", sqlalchemy.Numeric(12, 2), default=0, server_default="0"),
    sqlalchemy.Column("last_seen_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("decimal_del_balance", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("decimal_balance_cached_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("shevelev_balance_cached", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("shevelev_balance_cached_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("show_del_to_public", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("show_shev_to_public", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("token_lamp_enabled", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("legal_accepted_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("legal_docs_version", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("link_token", sqlalchemy.String(64), nullable=True),
    sqlalchemy.Column("link_token_expires", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("link_merge_secondary_id", sqlalchemy.Integer, nullable=True),
    sqlalchemy.Column("screen_rim_json", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("profile_ui_theme", sqlalchemy.String(64), nullable=True),
    sqlalchemy.Column("profile_public_cards_json", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("start_trial_claimed_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("start_trial_until", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("start_trial_end_notified", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("music_player_enabled", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("music_player_position", sqlalchemy.String(50), default="bottom-right", server_default="bottom-right"),
    sqlalchemy.Column("music_player_volume", sqlalchemy.Float, default=0.5, server_default="0.5"),
    sqlalchemy.Column("notification_prefs_json", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("wellness_journal_interval_days", sqlalchemy.Integer, default=1, server_default="1"),
    sqlalchemy.Column("wellness_journal_opt_out", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("wellness_journal_admin_paused", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("wellness_last_prompt_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("wellness_next_prompt_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("wellness_baseline_json", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("wellness_weekly_digest_last_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("wellness_journal_pdf_allowed", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("wellness_renewal_nudge_for_end", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("wellness_coach_pause_until", sqlalchemy.DateTime, nullable=True),
)

sessions = sqlalchemy.Table(
    "sessions",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("token", sqlalchemy.String(512), unique=True, nullable=False),
    sqlalchemy.Column("expires_at", sqlalchemy.DateTime, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

messages = sqlalchemy.Table(
    "messages",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("session_key", sqlalchemy.String(64), nullable=True),
    sqlalchemy.Column("role", sqlalchemy.String(20), nullable=False),
    sqlalchemy.Column("content", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

leads = sqlalchemy.Table(
    "leads",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("name", sqlalchemy.String(255), nullable=True),
    sqlalchemy.Column("phone", sqlalchemy.String(50), nullable=True),
    sqlalchemy.Column("question", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("status", sqlalchemy.String(20), default="new", server_default="new"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

products = sqlalchemy.Table(
    "products",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("name", sqlalchemy.String(255), nullable=False),
    sqlalchemy.Column("description", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("price", sqlalchemy.Numeric(10, 2), nullable=False),
    sqlalchemy.Column("image_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("category", sqlalchemy.String(100), nullable=True),
    sqlalchemy.Column("stock", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("active", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

orders = sqlalchemy.Table(
    "orders",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("product_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("products.id"), nullable=True),
    sqlalchemy.Column("amount", sqlalchemy.Numeric(10, 2), nullable=False),
    sqlalchemy.Column("status", sqlalchemy.String(20), default="pending", server_default="pending"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

posts = sqlalchemy.Table(
    "posts",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("content", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("likes", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("approved", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

post_likes = sqlalchemy.Table(
    "post_likes",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("post_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("posts.id"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
)

referrals = sqlalchemy.Table(
    "referrals",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("referrer_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("referred_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("bonus_applied", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("referral_bonus_amount", sqlalchemy.Numeric(12, 2), nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

referral_withdrawals = sqlalchemy.Table(
    "referral_withdrawals",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("amount_rub", sqlalchemy.Numeric(12, 2), nullable=False),
    sqlalchemy.Column("status", sqlalchemy.String(20), default="pending", server_default="pending"),
    sqlalchemy.Column("admin_note", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.Column("processed_at", sqlalchemy.DateTime, nullable=True),
)

referral_promo_links = sqlalchemy.Table(
    "referral_promo_links",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("token", sqlalchemy.String(64), unique=True, nullable=False),
    sqlalchemy.Column("plan_key", sqlalchemy.String(20), nullable=False),
    sqlalchemy.Column("period_days", sqlalchemy.Integer, nullable=False, server_default="30"),
    sqlalchemy.Column("max_activations", sqlalchemy.Integer, nullable=True),
    sqlalchemy.Column("activations_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("valid_until", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("created_by", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

followups = sqlalchemy.Table(
    "followups",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("scheduled_at", sqlalchemy.DateTime, nullable=False),
    sqlalchemy.Column("message", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("sent", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

pdf_protocols = sqlalchemy.Table(
    "pdf_protocols",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("name", sqlalchemy.String(255), nullable=False),
    sqlalchemy.Column("description", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("price", sqlalchemy.Numeric(10, 2), nullable=False),
    sqlalchemy.Column("file_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("category", sqlalchemy.String(100), nullable=True),
)

subscriptions = sqlalchemy.Table(
    "subscriptions",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("plan", sqlalchemy.String(20), nullable=False),
    sqlalchemy.Column("price", sqlalchemy.Numeric(10, 2), nullable=False),
    sqlalchemy.Column("start_date", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.Column("end_date", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("active", sqlalchemy.Boolean, default=True, server_default="true"),
)

subscription_events = sqlalchemy.Table(
    "subscription_events",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("subject_user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("kind", sqlalchemy.String(32), nullable=False),
    sqlalchemy.Column("plan", sqlalchemy.String(20), nullable=False),
    sqlalchemy.Column("price", sqlalchemy.Numeric(12, 2), nullable=False, server_default="0"),
    sqlalchemy.Column("valid_from", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("valid_to", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("counterparty_user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

page_views = sqlalchemy.Table(
    "page_views",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("path", sqlalchemy.String(512), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

ai_settings = sqlalchemy.Table(
    "ai_settings",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("system_prompt", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("retrieval_mode", sqlalchemy.String(64), nullable=False, server_default="title_first"),
    sqlalchemy.Column("retrieval_top_k", sqlalchemy.Integer, nullable=False, server_default="24"),
    sqlalchemy.Column("updated_by", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("updated_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

knowledge_base = sqlalchemy.Table(
    "knowledge_base",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("title", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("content", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("category", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("source_file", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

shop_products = sqlalchemy.Table(
    "shop_products",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("seller_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("name", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("description", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("price", sqlalchemy.Integer, nullable=True),
    sqlalchemy.Column("url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("mushroom_type", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("image_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("image_urls_json", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("brand_name", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("price_old", sqlalchemy.Integer, nullable=True),
    sqlalchemy.Column("verified_personal", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("category", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("in_stock", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

product_reviews = sqlalchemy.Table(
    "product_reviews",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("product_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("shop_products.id"), nullable=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("rating", sqlalchemy.Integer, nullable=False),
    sqlalchemy.Column("text", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

shop_product_likes = sqlalchemy.Table(
    "shop_product_likes",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("product_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("shop_products.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.UniqueConstraint("user_id", "product_id", name="uq_shop_product_like_user"),
)

shop_product_comments = sqlalchemy.Table(
    "shop_product_comments",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("product_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("shop_products.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("content", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

product_questions = sqlalchemy.Table(
    "product_questions",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("product_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("shop_products.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("question_text", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("answer_text", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("answered_by", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("answered_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

shop_cart_items = sqlalchemy.Table(
    "shop_cart_items",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("product_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("shop_products.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("quantity", sqlalchemy.Integer, nullable=False, server_default="1"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.UniqueConstraint("user_id", "product_id", name="uq_shop_cart_user_product"),
)

shop_market_orders = sqlalchemy.Table(
    "shop_market_orders",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("status", sqlalchemy.String(32), default="new", server_default="new"),
    sqlalchemy.Column("delivery_address", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("delivery_city", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("delivery_phone", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("delivery_comment", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("total_amount", sqlalchemy.Integer, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

shop_market_order_items = sqlalchemy.Table(
    "shop_market_order_items",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("order_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("shop_market_orders.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("product_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("shop_products.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("quantity", sqlalchemy.Integer, nullable=False, server_default="1"),
    sqlalchemy.Column("unit_price", sqlalchemy.Integer, nullable=True),
)

support_message_deliveries = sqlalchemy.Table(
    "support_message_deliveries",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("admin_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("recipient_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("feedback_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("feedback.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("message_preview", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("in_app_delivered", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("telegram_attempted", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("telegram_ok", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("user_was_online", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

community_folders = sqlalchemy.Table(
    "community_folders",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("name", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

community_posts = sqlalchemy.Table(
    "community_posts",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("title", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("content", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("image_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("images_json", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column(
        "from_telegram",
        sqlalchemy.Boolean,
        default=False,
        server_default="false",
    ),
    sqlalchemy.Column("folder_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_folders.id"), nullable=True),
    sqlalchemy.Column("likes_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("comments_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("saves_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("reposts_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("tags", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("pinned", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("approved", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

community_comments = sqlalchemy.Table(
    "community_comments",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("post_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_posts.id"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("content", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column(
        "seen_by_post_owner",
        sqlalchemy.Boolean,
        default=True,
        server_default="true",
    ),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

community_likes = sqlalchemy.Table(
    "community_likes",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("post_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_posts.id"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column(
        "seen_by_post_owner",
        sqlalchemy.Boolean,
        default=True,
        server_default="true",
    ),
)

feedback = sqlalchemy.Table(
    "feedback",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("message", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("status", sqlalchemy.String(20), default="new", server_default="new"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

training_bot_operators = sqlalchemy.Table(
    "training_bot_operators",
    metadata,
    sqlalchemy.Column("telegram_id", sqlalchemy.BigInteger, primary_key=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("granted_by", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("granted_by_tg_id", sqlalchemy.BigInteger, nullable=True),
    sqlalchemy.Column("display_label", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

training_bot_access_requests = sqlalchemy.Table(
    "training_bot_access_requests",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("requester_tg_id", sqlalchemy.BigInteger, nullable=False),
    sqlalchemy.Column("requester_label", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("status", sqlalchemy.String(24), nullable=False, server_default="pending"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

admin_permissions = sqlalchemy.Table(
    "admin_permissions",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), unique=True),
    sqlalchemy.Column("can_dashboard", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_ai", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_ai_posts", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_shop", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_payment", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_users", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_feedback", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_broadcast", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_knowledge", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_community", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_groups", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_homepage", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_dashboard_blocks", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_radio_downtempo", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_training_bot", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_ai_unlimited", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

# Плейлист радио Down Tempo (файлы в media/radio/downtempo/)
radio_downtempo_tracks = sqlalchemy.Table(
    "radio_downtempo_tracks",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("title", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("storage_name", sqlalchemy.String(255), unique=True, nullable=False),
    sqlalchemy.Column("sort_order", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

# Ключ-значение (value = JSON-строка): group_creation_policy и др.
platform_settings = sqlalchemy.Table(
    "platform_settings",
    metadata,
    sqlalchemy.Column("key", sqlalchemy.String(128), primary_key=True),
    sqlalchemy.Column("value", sqlalchemy.Text, nullable=False, server_default=""),
)

# Идемпотентность вебхуков оплаты (provider + external_id транзакции)
payment_webhook_dedup = sqlalchemy.Table(
    "payment_webhook_dedup",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("provider", sqlalchemy.String(32), nullable=False),
    sqlalchemy.Column("external_id", sqlalchemy.String(128), nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.UniqueConstraint("provider", "external_id", name="uq_payment_webhook_dedup"),
)

community_follows = sqlalchemy.Table(
    "community_follows",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("follower_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("following_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

community_saved = sqlalchemy.Table(
    "community_saved",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("post_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

community_reposts = sqlalchemy.Table(
    "community_reposts",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("post_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.UniqueConstraint("post_id", "user_id", name="uq_community_reposts_post_user"),
)

community_groups = sqlalchemy.Table(
    "community_groups",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("name", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("description", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_by", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.Column("join_mode", sqlalchemy.String(20), default="approval", server_default="approval"),
    sqlalchemy.Column("message_retention_days", sqlalchemy.Integer, nullable=True),
    sqlalchemy.Column("slow_mode_seconds", sqlalchemy.Integer, nullable=True),
    sqlalchemy.Column("show_history_to_new_members", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("image_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("allow_photo", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("allow_audio", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("auto_delete_enabled", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("pinned_message_text", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("pinned_message_updated_at", sqlalchemy.DateTime, nullable=True),
)

community_group_join_requests = sqlalchemy.Table(
    "community_group_join_requests",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("group_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_groups.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("status", sqlalchemy.String(20), default="pending", server_default="pending"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.UniqueConstraint("group_id", "user_id", name="uq_cg_join_req"),
)

community_group_members = sqlalchemy.Table(
    "community_group_members",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("group_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_groups.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("joined_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.Column("last_read_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("chat_last_seen_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("addressed_last_read_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("notifications_enabled", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.UniqueConstraint("group_id", "user_id", name="uq_community_group_member"),
)

community_group_messages = sqlalchemy.Table(
    "community_group_messages",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("group_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_groups.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("sender_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("reply_to_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_group_messages.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("addressed_user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("text", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("image_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("audio_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

community_group_member_permissions = sqlalchemy.Table(
    "community_group_member_permissions",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("group_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_groups.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("can_send_messages", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("can_send_photo", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("can_send_audio", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("updated_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.UniqueConstraint("group_id", "user_id", name="uq_cg_member_perms"),
)

community_group_member_bans = sqlalchemy.Table(
    "community_group_member_bans",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("group_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_groups.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("banned_by", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("reason", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("banned_until", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("is_permanent", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.UniqueConstraint("group_id", "user_id", name="uq_cg_member_ban"),
)

community_group_typing_status = sqlalchemy.Table(
    "community_group_typing_status",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("group_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_groups.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("updated_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now(), nullable=False),
    sqlalchemy.UniqueConstraint("group_id", "user_id", name="uq_cg_typing"),
)

community_group_message_likes = sqlalchemy.Table(
    "community_group_message_likes",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("message_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_group_messages.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.UniqueConstraint("message_id", "user_id", name="uq_cgm_like_user"),
)

community_messages = sqlalchemy.Table(
    "community_messages",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("sender_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("recipient_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("text", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("is_read", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

profile_likes = sqlalchemy.Table(
    "profile_likes",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("liked_user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column(
        "seen_by_owner",
        sqlalchemy.Boolean,
        default=True,
        server_default="true",
    ),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

community_profiles = sqlalchemy.Table(
    "community_profiles",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), unique=True, nullable=False),
    sqlalchemy.Column("display_name", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("bio", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("is_public", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("posts_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("followers_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("following_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

direct_messages = sqlalchemy.Table(
    "direct_messages",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("sender_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("recipient_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("text", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("is_read", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("is_system", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

wellness_journal_entries = sqlalchemy.Table(
    "wellness_journal_entries",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("role", sqlalchemy.String(24), nullable=False),
    sqlalchemy.Column("raw_text", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("extracted_json", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("direct_message_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("direct_messages.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

platform_ai_feedback = sqlalchemy.Table(
    "platform_ai_feedback",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("user_role", sqlalchemy.String(20), nullable=False, server_default="user"),
    sqlalchemy.Column("raw_text", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("source", sqlalchemy.String(48), nullable=True),
    sqlalchemy.Column("admin_reply", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("admin_reply_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

ai_community_bot_settings = sqlalchemy.Table(
    "ai_community_bot_settings",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("master_enabled", sqlalchemy.Boolean, nullable=False, server_default="true"),
    sqlalchemy.Column("allow_posts", sqlalchemy.Boolean, nullable=False, server_default="true"),
    sqlalchemy.Column("allow_comments", sqlalchemy.Boolean, nullable=False, server_default="true"),
    sqlalchemy.Column("allow_follow", sqlalchemy.Boolean, nullable=False, server_default="true"),
    sqlalchemy.Column("allow_unfollow", sqlalchemy.Boolean, nullable=False, server_default="true"),
    sqlalchemy.Column("allow_reply_to_comments", sqlalchemy.Boolean, nullable=False, server_default="true"),
    sqlalchemy.Column("allow_profile_thoughts", sqlalchemy.Boolean, nullable=False, server_default="true"),
    sqlalchemy.Column("allow_photos", sqlalchemy.Boolean, nullable=False, server_default="false"),
    sqlalchemy.Column("allow_story_posts", sqlalchemy.Boolean, nullable=False, server_default="true"),
    sqlalchemy.Column("allow_bug_reports", sqlalchemy.Boolean, nullable=False, server_default="true"),
    sqlalchemy.Column("limit_posts_per_day", sqlalchemy.Integer, nullable=False, server_default="5"),
    sqlalchemy.Column("limit_comments_per_day", sqlalchemy.Integer, nullable=False, server_default="30"),
    sqlalchemy.Column("limit_follows_per_day", sqlalchemy.Integer, nullable=False, server_default="15"),
    sqlalchemy.Column("limit_unfollows_per_day", sqlalchemy.Integer, nullable=False, server_default="10"),
    sqlalchemy.Column("limit_thoughts_per_day", sqlalchemy.Integer, nullable=False, server_default="15"),
    sqlalchemy.Column("limit_reply_comments_per_day", sqlalchemy.Integer, nullable=False, server_default="25"),
    sqlalchemy.Column("thoughts_count_date", sqlalchemy.Date, nullable=True),
    sqlalchemy.Column("thoughts_count_today", sqlalchemy.Integer, nullable=True),
    sqlalchemy.Column(
        "allow_telegram_channel",
        sqlalchemy.Boolean,
        nullable=False,
        server_default="true",
    ),
    sqlalchemy.Column("last_tick_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("updated_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

moderation_log = sqlalchemy.Table(
    "moderation_log",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("content_type", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("content_text", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("reason", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("action_taken", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

blocked_identities = sqlalchemy.Table(
    "blocked_identities",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("id_type", sqlalchemy.String(32), nullable=False),
    sqlalchemy.Column("id_value", sqlalchemy.String(512), nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.UniqueConstraint("id_type", "id_value", name="uq_blocked_identities_type_value"),
)

pending_google_links = sqlalchemy.Table(
    "pending_google_links",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("token", sqlalchemy.String(64), unique=True, nullable=False),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("google_id", sqlalchemy.String(128), nullable=False),
    sqlalchemy.Column("email", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("name", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("avatar", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("expires_at", sqlalchemy.DateTime, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

ai_training_folders = sqlalchemy.Table(
    "ai_training_folders",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("name", sqlalchemy.Text, unique=True, nullable=False),
)

ai_training_posts = sqlalchemy.Table(
    "ai_training_posts",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("title", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("content", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("category", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("folder", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("image_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("is_active", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.Column("ingest_tg_chat_id", sqlalchemy.BigInteger, nullable=True),
    sqlalchemy.Column("ingest_tg_message_id", sqlalchemy.BigInteger, nullable=True),
)

homepage_blocks = sqlalchemy.Table(
    "homepage_blocks",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("block_name", sqlalchemy.Text, unique=True, nullable=False),
    sqlalchemy.Column("title", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("subtitle", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("content", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("is_visible", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("position", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("access_level", sqlalchemy.Text, default="all", server_default="all"),
    sqlalchemy.Column("custom_title", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("blur_for_guests", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("blur_text", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("updated_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

dashboard_blocks = sqlalchemy.Table(
    "dashboard_blocks",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("block_key", sqlalchemy.Text, unique=True, nullable=False),
    sqlalchemy.Column("block_name", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("is_visible", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("position", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("access_level", sqlalchemy.Text, default="all", server_default="all"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

user_block_overrides = sqlalchemy.Table(
    "user_block_overrides",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("block_key", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("is_visible", sqlalchemy.Boolean, nullable=True),
    sqlalchemy.Column("custom_name", sqlalchemy.Text, nullable=True),
)

# Автопост из личного Telegram-канала пользователя в ленту сообщества (главный бот)
user_channel_autopost = sqlalchemy.Table(
    "user_channel_autopost",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False, unique=True),
    sqlalchemy.Column("channel_chat_id", sqlalchemy.BigInteger, nullable=False, unique=True),
    sqlalchemy.Column("channel_title", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("channel_username", sqlalchemy.String(255), nullable=True),
    sqlalchemy.Column("autopost_enabled", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column(
        "channel_social_button_enabled",
        sqlalchemy.Boolean,
        default=False,
        server_default="false",
    ),
    sqlalchemy.Column("linked_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.Column("updated_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

channel_autopost_log = sqlalchemy.Table(
    "channel_autopost_log",
    metadata,
    sqlalchemy.Column("channel_chat_id", sqlalchemy.BigInteger, nullable=False),
    sqlalchemy.Column("message_id", sqlalchemy.Integer, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.PrimaryKeyConstraint("channel_chat_id", "message_id"),
)

# Внутриигровые события (лента /notifications) — не удаляются
in_app_notifications = sqlalchemy.Table(
    "in_app_notifications",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("recipient_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("actor_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("ntype", sqlalchemy.String(40), nullable=False),
    sqlalchemy.Column("title", sqlalchemy.Text, nullable=False, server_default=""),
    sqlalchemy.Column("body", sqlalchemy.Text, nullable=False, server_default=""),
    sqlalchemy.Column("link_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("source_kind", sqlalchemy.String(32), nullable=True),
    sqlalchemy.Column("source_id", sqlalchemy.Integer, nullable=True),
    sqlalchemy.Column("read_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
    sqlalchemy.Column("meta_json", sqlalchemy.Text, nullable=True),
)
