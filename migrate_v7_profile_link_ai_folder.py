"""v7: profile link fields + AI training post folders.

Дублирует автомиграцию в main.py при старте приложения — этот файл можно
запускать вручную только если нужно применить схему без рестарта сервиса.
"""
import os
import sqlalchemy as sa
from config import settings


def _sync_url(url: str) -> str:
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
    )


def main():
    engine = sa.create_engine(_sync_url(settings.DATABASE_URL), pool_pre_ping=True)
    stmts = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_link_label TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_link_url TEXT",
        "ALTER TABLE ai_training_posts ADD COLUMN IF NOT EXISTS folder TEXT",
    ]
    with engine.begin() as conn:
        for sql in stmts:
            conn.execute(sa.text(sql))
    print("migrate_v7: OK (profile_link_*, ai_training_posts.folder)")


if __name__ == "__main__":
    main()
