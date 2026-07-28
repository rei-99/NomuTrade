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
    """Create all tables (dev convenience), then apply additive column patches.

    TODO: replace create_all with Alembic migrations before production.

    create_all never alters existing tables, so columns added to the model
    after a dev DB was created are patched in here (additive-only, idempotent):
    """
    from app.core import models  # noqa: F401  (register tables on the metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_additive_columns(conn)


# table -> {column: DDL type} — additive patches only, never drops/changes.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "orders": {"stop_price": "NUMERIC(24, 8)"},
}


async def _ensure_additive_columns(conn) -> None:
    from sqlalchemy import text

    for table, columns in _ADDITIVE_COLUMNS.items():
        if conn.dialect.name == "sqlite":
            rows = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {r[1] for r in rows}
        else:  # postgres
            rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :table"
                ),
                {"table": table},
            )
            existing = {r[0] for r in rows}
        for column, ddl in columns.items():
            if column not in existing:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                )


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped AsyncSession.

    The engine/sessionmaker are created once in the lifespan and cached on
    app.state; this dependency only opens a session per request.
    """
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session
