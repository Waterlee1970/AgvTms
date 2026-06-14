"""
Database engine, session factory, and dependency injection.

Uses SQLAlchemy 2.0 async with asyncpg driver.
Falls back to in-memory mode when DB is unavailable (dev/demo mode).
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


# Lazy-initialized engine (created on first use)
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_db_available: Optional[bool] = None


async def _check_db_available() -> bool:
    """Check if the database is reachable."""
    global _db_available
    if _db_available is not None:
        return _db_available
    if not settings.USE_DB_PERSISTENCE:
        _db_available = False
        return False
    try:
        engine = get_engine()
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        _db_available = True
        logger.info("✅ Database connected: %s", settings.DATABASE_URL.split("@")[-1])
    except Exception as e:
        _db_available = False
        logger.warning("⚠️ Database unavailable (%s), running in memory-only mode", e)
    return _db_available


def get_engine() -> AsyncEngine:
    """Get or create the async engine (singleton)."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            echo=settings.DB_ECHO,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the session factory (singleton)."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[Optional[AsyncSession], None]:
    """
    FastAPI dependency: yield an async DB session.
    Returns None if DB is unavailable (graceful degradation).
    """
    if not await _check_db_available():
        yield None
        return
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables if they don't exist."""
    if not await _check_db_available():
        return
    # Import models so they register with Base
    from . import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Database tables created/verified")


async def close_db() -> None:
    """Dispose of the engine on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
