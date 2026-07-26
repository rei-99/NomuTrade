"""Auth module: dev-login (SSO stub), session profile, logout.

The only module shipped with the foundation. Endpoints (under /api/v1):
- POST /auth/dev-login {email} -> {token, user}   (DEV_AUTH must be enabled)
- GET  /auth/me        -> {user, roles, permissions}
- POST /auth/logout    -> invalidates the session token
"""

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

router = APIRouter(tags=["auth"])


class DevLoginRequest(BaseModel):
    email: str


def _user_json(user: User) -> dict:
    return {
        "user_id": user.user_id,
        "upn": user.upn,
        "display_name": user.display_name,
        "email": user.email,
    }


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
