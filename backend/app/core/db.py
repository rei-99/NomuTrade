"""Async engine/session plumbing and the declarative Base."""

from typing import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings


class Base(DeclarativeBase):
    pass


def get_engine(settings: Settings) -> AsyncEngine:
    connect_args: dict = {}
    if settings.DATABASE_URL.startswith("sqlite"):
        # SQLite allows a single writer; give concurrent workers up to 15 s to
        # wait for the write lock instead of failing with "database is locked"
        # (matters during the one-time dataset bulk load).
        connect_args["timeout"] = 15
    return create_async_engine(settings.DATABASE_URL, connect_args=connect_args)


def get_sessionmaker(
    settings: Settings, engine: AsyncEngine | None = None
) -> async_sessionmaker[AsyncSession]:
    engine = engine or get_engine(settings)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create all tables (dev convenience).

    TODO: replace create_all with Alembic migrations before production.
    """
    from app.core import models  # noqa: F401  (register tables on the metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped AsyncSession.

    The engine/sessionmaker are created once in the lifespan and cached on
    app.state; this dependency only opens a session per request.
    """
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session
