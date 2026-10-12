import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings


def connection_options():
    return {
        "timeout": 5,
        "command_timeout": 30,
        "prepared_statement_name_func": lambda: f"__lenny_{uuid.uuid4().hex}__",
        "statement_cache_size": 0,
    }


engine = create_async_engine(
    settings().database_url,
    pool_pre_ping=True,
    pool_size=settings().database_pool_size,
    max_overflow=4,
    connect_args=connection_options(),
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def database():
    async with SessionLocal() as db:
        yield db
