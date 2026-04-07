"""
Async SQLAlchemy engine, session factory, and declarative Base.
"""
import ssl
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Build connect_args for SSL if the URL contains sslmode=require
# asyncpg uses a native Python ssl.SSLContext instead of the query param
connect_args = {}
db_url = settings.DATABASE_URL
if "sslmode=" in db_url:
    # Create a permissive SSL context (Aiven uses self-signed certs by default)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    connect_args["ssl"] = ssl_ctx
    # Strip the sslmode param from the URL — asyncpg doesn't understand it
    import re
    db_url = re.sub(r"[?&]sslmode=[^&]*", "", db_url)
    # Fix dangling ? or & at the end
    db_url = db_url.rstrip("?&")

# Async engine — pool_pre_ping keeps stale connections out of the pool
engine = create_async_engine(
    db_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args=connect_args,
)

# Session factory bound to the engine
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields one session per request, auto-closes."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            if session.is_active:
                await session.commit()
        except Exception as e:
            logger.error("DB session error, rolling back: %s", e)
            await session.rollback()
            raise
