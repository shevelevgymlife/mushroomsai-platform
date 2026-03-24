import asyncio
from typing import Any, List, Optional
import sqlalchemy
from sqlalchemy import text
from config import settings

def _sync_url(url: str) -> str:
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
           .replace("postgresql+psycopg2://", "postgresql://")
    )


metadata = sqlalchemy.MetaData()

_engine: sqlalchemy.Engine | None = None


def get_engine() -> sqlalchemy.Engine:
    """Создаёт sync engine при первом обращении (импорт main без DATABASE_URL не падает — удобнее логи Render)."""
    global _engine
    if _engine is not None:
        return _engine
    _db_url = (settings.DATABASE_URL or "").strip()
    if not _db_url:
        raise RuntimeError(
            "MushroomsAI: DATABASE_URL не задан или пустой. "
            "На Render: открой PostgreSQL → Connect → Internal Database URL, "
            "вставь в Web Service → Environment как DATABASE_URL → Save → Manual Deploy."
        )
    _engine = sqlalchemy.create_engine(
        _sync_url(_db_url),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
    return _engine

class AsyncDatabase:
    async def connect(self):
        def _ping():
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        await asyncio.to_thread(_ping)

    async def disconnect(self):
        global _engine
        if _engine is None:
            return
        eng = _engine
        _engine = None
        await asyncio.to_thread(eng.dispose)

    async def execute(self, query, params: Optional[dict] = None) -> Any:
        if isinstance(query, str):
            query = text(query)

        def _run():
            with get_engine().begin() as conn:
                result = conn.execute(query, params) if params is not None else conn.execute(query)
                try:
                    pk = result.inserted_primary_key
                    if pk is not None:
                        return pk[0] if pk else None
                except Exception:
                    pass
                return result.rowcount
        return await asyncio.to_thread(_run)

    async def fetch_one(self, query, params: Optional[dict] = None) -> Optional[dict]:
        def _run():
            with get_engine().connect() as conn:
                result = conn.execute(query, params) if params is not None else conn.execute(query)
                row = result.fetchone()
                return dict(row._mapping) if row is not None else None
        return await asyncio.to_thread(_run)

    async def fetch_one_write(self, query, params: Optional[dict] = None) -> Optional[dict]:
        """INSERT/UPDATE … RETURNING — в транзакции с commit (иначе fetch_one откатывает изменения)."""
        if isinstance(query, str):
            query = text(query)

        def _run():
            with get_engine().begin() as conn:
                result = conn.execute(query, params) if params is not None else conn.execute(query)
                row = result.fetchone()
                return dict(row._mapping) if row is not None else None
        return await asyncio.to_thread(_run)

    async def fetch_all(self, query, params: Optional[dict] = None) -> List[dict]:
        def _run():
            with get_engine().connect() as conn:
                result = conn.execute(query, params) if params is not None else conn.execute(query)
                return [dict(row._mapping) for row in result.fetchall()]
        return await asyncio.to_thread(_run)

    async def fetch_val(self, query, params: Optional[dict] = None) -> Any:
        def _run():
            with get_engine().connect() as conn:
                result = conn.execute(query, params) if params is not None else conn.execute(query)
                row = result.fetchone()
                return row[0] if row is not None else None
        return await asyncio.to_thread(_run)

database = AsyncDatabase()
