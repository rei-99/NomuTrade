"""Access-governance endpoints: requests, approvals, roles, grants, extension.

All paths are declared in full and mounted under /api/v1 by the app factory.
Design: docs/design/08 (workflow), 09 (SoD), 10 (JIT).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit as audit_log
from app.core.db import get_db
from app.core.errors import Forbidden, NotFound, StateConflict, ValidationError
from app.core.models import (
    AccessGrant,
    AccessRequest,
    ApprovalStep,
    GrantStatus,
    Permission,
    RequestStatus,
    Role,
    RolePermission,
    User,
)
from app.core.security import (
    SessionData,
    get_current_user,
    get_effective_permissions,
    invalidate_permissions,
    require_permission,
)
from app.core.timeutil import utcnow
from app.modules.access import service

router = APIRouter(tags=["access"])

OPEN_STATUSES = (RequestStatus.SUBMITTED.value, RequestStatus.PENDING_INFO.value)


# ---------------------------------------------------------------------------
# Access requests
# ---------------------------------------------------------------------------


class CreateAccessRequest(BaseModel):
    target_role: str = Field(min_length=1)  # role name
    justification: str = Field(min_length=1)
    requested_duration_hours: int = Field(ge=1)
    on_behalf_of: str | None = None  # email of the beneficiary


@router.post("/access-requests", status_code=201)
async def create_access_request(
    body: CreateAccessRequest,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = await service.get_role_by_name(db, body.target_role)
    if role is None:
        raise NotFound(f"role '{body.target_role}' not found")
    on_behalf_of_id = None
    if body.on_behalf_of:
        beneficiary = (
            await db.execute(select(User).where(User.email == body.on_behalf_of))
        ).scalar_one_or_none()
        if beneficiary is None:
            raise NotFound(f"user '{body.on_behalf_of}' not found")
        on_behalf_of_id = beneficiary.user_id

    request, chain = await service.submit_access_request(
        db,
        requester_id=session.user_id,
        requester_email=session.email,
        role=role,
        justification=body.justification,
        requested_hours=body.requested_duration_hours,
        on_behalf_of_id=on_behalf_of_id,
    )
    await db.commit()
    return {
        "request_id": request.request_id,
        "status": request.status,
        "current_level": 1,
        "levels": chain,
    }


@router.get("/access-requests")
async def list_access_requests(
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mine (requester or beneficiary); GRANT_VIEW holders get all."""
    effective = await get_effective_permissions(db, session.user_id)
    stmt = select(AccessRequest)
    if "GRANT_VIEW" not in effective:
        stmt = stmt.where(
            or_(
                AccessRequest.requester_id == session.user_id,
                AccessRequest.on_behalf_of == session.user_id,
            )
        )
    requests = (
        (await db.execute(stmt.order_by(AccessRequest.created_at.desc())))
        .scalars()
        .all()
    )
    return {
        "items": [await service.request_json(db, r) for r in requests],
        "next_cursor": None,
    }


@router.get("/access-requests/{request_id}")
async def get_access_request(
    request_id: str,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request = await db.get(AccessRequest, request_id)
    if request is None:
        raise NotFound("access request not found")
    effective = await get_effective_permissions(db, session.user_id)
    allowed = (
        request.requester_id == session.user_id
        or request.on_behalf_of == session.user_id
        or "GRANT_VIEW" in effective
        or service.is_current_approver(request, session.user_id)
    )
    if not allowed:
        raise Forbidden("not authorized to view this access request")
    return await service.request_json(db, request)


@router.post("/access-requests/{request_id}/withdraw")
async def withdraw_access_request(
    request_id: str,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request = await db.get(AccessRequest, request_id)
    if request is None:
        raise NotFound("access request not found")
    if request.requester_id != session.user_id:
        raise Forbidden("only the requester may withdraw this request")
    if request.status not in OPEN_STATUSES:
        raise StateConflict(f"cannot withdraw a request in status {request.status}")
    request.status = RequestStatus.WITHDRAWN.value
    request.decided_at = utcnow()
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=service.ACCESS_REQUEST_WITHDRAWN,
        resource_type="ACCESS_REQUEST",
        resource_id=request.request_id,
    )
    level = service.current_level(request)
    for step in request.steps:
        if step.decision is None and step.level == level:
            await service.notify(
                db,
                step.approver_id,
                "ACCESS",
                "Access request withdrawn",
                f"{session.email} withdrew their access request.",
            )
    await db.commit()
    return await service.request_json(db, request)


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


@router.get("/approvals")
async def list_my_approvals(
    session: SessionData = Depends(require_permission("APPROVE_ACCESS")),
    db: AsyncSession = Depends(get_db),
):
    """Undecided steps assigned to me at their request's current level."""
    steps = (
        (
            await db.execute(
                select(ApprovalStep)
                .join(AccessRequest, AccessRequest.request_id == ApprovalStep.request_id)
                .where(
                    ApprovalStep.approver_id == session.user_id,
                    ApprovalStep.decision.is_(None),
                    AccessRequest.status.in_(OPEN_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    items = []
    for step in steps:
        request = await db.get(AccessRequest, step.request_id)
        if step.level != service.current_level(request):
            continue  # stale sibling step from an already-decided level
        items.append(
            {
                "step_id": step.step_id,
                "level": step.level,
                "request": await service.request_json(db, request),
            }
        )
    return {"items": items}


class DecisionRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    comment: str = Field(min_length=1)  # mandatory


@router.post("/approvals/{step_id}/decision")
async def decide_approval(
    step_id: str,
    body: DecisionRequest,
    session: SessionData = Depends(require_permission("APPROVE_ACCESS")),
    db: AsyncSession = Depends(get_db),
):
    step = await db.get(ApprovalStep, step_id)
    if step is None:
        raise NotFound("approval step not found")
    if step.approver_id != session.user_id:
        raise Forbidden("you are not the assigned approver for this step")
    request = await db.get(AccessRequest, step.request_id)
    if request.requester_id == session.user_id:
        raise Forbidden("a requester may not approve their own request")
    if step.decision is not None:
        raise StateConflict("this step has already been decided")
    if request.status not in OPEN_STATUSES:
        raise StateConflict(f"request is in status {request.status}")
    if step.level != service.current_level(request):
        raise StateConflict("this step is not at the request's current level")

    role = await db.get(Role, request.role_id)
    await service.apply_decision(
        db,
        step=step,
        request=request,
        role=role,
        approver_id=session.user_id,
        decision=body.decision,
        comment=body.comment,
    )
    return await service.request_json(db, request)


# ---------------------------------------------------------------------------
# Roles & permission catalog
# ---------------------------------------------------------------------------


@router.get("/roles")
async def list_roles(
    # Any authenticated user may read the role catalog (FR-IAM: access
    # requests are available to all authenticated users, and the request
    # form needs the catalog; role names are not sensitive). Role
    # management below stays ROLE_MANAGE-gated.
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = ((await db.execute(select(Role).order_by(Role.name))).scalars().all())
    return [service.role_json(r) for r in roles]


class CreateRole(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    permission_actions: list[str] = []


async def _permissions_by_action(
    db: AsyncSession, actions: list[str]
) -> dict[str, Permission]:
    perms = (
        (await db.execute(select(Permission).where(Permission.action.in_(actions))))
        .scalars()
        .all()
    )
    by_action = {p.action: p for p in perms}
    unknown = sorted(set(actions) - set(by_action))
    if unknown:
        raise ValidationError(
            f"unknown permission actions: {', '.join(unknown)}",
            details=[{"unknown_actions": unknown}],
        )
    return by_action


@router.post("/roles", status_code=201)
async def create_role(
    body: CreateRole,
    session: SessionData = Depends(require_permission("ROLE_MANAGE")),
    db: AsyncSession = Depends(get_db),
):
    existing = await service.get_role_by_name(db, body.name)
    if existing is not None:
        raise StateConflict(f"role '{body.name}' already exists")
    by_action = await _permissions_by_action(db, body.permission_actions)
    role = Role(
        name=body.name,
        description=body.description,
        built_in=False,
        version=1,
        status="ACTIVE",
    )
    db.add(role)
    await db.flush()
    for action in body.permission_actions:
        db.add(RolePermission(role_id=role.role_id, permission_id=by_action[action].permission_id))
    await db.flush()
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=service.ROLE_CREATED,
        resource_type="ROLE",
        resource_id=role.role_id,
        payload={"name": role.name, "permission_actions": sorted(body.permission_actions)},
    )
    await db.commit()
    await db.refresh(role)
    return service.role_json(role)


class PatchRole(BaseModel):
    description: str | None = None
    permission_actions: list[str] | None = None


@router.patch("/roles/{role_id}")
async def update_role(
    role_id: str,
    body: PatchRole,
    session: SessionData = Depends(require_permission("ROLE_MANAGE")),
    db: AsyncSession = Depends(get_db),
):
    role = await db.get(Role, role_id)
    if role is None:
        raise NotFound("role not found")
    if body.description is None and body.permission_actions is None:
        raise ValidationError("nothing to update: provide description and/or permission_actions")
    if body.description is not None:
        role.description = body.description
    if body.permission_actions is not None:
        by_action = await _permissions_by_action(db, body.permission_actions)
        for link in (
            (await db.execute(select(RolePermission).where(RolePermission.role_id == role.role_id)))
            .scalars()
            .all()
        ):
            await db.delete(link)
        await db.flush()
        for action in body.permission_actions:
            db.add(RolePermission(role_id=role.role_id, permission_id=by_action[action].permission_id))
    role.version += 1
    await db.flush()
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=service.ROLE_UPDATED,
        resource_type="ROLE",
        resource_id=role.role_id,
        payload={
            "name": role.name,
            "version": role.version,
            **(
                {"permission_actions": sorted(body.permission_actions)}
                if body.permission_actions is not None
                else {}
            ),
        },
    )
    # Effective permissions changed: invalidate every holder of this role.
    holder_ids = (
        (await db.execute(
            select(AccessGrant.user_id)
            .where(AccessGrant.role_id == role.role_id, AccessGrant.status == GrantStatus.ACTIVE.value)
            .distinct()
        ))
        .scalars()
        .all()
    )
    for user_id in holder_ids:
        invalidate_permissions(user_id)
    await db.commit()
    await db.refresh(role)
    return service.role_json(role)


@router.get("/permissions")
async def list_permissions(
    session: SessionData = Depends(require_permission("ROLE_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    perms = ((await db.execute(select(Permission).order_by(Permission.action))).scalars().all())
    return [
        {
            "permission_id": p.permission_id,
            "action": p.action,
            "resource_type": p.resource_type,
            "description": p.description,
        }
        for p in perms
    ]


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


@router.get("/grants")
async def list_grants(
    session: SessionData = Depends(require_permission("GRANT_VIEW")),
    db: AsyncSession = Depends(get_db),
    user_email: str | None = Query(None),
    role: str | None = Query(None),
    status: str | None = Query(None),
):
    stmt = (
        select(AccessGrant)
        .join(User, User.user_id == AccessGrant.user_id)
        .join(Role, Role.role_id == AccessGrant.role_id)
    )
    if user_email:
        stmt = stmt.where(User.email == user_email)
    if role:
        stmt = stmt.where(Role.name == role)
    if status:
        if status not in GrantStatus.__members__:
            raise ValidationError(f"status must be one of: {', '.join(GrantStatus.__members__)}")
        stmt = stmt.where(AccessGrant.status == status)
    grants = (
        (await db.execute(stmt.order_by(AccessGrant.created_at.desc())))
        .scalars()
        .all()
    )
    return {"items": [service.grant_json(g) for g in grants]}


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=1)  # mandatory


@router.post("/grants/{grant_id}/revoke")
async def revoke_grant(
    grant_id: str,
    body: RevokeRequest,
    session: SessionData = Depends(require_permission("GRANT_REVOKE")),
    db: AsyncSession = Depends(get_db),
):
    grant = await db.get(AccessGrant, grant_id)
    if grant is None:
        raise NotFound("grant not found")
    if grant.status != GrantStatus.ACTIVE.value:
        raise StateConflict(f"grant is not ACTIVE (status {grant.status})")
    grant.status = GrantStatus.REVOKED.value
    grant.revoked_by = session.user_id
    grant.revoked_reason = body.reason
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=service.GRANT_REVOKED,
        resource_type="ACCESS_GRANT",
        resource_id=grant.grant_id,
        severity="HIGH",
        payload={"user_id": grant.user_id, "role": grant.role.name, "reason": body.reason},
        flush_only=False,  # revocation is security-critical
    )
    invalidate_permissions(grant.user_id)
    await service.notify(
        db,
        grant.user_id,
        "GRANT",
        "Access grant revoked",
        f"Your '{grant.role.name}' grant was revoked: {body.reason}",
    )
    await db.commit()
    return service.grant_json(grant)


class ExtendRequest(BaseModel):
    additional_hours: int = Field(ge=1)
    justification: str = Field(min_length=1)


@router.post("/grants/{grant_id}/extend", status_code=201)
async def extend_grant(
    grant_id: str,
    body: ExtendRequest,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Grantee-only extension: opens an AccessRequest for the same role whose
    justification carries the EXTENSION_PREFIX marker; the normal approval
    chain applies and the final approval extends the grant (see service)."""
    grant = await db.get(AccessGrant, grant_id)
    if grant is None:
        raise NotFound("grant not found")
    if grant.user_id != session.user_id:
        raise Forbidden("only the grantee may extend a grant")
    if grant.status != GrantStatus.ACTIVE.value:
        raise StateConflict(f"only an ACTIVE grant can be extended (status {grant.status})")
    role = await db.get(Role, grant.role_id)
    request, chain = await service.submit_access_request(
        db,
        requester_id=session.user_id,
        requester_email=session.email,
        role=role,
        justification=f"{service.EXTENSION_PREFIX}{grant.grant_id}: {body.justification}",
        requested_hours=body.additional_hours,
        on_behalf_of_id=None,
    )
    await db.commit()
    return {
        "request_id": request.request_id,
        "status": request.status,
        "current_level": 1,
        "levels": chain,
    }
