from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    TELEGRAM_TOKEN: str = ""
    OPS_TELEGRAM_TOKEN: str = ""  # отдельный бот для задач/подтверждений (ops bot)
    OPENAI_API_KEY: str = ""
    DATABASE_URL: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    JWT_SECRET: str = "change-me-in-production"
    ADMIN_TG_ID: int = 0
    ADMIN_EMAIL: str = ""  # опционально: email владельца (Google) = права оператора
    DEPLOY_NOTIFY_EMAIL_TO: str = ""  # куда слать уведомление о деплое
    DEPLOY_NOTIFY_EMAIL_FROM: str = ""  # от кого слать (если пусто, используем SMTP_USER)
    DEPLOY_NOTIFY_TG_BOT_TOKEN: str = ""  # отдельный токен Telegram-бота для уведомлений/подтверждений
    DEPLOY_NOTIFY_TG_CHAT_ID: str = ""  # chat_id для deploy-уведомлений (личка/группа)
    DEPLOY_NOTIFY_TASK_CHAT_ID: str = ""  # chat_id для статусов задач (если пусто = DEPLOY_NOTIFY_TG_CHAT_ID)
    DEPLOY_NOTIFY_TASK_EMAIL_TO: str = ""  # email для статусов задач (если пусто = DEPLOY_NOTIFY_EMAIL_TO)
    TASK_APPROVAL_BOT_TOKEN: str = ""  # токен для интерактивных подтверждений Да/Нет
    TASK_APPROVAL_CHAT_ID: str = ""  # чат для вопросов подтверждения (если пусто, берем DEPLOY_NOTIFY_TASK_CHAT_ID)
    TASK_APPROVAL_ALLOWED_TG_IDS: str = ""  # доп. TG ID через запятую, кто может нажимать Да/Нет
    TASK_EXECUTOR_WEBHOOK_URL: str = ""  # endpoint внешнего исполнителя задач (optional)
    TASK_EXECUTOR_WEBHOOK_TOKEN: str = ""  # bearer token для TASK_EXECUTOR_WEBHOOK_URL (optional)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_USE_TLS: bool = True
    SITE_URL: str = "https://mushroomsai.ru"
    TELEGRAM_BOT_USERNAME: str = "mushrooms_ai_bot"
    SHEVELEV_TOKEN_ADDRESS: str = ""
    DECIMAL_RPC_URL: str = "https://node.decimalchain.com/web3/"

    class Config:
        env_file = ".env"
        extra = "ignore"


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
