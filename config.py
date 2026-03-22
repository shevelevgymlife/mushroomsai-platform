from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    TELEGRAM_TOKEN: str = ""
    OPENAI_API_KEY: str = ""
    DATABASE_URL: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    JWT_SECRET: str = "change-me-in-production"
    ADMIN_TG_ID: int = 0
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


def shevelev_token_address() -> str:
    """ERC-20 SHEVELEV на Decimal Smart Chain: env SHEVELEV_TOKEN_ADDRESS или строка в deployment/shevelev_token_address.txt."""
    env = (settings.SHEVELEV_TOKEN_ADDRESS or "").strip()
    if env:
        return env
    return _shevelev_address_from_file().strip()
