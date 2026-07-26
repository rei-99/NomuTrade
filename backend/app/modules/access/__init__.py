"""Access governance: access requests, approvals, roles, grants, JIT expiry.

Endpoints (under /api/v1): /access-requests, /approvals, /roles, /permissions,
/grants. Workers: jit_expiry (30 s grant-expiry sweep) and a startup task that
seeds the SoD conflict matrix idempotently (ensure_seed_data).
"""

from app.modules.access import service
from app.modules.access.routes import router
from app.modules.access.service import (
    ensure_seed_data,
    jit_expiry,
    sweep_expired_grants,
)


def get_workers(settings):
    """Worker contract: callables fn(bus, sessionmaker) -> coroutine."""

    async def _sod_seed(bus, sessionmaker):
        await ensure_seed_data(sessionmaker)

    return [jit_expiry, _sod_seed]


__all__ = [
    "router",
    "get_workers",
    "service",
    "ensure_seed_data",
    "jit_expiry",
    "sweep_expired_grants",
]
