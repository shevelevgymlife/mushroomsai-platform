import logging
import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from db.database import database
from web.routes.public import router as public_router
from web.routes.auth_routes import router as auth_router
from web.routes.user import router as user_router
from web.routes.legal_routes import router as legal_router
from web.routes.admin import router as admin_router
from web.routes.account import router as account_router
from web.routes.language import router as language_router
from web.routes.webhooks import router as webhooks_router
from web.routes.seller import router as seller_router
from web.translations import TRANSLATIONS, parse_accept_language, SUPPORTED_LANGS
from services.heavy_startup import run_heavy_startup
from web.templates_utils import Jinja2Templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -------------------- MIDDLEWARE --------------------

class LanguageMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        lang = request.cookies.get("lang")
        if lang not in SUPPORTED_LANGS:
            accept = request.headers.get("accept-language", "")
            lang = parse_accept_language(accept)
        request.state.lang = lang
        request.state.t = TRANSLATIONS.get(lang, TRANSLATIONS["ru"])
        return await call_next(request)


class ProbeBlockMiddleware(BaseHTTPMiddleware):
    _WP_DIR_SEGMENTS = frozenset({"wp-admin", "wp-includes", "wp-content", "wordpress"})
    _WP_PROBE_FILES = frozenset(
        {
            "xmlrpc.php",
            "wp-login.php",
            "readme.html",
            "license.txt",
            "wlwmanifest.xml",
            "setup-config.php",
            "install.php",
        }
    )

    @classmethod
    def _is_wp_probe(cls, raw_path: str) -> bool:
        p = (raw_path or "/").lower()
        while "//" in p:
            p = p.replace("//", "/")
        if not p.startswith("/"):
            p = "/" + p
        segments = [s for s in p.split("/") if s]
        if any(s in cls._WP_DIR_SEGMENTS for s in segments):
            return True
        if "wp-json" in segments:
            return True
        if segments and segments[-1] in cls._WP_PROBE_FILES:
            return True
        return False

    async def dispatch(self, request: Request, call_next):
        if self._is_wp_probe(request.url.path):
            return Response(status_code=404)
        return await call_next(request)


_STARTUP_SKIP_PATHS = frozenset({"/health", "/healthz", "/favicon.ico", "/robots.txt", "/sitemap.xml"})


class StartupGateMiddleware(BaseHTTPMiddleware):
    @staticmethod
    def _wants_html_page(request: Request) -> bool:
        return "text/html" in (request.headers.get("accept") or "").lower()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in _STARTUP_SKIP_PATHS or path.startswith("/static") or path.startswith("/media"):
            return await call_next(request)

        if getattr(request.app.state, "startup_complete", False):
            return await call_next(request)

        if not self._wants_html_page(request):
            if path == "/":
                return Response("ok", status_code=200)
            return await call_next(request)

        return JSONResponse(
            {"detail": "starting", "retry": True},
            status_code=503,
            headers={"Retry-After": "5"},
        )


# -------------------- LIFESPAN --------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.startup_complete = False

    try:
        await database.connect()
        logger.info("DB connected")
    except Exception as e:
        logger.error("DB connection failed: %s", e)

    # Авто-миграция новых колонок
    try:
        await database.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS link_token VARCHAR(64),
            ADD COLUMN IF NOT EXISTS link_token_expires TIMESTAMP
        """)
        logger.info("DB migration: link_token columns OK")
    except Exception as e:
        logger.warning("DB migration skipped: %s", e)

    # Уведомление о деплое в Telegram
    try:
        from services.tg_notify import notify_deploy_ok
        await notify_deploy_ok()
    except Exception:
        pass

    task = asyncio.create_task(run_heavy_startup(app))

    bot_app = None
    if settings.TELEGRAM_TOKEN:
        try:
            from bot.main_bot import create_bot, setup_bot_menu
            bot_app = create_bot()
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling(drop_pending_updates=True)
            await setup_bot_menu(bot_app)
            logger.info("Main bot started")
        except Exception as e:
            logger.error("Primary bot startup error: %s", e)

    notify_bot_app = None
    if settings.NOTIFY_BOT_TOKEN:
        try:
            from bot.notify_bot import create_notify_bot
            notify_bot_app = create_notify_bot()
            await notify_bot_app.initialize()
            await notify_bot_app.start()
            await notify_bot_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Notify bot started")
        except Exception as e:
            logger.error("Notify bot startup error: %s", e)

    try:
        yield
    finally:
        for app in [bot_app, notify_bot_app]:
            if app:
                try:
                    await app.updater.stop()
                    await app.stop()
                    await app.shutdown()
                except Exception:
                    pass

        if not task.done():
            task.cancel()
            try:
                await task
            except:
                pass

        try:
            await database.disconnect()
        except:
            pass


# -------------------- APP --------------------

app = FastAPI(
    title="MushroomsAI Platform",
    version="1.0.0",
    lifespan=lifespan,
)

templates = Jinja2Templates(directory="web/templates")

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
app.add_middleware(ProbeBlockMiddleware)
app.add_middleware(StartupGateMiddleware)

# Static
app.mount("/static", StaticFiles(directory="static"), name="static")

if os.path.exists("/data"):
    app.mount("/media", StaticFiles(directory="/data"), name="media")
else:
    os.makedirs("./media", exist_ok=True)
    app.mount("/media", StaticFiles(directory="./media"), name="media")


# -------------------- ROUTES --------------------

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    """Browsers request /favicon.ico by default; redirect to SVG in static."""
    # В static должен лежать favicon.svg (в проекте он есть).
    return Response(status_code=302, headers={"Location": "/static/favicon.svg?v=1"})


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    """Минимальный robots.txt — убирает 404 в логах у поисковых ботов."""
    return Response("User-agent: *\nDisallow:\n", media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    """Простой sitemap для роботов. При необходимости можно расширить страницами сайта."""
    host = (os.getenv("SITE_URL") or "https://mushroomsai.ru").rstrip("/")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{host}/</loc>
  </url>
</urlset>
"""
    return Response(xml, media_type="application/xml")


@app.get("/health")
async def health(request: Request):
    return {
        "status": "ok",
        "service": "mushroomsai",
        "commit": (os.getenv("RENDER_GIT_COMMIT") or "")[:12],
        "ready": getattr(request.app.state, "startup_complete", False),
    }


# Routers
app.include_router(public_router)
app.include_router(auth_router)
app.include_router(legal_router)
app.include_router(user_router)
app.include_router(seller_router)
app.include_router(admin_router)
app.include_router(account_router)
app.include_router(language_router)
app.include_router(webhooks_router)
