import asyncio
import logging
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
    ]
    for sql in new_columns:
        try:
            await database.execute(sql)
        except Exception as e:
            logger.warning(f"Column migration: {e}")

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

# Routers
app.include_router(public_router)
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(admin_router)
app.include_router(account_router)
app.include_router(language_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mushroomsai"}
