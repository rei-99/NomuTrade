"""FastAPI application factory: lifespan, module auto-discovery, health endpoint.

Module auto-discovery contract (for module agents): drop a package under
`app/modules/<name>/`; in its `__init__.py` expose
- `router`: an APIRouter — included under the `/api/v1` prefix, and/or
- `get_workers(settings)`: returning callables `fn(bus, sessionmaker)` that
  return coroutines; they run as background tasks alongside the outbox relay
  and are cancelled on shutdown.
Discovery works with zero modules present.
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

import app.modules as modules_pkg
from app.config import Settings
from app.core.db import create_all, get_engine, get_sessionmaker
from app.core.errors import TraceIdMiddleware, register_error_handlers
from app.core.events import InProcessBus, RedisBus, run_worker_coroutines
from app.core.models import User
from app.core.secrets import get_secret_provider
from app.core.security import get_session_store
from app.seed import seed as seed_database


def _discover_modules(app: FastAPI, settings: Settings) -> list[Callable]:
    """Import every package under app/modules; wire routers and collect workers."""
    worker_fns: list[Callable] = []
    for info in pkgutil.iter_modules(modules_pkg.__path__):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"app.modules.{info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            app.include_router(router, prefix="/api/v1")
        get_workers = getattr(module, "get_workers", None)
        if callable(get_workers):
            worker_fns.extend(get_workers(settings) or [])
    return worker_fns


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Process-local market-data state belongs to this app instance.
        from app.modules.marketdata.registry import reset_registry

        reset_registry()

        # DB: engine/sessionmaker cached on app.state; create_all in dev.
        engine = get_engine(settings)
        sessionmaker = get_sessionmaker(settings, engine)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        await create_all(engine)

        # Auto-seed catalog + demo data on an empty database.
        async with sessionmaker() as session:
            user_count = await session.scalar(select(func.count(User.user_id)))
            if not user_count:
                await seed_database(session)
                await session.commit()

        # Idempotent startup patch (design 26 §R2): every user without a
        # password hash gets the training demo password. Runs on every boot,
        # so it also reaches dev DBs the once-only seed cannot.
        from app.modules.auth.passwords import ensure_demo_passwords

        await ensure_demo_passwords(sessionmaker)

        # Infrastructure components.
        bus = (
            RedisBus(settings.REDIS_URL)
            if settings.EVENT_BUS == "redis"
            else InProcessBus()
        )
        app.state.bus = bus
        app.state.session_store = get_session_store(settings)
        app.state.secret_provider = get_secret_provider(settings)

        # Background workers: module workers + outbox relay.
        worker_task = None
        if settings.RUN_WORKERS:
            worker_task = asyncio.create_task(
                run_worker_coroutines(settings, sessionmaker, bus, worker_fns),
                name="worker-supervisor",
            )
        app.state.worker_task = worker_task

        try:
            yield
        finally:
            if worker_task is not None:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
            store_close = getattr(app.state.session_store, "close", None)
            if store_close is not None:
                await store_close()
            await bus.close()
            await engine.dispose()

    app = FastAPI(title="STP Platform", version="0.1.0", lifespan=lifespan)

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    worker_fns = _discover_modules(app, settings)

    register_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TraceIdMiddleware)  # outermost: trace id on every response

    return app


app = create_app()
