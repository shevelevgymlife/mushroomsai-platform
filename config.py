from pydantic_settings import BaseSettings
from typing import Optional


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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
