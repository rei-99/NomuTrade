"""Auth module: password login, dev-login (SSO stub), session profile, logout.

Endpoints (under /api/v1):
- POST /auth/login     {email, password} -> {token, user}  (design 26 §R2)
- POST /auth/dev-login {email} -> {token, user}   (DEV_AUTH must be enabled)
- GET  /auth/me        -> {user, roles, permissions}
- POST /auth/logout    -> invalidates the session token

Password login (design 26 §R2, training environment only — production stays
SSO per SRS): uniform 401 for unknown email and wrong password (no user
enumeration), plus an in-memory per-email lockout — after
LOGIN_MAX_FAILURES consecutive failures within LOGIN_LOCKOUT_SECONDS,
further attempts get a 401 "temporarily locked, retry in N s" with
details=[{"retry_after_seconds": N}]; the counter resets on success or
window expiry. The lockout is process-local (single uvicorn worker), same
as the in-memory session store. Demo users all share the training default
password `demo1234` (see passwords.py; documented in README + login hint
box). Failures/successes audit AUTH_LOGIN_FAILURE / AUTH_LOGIN_SUCCESS.
"""

import itertools
import time
from math import ceil

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.audit import AUTH_LOGIN_FAILURE, AUTH_LOGIN_SUCCESS, write_audit
from app.core.db import get_db
from app.core.errors import Unauthenticated
from app.core.models import User
from app.core.security import (
    SessionData,
    extract_bearer_token,
    get_active_role_names,
    get_current_user,
    get_effective_permissions,
)
from app.modules.auth.passwords import (
    DEMO_PASSWORD,
    demo_password_hash,
    verify_password,
)

router = APIRouter(tags=["auth"])

# Round-robin over the trader_1..trader_100 audience accounts for the
# demo-credential endpoint (process-local; wraps past 100).
_demo_credential_cycle = itertools.cycle(range(1, 101))


class DevLoginRequest(BaseModel):
    email: str


def _user_json(user: User) -> dict:
    return {
        "user_id": user.user_id,
        "upn": user.upn,
        "display_name": user.display_name,
        "email": user.email,
    }


# ---------------------------------------------------------------------------
# Password login (design 26 §R2) with in-memory per-email lockout
# ---------------------------------------------------------------------------

# Module constants so tests can monkeypatch the window/threshold if needed.
LOGIN_MAX_FAILURES = 5  # consecutive failures that trigger the lockout
LOGIN_LOCKOUT_SECONDS = 60.0  # lockout/failure window

# email -> monotonic timestamps of the current run of consecutive failures.
_LOGIN_FAILURES: dict[str, list[float]] = {}


def _recent_failures(email: str, now: float) -> list[float]:
    """Failure timestamps for `email` inside the current lockout window."""
    cutoff = now - LOGIN_LOCKOUT_SECONDS
    failures = [t for t in _LOGIN_FAILURES.get(email, []) if t > cutoff]
    if failures:
        _LOGIN_FAILURES[email] = failures
    else:
        _LOGIN_FAILURES.pop(email, None)
    return failures


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
async def password_login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    source_ip = request.client.host if request.client else None
    now = time.monotonic()
    failures = _recent_failures(body.email, now)

    if len(failures) >= LOGIN_MAX_FAILURES:
        retry_after = max(1, ceil(LOGIN_LOCKOUT_SECONDS - (now - failures[0])))
        await write_audit(
            db,
            actor_id=None,
            event_type=AUTH_LOGIN_FAILURE,
            severity="WARN",
            source_ip=source_ip,
            payload={"email": body.email, "reason": "lockout"},
            flush_only=False,
        )
        raise Unauthenticated(
            f"temporarily locked, retry in {retry_after} s",
            details=[{"retry_after_seconds": retry_after}],
        )

    user = (
        await db.execute(
            select(User).where(or_(User.upn == body.email, User.email == body.email))
        )
    ).scalar_one_or_none()
    if user is None or user.status != "ACTIVE":
        # Burn the same PBKDF2 cost as a real check so response timing does
        # not reveal whether the account exists (no enumeration, §R2).
        verify_password(body.password, demo_password_hash())
        ok = False
    else:
        ok = verify_password(body.password, user.password_hash)
    if not ok:
        _LOGIN_FAILURES.setdefault(body.email, []).append(now)
        await write_audit(
            db,
            actor_id=None,
            event_type=AUTH_LOGIN_FAILURE,
            source_ip=source_ip,
            payload={"email": body.email},
            flush_only=False,
        )
        raise Unauthenticated("invalid credentials")

    _LOGIN_FAILURES.pop(body.email, None)
    token = await request.app.state.session_store.create(user)
    await write_audit(
        db,
        actor_id=user.user_id,
        event_type=AUTH_LOGIN_SUCCESS,
        source_ip=source_ip,
        flush_only=False,
    )
    return {"token": token, "user": _user_json(user)}


@router.post("/auth/dev-login")
async def dev_login(
    body: DevLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    source_ip = request.client.host if request.client else None
    if not settings.DEV_AUTH:
        raise Unauthenticated("dev login is disabled")
    user = (
        await db.execute(
            select(User).where(or_(User.upn == body.email, User.email == body.email))
        )
    ).scalar_one_or_none()
    if user is None or user.status != "ACTIVE":
        await write_audit(
            db,
            actor_id=None,
            event_type=AUTH_LOGIN_FAILURE,
            severity="WARN",
            source_ip=source_ip,
            payload={"email": body.email},
            flush_only=False,
        )
        raise Unauthenticated("unknown user")
    token = await request.app.state.session_store.create(user)
    await write_audit(
        db,
        actor_id=user.user_id,
        event_type=AUTH_LOGIN_SUCCESS,
        source_ip=source_ip,
        flush_only=False,
    )
    return {"token": token, "user": _user_json(user)}


@router.get("/auth/demo-credential")
async def demo_credential(request: Request):
    """Audience-demo convenience: the next trader_N credential in round-robin.

    Lets the login page prefill a *different* demo account per visitor so
    audience members can sign in with one tap. Training-environment only —
    gated behind DEV_AUTH exactly like dev-login (never enabled in real
    deployments). The counter is process-local; it simply wraps past 100.
    """
    settings: Settings = request.app.state.settings
    if not settings.DEV_AUTH:
        raise Unauthenticated("demo credential issuance is disabled")
    n = next(_demo_credential_cycle)
    return {
        "email": f"trader_{n}@demo.nomura",
        "password": DEMO_PASSWORD,
    }


@router.get("/auth/me")
async def me(
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = await get_active_role_names(db, session.user_id)
    permissions = await get_effective_permissions(db, session.user_id)
    return {
        "user": {
            "user_id": session.user_id,
            "upn": session.upn,
            "display_name": session.display_name,
            "email": session.email,
        },
        "roles": sorted(roles),
        "permissions": sorted(permissions),
    }


@router.post("/auth/logout")
async def logout(
    request: Request,
    session: SessionData = Depends(get_current_user),
):
    token = extract_bearer_token(request)
    if token:
        await request.app.state.session_store.delete(token)
    return {"status": "ok"}
