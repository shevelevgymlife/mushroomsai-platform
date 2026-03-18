import asyncio
from typing import Any, List, Optional

import sqlalchemy
from sqlalchemy import text

from config import settings


def _sync_url(url: str) -> str:
    """Convert async-driver URL to psycopg2-compatible sync URL."""
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
           .replace("postgresql+psycopg2://", "postgresql://")
    )


metadata = sqlalchemy.MetaData()

engine = sqlalchemy.create_engine(
    _sync_url(settings.DATABASE_URL),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)


class AsyncDatabase:
    """
    Drop-in async wrapper around SQLAlchemy sync engine (psycopg2).
    Replicates the databases.Database API used throughout the codebase.
    All operations run in a thread pool via asyncio.to_thread().
    """

    async def connect(self):
        def _ping():
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        await asyncio.to_thread(_ping)

    async def disconnect(self):
        await asyncio.to_thread(engine.dispose)

    async def execute(self, query) -> Any:
        def _run():
            with engine.begin() as conn:
                result = conn.execute(query)
                pk = result.inserted_primary_key
                if pk is not None:
                    return pk[0] if pk else None
                return result.rowcount
        return await asyncio.to_thread(_run)

    async def fetch_one(self, query) -> Optional[dict]:
        def _run():
            with engine.connect() as conn:
                result = conn.execute(query)
                row = result.fetchone()
                return dict(row._mapping) if row is not None else None
        return await asyncio.to_thread(_run)

    async def fetch_all(self, query) -> List[dict]:
        def _run():
            with engine.connect() as conn:
                result = conn.execute(query)
                return [dict(row._mapping) for row in result.fetchall()]
        return await asyncio.to_thread(_run)

    async def fetch_val(self, query) -> Any:
        def _run():
            with engine.connect() as conn:
                result = conn.execute(query)
                row = result.fetchone()
                return row[0] if row is not None else None
        return await asyncio.to_thread(_run)


database = AsyncDatabase()
