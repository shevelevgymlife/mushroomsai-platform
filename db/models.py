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
    sqlalchemy.Column("referral_code", sqlalchemy.String(20), unique=True, nullable=True),
    sqlalchemy.Column("referred_by", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("linked_tg_id", sqlalchemy.BigInteger, nullable=True),
    sqlalchemy.Column("linked_google_id", sqlalchemy.String(128), nullable=True),
    sqlalchemy.Column("primary_user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("daily_questions", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("daily_recipes", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("last_reset", sqlalchemy.Date, nullable=True),
    sqlalchemy.Column("bio", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("profile_link_label", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("profile_link_url", sqlalchemy.Text, nullable=True),
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
    sqlalchemy.Column("referral_balance", sqlalchemy.Numeric(12, 2), default=0, server_default="0"),
    sqlalchemy.Column("last_seen_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("decimal_del_balance", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("decimal_balance_cached_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("shevelev_balance_cached", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("shevelev_balance_cached_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("show_del_to_public", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("show_shev_to_public", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("legal_accepted_at", sqlalchemy.DateTime, nullable=True),
    sqlalchemy.Column("legal_docs_version", sqlalchemy.Text, nullable=True),
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
    sqlalchemy.Column("folder_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_folders.id"), nullable=True),
    sqlalchemy.Column("likes_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("comments_count", sqlalchemy.Integer, default=0, server_default="0"),
    sqlalchemy.Column("saves_count", sqlalchemy.Integer, default=0, server_default="0"),
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

admin_permissions = sqlalchemy.Table(
    "admin_permissions",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id"), unique=True),
    sqlalchemy.Column("can_dashboard", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_ai", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_shop", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_users", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_feedback", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_broadcast", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("can_knowledge", sqlalchemy.Boolean, default=False, server_default="false"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

# Ключ-значение (value = JSON-строка): group_creation_policy и др.
platform_settings = sqlalchemy.Table(
    "platform_settings",
    metadata,
    sqlalchemy.Column("key", sqlalchemy.String(128), primary_key=True),
    sqlalchemy.Column("value", sqlalchemy.Text, nullable=False, server_default=""),
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
    sqlalchemy.UniqueConstraint("group_id", "user_id", name="uq_community_group_member"),
)

community_group_messages = sqlalchemy.Table(
    "community_group_messages",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("group_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_groups.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("sender_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("reply_to_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("community_group_messages.id", ondelete="SET NULL"), nullable=True),
    sqlalchemy.Column("text", sqlalchemy.Text, nullable=False),
    sqlalchemy.Column("image_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("audio_url", sqlalchemy.Text, nullable=True),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
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
    sqlalchemy.Column("is_active", sqlalchemy.Boolean, default=True, server_default="true"),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
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
