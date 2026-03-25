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
    # Отдельный бот для добавления обучающих постов (тот же DATABASE_URL). Пусто — бот не стартует.
    TRAINING_BOT_TOKEN: str = ""
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
    # Email владельца (полный доступ в админку + sync role=admin). В Render можно переопределить env.
    ADMIN_EMAIL: str = "shevelevgymlife@gmail.com"
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
