import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file_for_settings() -> str | None:
    """Локально — .env; на Render только Dashboard → Environment (без файла .env на диске)."""
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_NAME"):
        return None
    return ".env"


class Settings(BaseSettings):
    TELEGRAM_TOKEN: str = ""
    # Канал для дублей постов NeuroFungi AI (основной бот TELEGRAM_TOKEN должен быть админом). Пример: @ShevelevVlog или -100…
    NEUROFUNGI_AI_TG_CHANNEL: str = ""
    # Отдельный бот для добавления обучающих постов (тот же DATABASE_URL). Пусто — бот не стартует.
    TRAINING_BOT_TOKEN: str = ""
    # Импорт канала → ai_training_posts. Пусто = тот же токен, что TRAINING_BOT_TOKEN (один бот, один polling).
    CHANNEL_INGEST_BOT_TOKEN: str = ""
    # Список chat_id каналов (-100…) через запятую. Пусто — бот канала не стартует (безопасность).
    CHANNEL_INGEST_ALLOWED_IDS: str = ""
    # Папка для постов из канала (как в админке / боте обучения).
    # Папка обучающих постов + зеркало в ленте; можно переопределить в Environment.
    CHANNEL_INGEST_FOLDER: str = "Из канала с 26.03.26"
    # ID пользователя сайта (users.id), от имени которого публиковать пост в ленте сообщества. 0 — не публиковать.
    CHANNEL_INGEST_COMMUNITY_USER_ID: int = 0
    TELEGRAM_BOT_USERNAME: str = ""  # напр. mushroomsai_bot (без @)
    TELEGRAM_WEBAPP_SKIP_VERIFY: bool = False  # DEBUG ONLY: пропустить проверку подписи initData
    NOTIFY_BOT_TOKEN: str = ""       # отдельный бот для уведомлений админу
    GITHUB_WEBHOOK_SECRET: str = ""  # секрет для GitHub Webhooks
    OPENAI_API_KEY: str = ""
    DATABASE_URL: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    JWT_SECRET: str = "change-me-in-production"
    ADMIN_TG_ID: int = 0
    # Доп. Telegram ID (через запятую), кто подтверждает заявки в боте обучающих постов (помимо ADMIN_TG_ID и владельца).
    TRAINING_BOT_APPROVER_TG_IDS: str = ""
    # Email владельца (полный доступ в админку + sync role=admin). В Render можно переопределить env.
    ADMIN_EMAIL: str = "shevelevgymlife@gmail.com"
    # users.id аккаунта «техподдержка» для системных ЛС; 0 — взять по ADMIN_EMAIL или первого admin
    TECH_SUPPORT_USER_ID: int = 0
    # Единый аккаунт NeuroFungi AI (ЛС, дневник, системные оповещения). 0 — как TECH_SUPPORT_USER_ID / ADMIN_EMAIL / первый admin
    NEUROFUNGI_AI_USER_ID: int = 0
    DEPLOY_NOTIFY_EMAIL_TO: str = ""  # куда слать уведомление о деплое
    DEPLOY_NOTIFY_EMAIL_FROM: str = ""  # от кого слать (если пусто, используем SMTP_USER)
    DEPLOY_NOTIFY_TASK_EMAIL_TO: str = ""  # email для статусов задач (если пусто = DEPLOY_NOTIFY_EMAIL_TO)
    TASK_AUTORUN_WEBHOOK_URL: str = ""  # внешний раннер задач (optional)
    TASK_AUTORUN_WEBHOOK_TOKEN: str = ""  # секрет для заголовков webhook (optional)
    TASK_AUTORUN_SECRET: str = ""  # альтернатива токену (optional)
    OPS_NOTIFY_DAILY_SUMMARY_HOUR_UTC: int = 9  # час UTC для ежедневной сводки (email)
    OPS_NOTIFY_BILLING_DUE_AT: str = ""  # дата платежа YYYY-MM-DD (optional)
    OPS_NOTIFY_BILLING_CURRENT_USD: float = 0.0  # текущие расходы (optional)
    OPS_NOTIFY_BILLING_LIMIT_USD: float = 0.0  # лимит расходов (optional)
    OPS_NOTIFY_BILLING_WARN_PERCENT: int = 90  # порог warning по расходам
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_USE_TLS: bool = True
    SITE_URL: str = "https://mushroomsai.ru"
    SHEVELEV_TOKEN_ADDRESS: str = ""
    DECIMAL_RPC_URL: str = "https://node.decimalchain.com/web3/"

    # Telegram WebApp auth (initData verification)
    TELEGRAM_BOT_USERNAME: str = ""  # без @
    TELEGRAM_BOT_TOKEN: str = ""  # токен бота для проверки initData подписи
    TELEGRAM_WEBAPP_STARTAPP: str = "webapp"  # startapp payload для deep-link
    # Доп. токены ботов (через запятую), если Mini App открывают с другого бота — иначе initData hash не сойдётся
    TELEGRAM_WEBAPP_EXTRA_BOT_TOKENS: str = ""
    # Provider token ЮKassa из @BotFather (тот же бот, что и TELEGRAM_TOKEN). Если задан — имеет приоритет над полем в админке.
    TELEGRAM_PAYMENT_PROVIDER_TOKEN: str = ""
    # Чек 54-ФЗ: в теле create payment (сайт) и по умолчанию для счёта в боте (1 = без НДС и т.п. по API ЮKassa). 0 — не добавлять.
    # Если в кабинете ЮKassa включены чеки, счёт в боте без receipt часто не создаётся (звёзды XTR при этом работают).
    YOOKASSA_RECEIPT_VAT_CODE: int = 0
    # None — как YOOKASSA_RECEIPT_VAT_CODE; 0 — явно без чека в sendInvoice бота; 1+ — только для бота (если на сайте чек не передаёте, а в боте провайдер требует).
    YOOKASSA_TELEGRAM_RECEIPT_VAT_CODE: int | None = None
    # Временно: другой магазин ЮKassa (напр. тестовый) без смены ключей в админке. Оба поля обязательны; вебхук проверяется тем же секретом.
    YOOKASSA_OVERRIDE_SHOP_ID: str = ""
    YOOKASSA_OVERRIDE_SECRET_KEY: str = ""

    model_config = SettingsConfigDict(env_file=_env_file_for_settings(), extra="ignore")


settings = Settings()


def _shevelev_address_from_file() -> str:
    p = Path(__file__).resolve().parent / "deployment" / "shevelev_token_address.txt"
    if not p.is_file():
        return ""
    try:
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("0x") and len(line) >= 42:
                return line
    except OSError:
        pass
    return ""


# Публичный адрес контракта ERC-20 SHEVELEV (Decimal Smart Chain). Используется, если не заданы env и файл.
DEFAULT_SHEVELEV_TOKEN_ADDRESS = "0xb5c1933b1fa015818ac2c53812f67611c48e6b56"


def shevelev_token_address() -> str:
    """ERC-20 SHEVELEV: env → файл deployment/shevelev_token_address.txt → константа по умолчанию."""
    env = (settings.SHEVELEV_TOKEN_ADDRESS or "").strip()
    if env:
        return env
    f = _shevelev_address_from_file().strip()
    if f:
        return f
    return (DEFAULT_SHEVELEV_TOKEN_ADDRESS or "").strip()
