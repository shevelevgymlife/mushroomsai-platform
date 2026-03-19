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
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
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
