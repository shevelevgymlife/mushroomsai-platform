import logging
import os

logging.basicConfig(level=logging.INFO)

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
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
from web.routes.seller import router as seller_router
from web.translations import TRANSLATIONS, parse_accept_language, SUPPORTED_LANGS
from services.heavy_startup import run_heavy_startup
from web.templates_utils import Jinja2Templates

logger = logging.getLogger(__name__)


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
    """Ранний 404 для массовых сканов WordPress — меньше работы, чем через сессии и роутеры."""

    _WP_DIR_SEGMENTS = frozenset({"wp-admin", "wp-includes", "wp-content", "wordpress"})
    _WP_PROBE_FILES = frozenset(
        {"xmlrpc.php", "wp-login.php", "readme.html", "license.txt", "wlwmanifest.xml"}
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


_STARTUP_SKIP_PATHS = frozenset({"/health", "/healthz", "/favicon.ico", "/robots.txt"})


class StartupGateMiddleware(BaseHTTPMiddleware):
    """Пока идёт тяжёлый старт в фоне: /health сразу 200; HTML-запросы к сайту — 503; пробы без text/html — 200 ok."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _STARTUP_SKIP_PATHS or path.startswith("/static") or path.startswith("/media"):
            return await call_next(request)
        if getattr(request.app.state, "startup_complete", False):
            return await call_next(request)
        if path == "/":
            accept = (request.headers.get("accept") or "").lower()
            if "text/html" in accept:
                return JSONResponse(
                    {"detail": "starting", "retry": True},
                    status_code=503,
                    headers={"Retry-After": "5"},
                )
            return Response("ok", status_code=200, media_type="text/plain")
        return JSONResponse(
            {"detail": "starting", "retry": True},
            status_code=503,
            headers={"Retry-After": "5"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.startup_complete = False
    startup_task: asyncio.Task | None = None
    try:
        await database.connect()
    except Exception as e:
        logger.critical(
            "Старт прерван: не удалось подключиться к PostgreSQL. "
            "Проверь DATABASE_URL (Internal URL базы на Render), статус Postgres «Available», "
            "что URL не обрезан и без лишних кавычек. Детали: %s",
            e,
            exc_info=True,
        )
        raise
    logger.info("DB connected; heavy migrations run in background (HTTP accepts probes)")
    startup_task = asyncio.create_task(run_heavy_startup(app))
    try:
        yield
    finally:
        if startup_task is not None and not startup_task.done():
            startup_task.cancel()
            try:
                await startup_task
            except asyncio.CancelledError:
                pass
        await database.disconnect()
        logger.info("Database disconnected")


app = FastAPI(
    title="MushroomsAI Platform",
    description="AI-платформа по функциональным грибам",
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

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    """Browsers request /favicon.ico by default; redirect to SVG in static."""
    return RedirectResponse(url="/static/favicon.svg?v=1", status_code=302)


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    """Минимальный robots.txt — убирает 404 в логах для поисковых ботов."""
    return Response("User-agent: *\nDisallow:\n", media_type="text/plain")

# Persistent media (Render Disk at /data, fallback to ./media locally)
if os.path.exists("/data"):
    app.mount("/media", StaticFiles(directory="/data"), name="media")
else:
    os.makedirs("./media", exist_ok=True)
    app.mount("/media", StaticFiles(directory="./media"), name="media")

# Routers
app.include_router(public_router)
app.include_router(auth_router)
app.include_router(legal_router)
app.include_router(user_router)
app.include_router(seller_router)
app.include_router(admin_router)
app.include_router(account_router)
app.include_router(language_router)


def _wants_html(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept or "*/*" in accept


def _updating_response(request: Request, status_code: int = 503):
    if _wants_html(request):
        return templates.TemplateResponse(
            "deploy_updating.html",
            {"request": request},
            status_code=status_code,
        )
    return JSONResponse(
        {
            "ok": False,
            "updating": True,
            "message": "PROJECT UPDATING. 1 min.",
        },
        status_code=status_code,
    )


@app.get("/updating")
async def updating_page(request: Request):
    return _updating_response(request, status_code=200)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (502, 503, 504):
        return _updating_response(request, status_code=503)
    if _wants_html(request):
        return HTMLResponse(str(exc.detail or "Error"), status_code=exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error: %s", exc)
    return _updating_response(request, status_code=503)


@app.get("/health")
async def health(request: Request):
    return {
        "status": "ok",
        "service": "mushroomsai",
        "ready": getattr(request.app.state, "startup_complete", False),
    }
