import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from db.database import database, metadata, engine
from web.routes.public import router as public_router
from web.routes.auth_routes import router as auth_router
from web.routes.user import router as user_router
from web.routes.admin import router as admin_router
from web.routes.account import router as account_router
from web.routes.language import router as language_router
from web.routes.seller import router as seller_router
from web.translations import TRANSLATIONS, parse_accept_language, SUPPORTED_LANGS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot_app = None


class LanguageMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        lang = request.cookies.get("lang")
        if lang not in SUPPORTED_LANGS:
            accept = request.headers.get("accept-language", "")
            lang = parse_accept_language(accept)
        request.state.lang = lang
        request.state.t = TRANSLATIONS.get(lang, TRANSLATIONS["ru"])
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await database.connect()
    logger.info("Database connected")

    # Ensure persistent storage directories exist (Render Disk at /data or local ./media)
    _base = "/data" if os.path.exists("/data") else "./media"
    os.makedirs(f"{_base}/products", exist_ok=True)
    os.makedirs(f"{_base}/community", exist_ok=True)
    os.makedirs(f"{_base}/avatars", exist_ok=True)
    logger.info(f"Media dirs ready under {_base}")

    # Create tables
    try:
        metadata.create_all(engine)
        logger.info("Tables created")
    except Exception as e:
        logger.warning(f"Table creation: {e}")

    # Add new columns to existing tables if they don't exist
    new_columns = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS linked_tg_id BIGINT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS linked_google_id VARCHAR(128)",
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
        "CREATE INDEX IF NOT EXISTS idx_cggm_group_time ON community_group_messages(group_id, created_at)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS needs_tariff_choice BOOLEAN DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS marketplace_seller BOOLEAN DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_balance NUMERIC(12,2) DEFAULT 0",
        "UPDATE users SET needs_tariff_choice = false WHERE needs_tariff_choice IS NULL",
        "ALTER TABLE users ALTER COLUMN needs_tariff_choice SET DEFAULT true",
        "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS seller_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        # v7: ссылка в профиле (Instagram) + папки обучающих постов AI
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_link_label TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_link_url TEXT",
        "ALTER TABLE ai_training_posts ADD COLUMN IF NOT EXISTS folder TEXT",
    ]
    try:
        await database.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_referrals_referred_unique ON referrals(referred_id)"
        )
    except Exception as e:
        logger.warning(f"referrals unique index: {e}")
    for sql in new_columns:
        try:
            await database.execute(sql)
        except Exception as e:
            logger.warning(f"Column migration: {e}")

    # Dashboard blocks: seed if empty + ensure соцсеть/магазин видны с тарифа Старт
    try:
        import sqlalchemy as sa
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
                    ),
                    {"k": key, "n": name, "p": pos, "al": al},
                )
            logger.info("Seeded dashboard_blocks defaults")
        else:
            # Восстановить соцсеть/магазин, если блоки есть, но выключены (частая причина «нет ленты»)
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
    except Exception as e:
        logger.warning(f"dashboard_blocks seed: {e}")

    # Ensure default AI settings exist
    try:
        from db.models import ai_settings
        count = await database.fetch_val(
            __import__("sqlalchemy", fromlist=["select"]).select(
                __import__("sqlalchemy", fromlist=["func"]).func.count()
            ).select_from(ai_settings)
        )
        if not count:
            from ai.system_prompt import DEFAULT_SYSTEM_PROMPT
            await database.execute(ai_settings.insert().values(system_prompt=DEFAULT_SYSTEM_PROMPT))
    except Exception as e:
        logger.warning(f"AI settings init: {e}")

    # Start Telegram bot
    global bot_app
    if settings.TELEGRAM_TOKEN:
        try:
            from bot.main_bot import create_bot
            bot_app = create_bot()
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot started")

            # Start scheduler
            from services.scheduler import start_scheduler
            start_scheduler(bot_app.bot)
        except Exception as e:
            logger.error(f"Bot startup error: {e}")

    yield

    # Shutdown
    if bot_app:
        try:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
        except Exception as e:
            logger.error(f"Bot shutdown error: {e}")

    await database.disconnect()
    logger.info("Database disconnected")


app = FastAPI(
    title="MushroomsAI Platform",
    description="AI-платформа по функциональным грибам",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.JWT_SECRET,
    max_age=3600,
    https_only=False,
    same_site="lax",
)
app.add_middleware(LanguageMiddleware)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Persistent media (Render Disk at /data, fallback to ./media locally)
if os.path.exists("/data"):
    app.mount("/media", StaticFiles(directory="/data"), name="media")
else:
    os.makedirs("./media", exist_ok=True)
    app.mount("/media", StaticFiles(directory="./media"), name="media")

# Routers
app.include_router(public_router)
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(seller_router)
app.include_router(admin_router)
app.include_router(account_router)
app.include_router(language_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mushroomsai"}
