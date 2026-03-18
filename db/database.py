import databases
import sqlalchemy
from config import settings

database = databases.Database(settings.DATABASE_URL)
metadata = sqlalchemy.MetaData()

engine = sqlalchemy.create_engine(
    settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql://", "postgresql://"),
    pool_pre_ping=True,
)


async def connect_db():
    await database.connect()


async def disconnect_db():
    await database.disconnect()
