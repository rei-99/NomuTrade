"""Sessions, authN/authZ dependencies, and the effective-permission resolver.

- Opaque server-side session tokens (in-memory dict by default, Redis impl for
  deployment); idle sliding TTL 30 min, absolute TTL 12 h.
- get_current_user: Bearer token -> SessionData, else 401 envelope.
- require_permission(*perms): deny-by-default authorization. Effective
  permissions are the union of permission actions of roles from the user's
  ACTIVE grants, with the grant window re-checked at request time. Results are
  cached in-process for 60 s; call invalidate_permissions(user_id) after any
  grant change. Denials write an AUTHORIZATION_DENIED audit event.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core import audit as audit_log
from app.core.db import get_db
from app.core.errors import Forbidden, Unauthenticated
from app.core.models import (
    AccessGrant,
    GrantStatus,
    Permission,
    Role,
    RolePermission,
    User,
)
from app.core.timeutil import as_utc, utcnow


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@dataclass
class SessionData:
    user_id: str
    upn: str
    display_name: str
    email: str
    created_at: datetime
    last_seen: datetime
    absolute_expiry: datetime

    def to_json(self) -> str:
        data = asdict(self)
        for key in ("created_at", "last_seen", "absolute_expiry"):
            data[key] = data[key].isoformat()
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str) -> "SessionData":
        data = json.loads(raw)
        for key in ("created_at", "last_seen", "absolute_expiry"):
            data[key] = datetime.fromisoformat(data[key])
        return cls(**data)


def _new_session(user: User, absolute_ttl_seconds: int) -> SessionData:
    now = utcnow()
    return SessionData(
        user_id=user.user_id,
        upn=user.upn,
        display_name=user.display_name,
        email=user.email,
        created_at=now,
        last_seen=now,
        absolute_expiry=now + timedelta(seconds=absolute_ttl_seconds),
    )


def _expired(data: SessionData, idle_ttl_seconds: int) -> bool:
    now = utcnow()
    if now >= as_utc(data.absolute_expiry):
        return True
    return (now - as_utc(data.last_seen)).total_seconds() > idle_ttl_seconds


class InMemorySessionStore:
    """Default dev/test session store: process-local dict with TTL checks."""

    def __init__(self, idle_ttl_seconds: int = 1800, absolute_ttl_seconds: int = 43200):
        self._idle_ttl = idle_ttl_seconds
        self._absolute_ttl = absolute_ttl_seconds
        self._sessions: dict[str, SessionData] = {}

    async def create(self, user: User) -> str:
        token = uuid.uuid4().hex
        self._sessions[token] = _new_session(user, self._absolute_ttl)
        return token

    async def get(self, token: str) -> SessionData | None:
        data = self._sessions.get(token)
        if data is None:
            return None
        if _expired(data, self._idle_ttl):
            self._sessions.pop(token, None)
            return None
        data.last_seen = utcnow()  # sliding idle TTL
        return data

    async def delete(self, token: str) -> None:
        self._sessions.pop(token, None)

    async def close(self) -> None:
        return None


class RedisSessionStore:
    """Redis-backed sessions (keys `sess:*`), sliding idle TTL via EXPIRE."""

    PREFIX = "sess:"

    def __init__(self, url: str, idle_ttl_seconds: int, absolute_ttl_seconds: int):
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url)
        self._idle_ttl = idle_ttl_seconds
        self._absolute_ttl = absolute_ttl_seconds

    def _key(self, token: str) -> str:
        return f"{self.PREFIX}{token}"

    async def create(self, user: User) -> str:
        token = uuid.uuid4().hex
        data = _new_session(user, self._absolute_ttl)
        await self._redis.set(self._key(token), data.to_json(), ex=self._idle_ttl)
        return token

    async def get(self, token: str) -> SessionData | None:
        key = self._key(token)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        data = SessionData.from_json(raw)
        if _expired(data, self._idle_ttl):
            await self._redis.delete(key)
            return None
        data.last_seen = utcnow()  # sliding idle TTL
        await self._redis.set(key, data.to_json(), ex=self._idle_ttl)
        return data

    async def delete(self, token: str) -> None:
        await self._redis.delete(self._key(token))

    async def close(self) -> None:
        await self._redis.aclose()


def get_session_store(settings: Settings):
    if settings.SESSION_STORE == "redis":
        return RedisSessionStore(
            settings.REDIS_URL,
            settings.ACCESS_TOKEN_TTL_IDLE_SECONDS,
            settings.ACCESS_TOKEN_TTL_ABSOLUTE_SECONDS,
        )
    return InMemorySessionStore(
        settings.ACCESS_TOKEN_TTL_IDLE_SECONDS,
        settings.ACCESS_TOKEN_TTL_ABSOLUTE_SECONDS,
    )


# ---------------------------------------------------------------------------
# AuthN
# ---------------------------------------------------------------------------


def extract_bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> SessionData:
    """FastAPI dependency: resolve Bearer token -> SessionData, else 401."""
    token = extract_bearer_token(request)
    if not token:
        raise Unauthenticated("missing bearer token")
    session = await request.app.state.session_store.get(token)
    if session is None:
        raise Unauthenticated("invalid or expired session")
    user = await db.get(User, session.user_id)
    if user is None or user.status != "ACTIVE":
        raise Unauthenticated("user is not active")
    return session


# ---------------------------------------------------------------------------
# AuthZ: effective-permission resolver (+60 s cache, deny by default)
# ---------------------------------------------------------------------------

_permission_cache: dict[str, tuple[float, set[str]]] = {}
PERMISSION_CACHE_TTL_SECONDS = 60.0


async def get_effective_permissions(db: AsyncSession, user_id: str) -> set[str]:
    """Union of permission actions from the user's ACTIVE, in-window grants.

    The [start_at, end_at] window is always re-checked here (request time),
    even when the caller goes through the 60 s cache.
    """
    now = utcnow()
    stmt = (
        select(Permission.action, AccessGrant.start_at, AccessGrant.end_at)
        .join(RolePermission, RolePermission.permission_id == Permission.permission_id)
        .join(AccessGrant, AccessGrant.role_id == RolePermission.role_id)
        .where(
            AccessGrant.user_id == user_id,
            AccessGrant.status == GrantStatus.ACTIVE.value,
        )
    )
    permissions: set[str] = set()
    for action, start_at, end_at in (await db.execute(stmt)).all():
        if as_utc(start_at) <= now <= as_utc(end_at):
            permissions.add(action)
    return permissions


async def get_active_role_names(db: AsyncSession, user_id: str) -> set[str]:
    """Names of roles from the user's ACTIVE, in-window grants."""
    now = utcnow()
    stmt = (
        select(Role.name, AccessGrant.start_at, AccessGrant.end_at)
        .join(AccessGrant, AccessGrant.role_id == Role.role_id)
        .where(
            AccessGrant.user_id == user_id,
            AccessGrant.status == GrantStatus.ACTIVE.value,
        )
    )
    return {
        name
        for name, start_at, end_at in (await db.execute(stmt)).all()
        if as_utc(start_at) <= now <= as_utc(end_at)
    }


async def _cached_permissions(db: AsyncSession, user_id: str) -> set[str]:
    now = time.monotonic()
    hit = _permission_cache.get(user_id)
    if hit and hit[0] > now:
        return hit[1]
    permissions = await get_effective_permissions(db, user_id)
    _permission_cache[user_id] = (now + PERMISSION_CACHE_TTL_SECONDS, permissions)
    return permissions


def invalidate_permissions(user_id: str) -> None:
    """Drop the cached permission set for a user (call after grant changes)."""
    _permission_cache.pop(user_id, None)


def require_permission(*perms: str):
    """Dependency factory enforcing that the caller holds ALL given permissions.

    Permission strings are catalog action names, e.g. require_permission("ORDER_SUBMIT").
    Deny by default: missing any permission -> AUTHORIZATION_DENIED audit + 403.
    """
    required = set(perms)

    async def _dependency(
        request: Request,
        session: SessionData = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> SessionData:
        effective = await _cached_permissions(db, session.user_id)
        if not required.issubset(effective):
            await audit_log.write_audit(
                db,
                actor_id=session.user_id,
                event_type=audit_log.AUTHORIZATION_DENIED,
                severity="INFO",
                source_ip=request.client.host if request.client else None,
                payload={
                    "required": sorted(required),
                    "path": request.url.path,
                },
                flush_only=False,  # security-critical: persist immediately
            )
            missing = ", ".join(sorted(required - effective))
            raise Forbidden(f"missing required permission: {missing}")
        return session

    return _dependency
