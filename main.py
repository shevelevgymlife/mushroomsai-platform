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

        html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="6">
<title>NEUROFUNGI AI — обновление</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:#0a0c14;
  color:#e8e8e8;
  min-height:100vh;
  overflow:hidden;
  position:relative;
  display:flex;align-items:center;justify-content:center;
}
/* Имитация фона главной страницы */
.bg{
  position:fixed;inset:0;z-index:0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 20%, rgba(61,212,224,.18) 0%, transparent 60%),
    radial-gradient(ellipse 60% 80% at 80% 80%, rgba(200,168,75,.13) 0%, transparent 60%),
    radial-gradient(ellipse 50% 50% at 50% 50%, rgba(61,212,224,.06) 0%, transparent 70%),
    #0a0c14;
}
/* Сетка-паттерн */
.bg::before{
  content:'';position:absolute;inset:0;
  background-image:linear-gradient(rgba(61,212,224,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(61,212,224,.04) 1px,transparent 1px);
  background-size:48px 48px;
}
/* Размытие поверх */
.blur-overlay{
  position:fixed;inset:0;z-index:1;
  backdrop-filter:blur(2px);
  background:rgba(10,12,20,.55);
}
/* Карточка по центру */
.card{
  position:relative;z-index:2;
  background:rgba(255,255,255,.04);
  border:1px solid rgba(61,212,224,.2);
  border-radius:24px;
  padding:48px 40px;
  max-width:420px;width:100%;
  text-align:center;
  box-shadow:0 0 60px rgba(61,212,224,.08),0 24px 80px rgba(0,0,0,.5);
  backdrop-filter:blur(20px);
}
.logo{
  width:68px;height:68px;border-radius:20px;
  background:linear-gradient(135deg,#c8a84b,#e8c96d);
  display:flex;align-items:center;justify-content:center;
  font-size:30px;font-weight:800;color:#0a0c14;
  margin:0 auto 24px;
  box-shadow:0 8px 32px rgba(200,168,75,.35);
}
.brand{font-size:13px;font-weight:700;letter-spacing:.15em;color:#3dd4e0;opacity:.7;margin-bottom:20px;text-transform:uppercase}
h1{font-size:24px;font-weight:700;color:#e8e8e8;margin-bottom:12px;line-height:1.3}
p{font-size:14px;color:#888;margin-bottom:36px;line-height:1.6}
.dots{display:flex;gap:10px;justify-content:center}
.dot{width:10px;height:10px;border-radius:50%;background:#3dd4e0;animation:pulse 1.5s ease-in-out infinite}
.dot:nth-child(2){animation-delay:.25s;background:#c8a84b}
.dot:nth-child(3){animation-delay:.5s}
@keyframes pulse{0%,80%,100%{opacity:.15;transform:scale(.7)}40%{opacity:1;transform:scale(1)}}
.progress{
  margin-top:28px;height:2px;border-radius:2px;
  background:rgba(255,255,255,.06);overflow:hidden;
}
.progress-bar{
  height:100%;width:0%;
  background:linear-gradient(90deg,#3dd4e0,#c8a84b);
  border-radius:2px;
  animation:progress 6s linear forwards;
}
@keyframes progress{0%{width:0%}90%{width:88%}100%{width:88%}}
</style>
</head>
<body>
<div class="bg"></div>
<div class="blur-overlay"></div>
<div class="card">
  <div class="logo">N</div>
  <div class="brand">NEUROFUNGI AI</div>
  <h1>Приложение обновляется</h1>
  <p>Устанавливаем обновления. Страница<br>обновится автоматически.</p>
  <div class="dots">
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
  </div>
  <div class="progress"><div class="progress-bar"></div></div>
</div>
</body>
</html>"""
        return Response(
            html,
            status_code=503,
            media_type="text/html",
            headers={"Retry-After": "6"},
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

    try:
        await database.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS screen_rim_json TEXT")
        logger.info("DB migration: screen_rim_json OK")
    except Exception as e:
        logger.warning("DB migration screen_rim_json: %s", e)

    try:
        await database.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS link_merge_secondary_id INTEGER"
        )
        logger.info("DB migration: link_merge_secondary_id OK")
    except Exception as e:
        logger.warning("DB migration link_merge_secondary_id: %s", e)

    try:
        await database.execute(
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS image_urls_json TEXT"
        )
        await database.execute(
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS brand_name TEXT"
        )
        await database.execute(
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS price_old INTEGER"
        )
        await database.execute(
            "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS verified_personal BOOLEAN DEFAULT false"
        )
        logger.info("DB migration: shop_products catalog columns OK")
    except Exception as e:
        logger.warning("DB migration shop_products catalog: %s", e)

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

    from bot.channel_ingest_bot import create_channel_ingest_bot, parse_allowed_channel_ids, register_channel_ingest_on_app

    allowed_channel_ids = parse_allowed_channel_ids()
    train_tok = (settings.TRAINING_BOT_TOKEN or "").strip()
    ch_tok = (settings.CHANNEL_INGEST_BOT_TOKEN or "").strip()
    # Один токен: приём канала на том же polling, что бот обучения (без второго CHANNEL_INGEST_BOT_TOKEN).
    unified_channel = bool(allowed_channel_ids) and bool(train_tok) and (not ch_tok or ch_tok == train_tok)
    separate_channel = bool(allowed_channel_ids) and bool(ch_tok) and bool(train_tok) and ch_tok != train_tok
    channel_only = bool(allowed_channel_ids) and bool(ch_tok) and not train_tok

    training_bot_app = None
    if train_tok:
        try:
            from bot.training_bot import create_training_bot

            training_bot_app = create_training_bot()
            if unified_channel:
                register_channel_ingest_on_app(training_bot_app, allowed_channel_ids)
                logger.info(
                    "Channel ingest: обработчик канала на боте обучающих постов (TRAINING_BOT_TOKEN; без отдельного polling)"
                )
            await training_bot_app.initialize()
            await training_bot_app.start()
            await training_bot_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Training posts bot started (polling; token from TRAINING_BOT_TOKEN)")
        except Exception:
            logger.exception("Training bot startup failed — проверьте TRAINING_BOT_TOKEN и что нет второго процесса с тем же polling")
    else:
        logger.info("Training posts bot: выключен (в Environment задайте TRAINING_BOT_TOKEN и сделайте redeploy)")

    channel_ingest_app = None
    if separate_channel or channel_only:
        try:
            from telegram import Update as TgUpdate

            channel_ingest_app = create_channel_ingest_bot()
            await channel_ingest_app.initialize()
            await channel_ingest_app.start()
            await channel_ingest_app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=list(TgUpdate.ALL_TYPES),
            )
            logger.info("Channel ingest: отдельный polling (другой токен, чем TRAINING_BOT_TOKEN)")
        except Exception:
            logger.exception("Channel ingest bot startup failed — токен, allowed ids или второй polling")
    elif allowed_channel_ids and not train_tok and not ch_tok:
        logger.warning(
            "CHANNEL_INGEST_ALLOWED_IDS заданы, но нет TRAINING_BOT_TOKEN и CHANNEL_INGEST_BOT_TOKEN — приём канала не запущен"
        )

    try:
        yield
    finally:
        for app in [bot_app, notify_bot_app, training_bot_app, channel_ingest_app]:
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
    title="NEUROFUNGI AI Platform",
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
