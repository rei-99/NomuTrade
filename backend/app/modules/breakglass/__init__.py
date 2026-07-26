"""Break-glass emergency access (FR-BG-002, docs/design/12).

An eligible user activates a short-lived (4 h, TBD-03) emergency grant
immediately; activation writes HIGH-severity audit synchronously, notifies all
Security Administrators, and opens a PENDING review item. Non-eligible
activation is rejected with 403 + HIGH audit (fail closed). Eligibility rows
are seeded idempotently by a worker startup task (ensure_seed_data).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import audit as audit_log
from app.core.db import get_db
from app.core.errors import Forbidden, NotFound, StateConflict
from app.core.models import (
    AccessGrant,
    BreakGlassActivation,
    BreakGlassEligibility,
    GrantStatus,
    Role,
    User,
)
from app.core.security import SessionData, invalidate_permissions, require_permission
from app.core.timeutil import as_utc, utcnow
from app.modules.access.service import active_grant_holders, get_role_by_name, notify

router = APIRouter(tags=["breakglass"])

BREAK_GLASS_ACTIVATED = "BREAK_GLASS_ACTIVATED"
BREAK_GLASS_DENIED = "BREAK_GLASS_DENIED"
BREAK_GLASS_REVIEWED = "BREAK_GLASS_REVIEWED"

BREAK_GLASS_DURATION_HOURS = 4  # TBD-03 default

#: Eligibility seeded at worker startup: email -> emergency role name.
ELIGIBILITY: dict[str, str] = {
    "sysadmin@demo.nomura": "System Administrator",
    "secadmin@demo.nomura": "Security Administrator",
}

REVIEW_PENDING = "PENDING"
REVIEW_REVIEWED = "REVIEWED"


async def ensure_seed_data(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Seed BreakGlassEligibility idempotently (safe to call concurrently).

    Uses a short per-connection busy timeout + retry so concurrent startup
    seeders cannot deadlock on SQLite writer locks for seconds (see the SoD
    seeder in app.modules.access.service for details).
    """
    max_attempts = 10
    for attempt in range(max_attempts):
        try:
            async with sessionmaker() as session:
                await session.execute(text("PRAGMA busy_timeout=500"))
                for email, role_name in ELIGIBILITY.items():
                    user = (
                        await session.execute(select(User).where(User.email == email))
                    ).scalar_one_or_none()
                    role = await get_role_by_name(session, role_name)
                    if user is None or role is None:
                        continue
                    if await session.get(BreakGlassEligibility, (user.user_id, role.role_id)) is None:
                        session.add(
                            BreakGlassEligibility(
                                user_id=user.user_id, emergency_role_id=role.role_id
                            )
                        )
                await session.execute(text("PRAGMA busy_timeout=5000"))
                await session.commit()
            return
        except IntegrityError:
            # A concurrent seeder won the race; the rows are present either way.
            return
        except OperationalError:
            await asyncio.sleep(0.1 * (attempt + 1))
        except Exception:
            # Worker path must never take down the supervisor.
            import logging

            logging.getLogger(__name__).exception(
                "ensure_seed_data (break-glass eligibility) failed"
            )
            return


def get_workers(settings):
    """Worker contract: callables fn(bus, sessionmaker) -> coroutine."""

    async def _eligibility_seed(bus, sessionmaker):
        await ensure_seed_data(sessionmaker)

    return [_eligibility_seed]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class ActivateRequest(BaseModel):
    emergency_role: str = Field(min_length=1)  # role name
    reason: str = Field(min_length=1)
    incident_ref: str = Field(min_length=1)


@router.post("/break-glass/activate", status_code=201)
async def activate(
    body: ActivateRequest,
    session: SessionData = Depends(require_permission("BREAKGLASS_ELIGIBLE")),
    db: AsyncSession = Depends(get_db),
):
    role = await get_role_by_name(db, body.emergency_role)
    if role is None:
        raise NotFound(f"role '{body.emergency_role}' not found")
    eligible = await db.get(BreakGlassEligibility, (session.user_id, role.role_id))
    if eligible is None:
        await audit_log.write_audit(
            db,
            actor_id=session.user_id,
            event_type=BREAK_GLASS_DENIED,
            resource_type="ROLE",
            resource_id=role.role_id,
            severity="HIGH",
            payload={
                "emergency_role": role.name,
                "reason": body.reason,
                "incident_ref": body.incident_ref,
            },
            flush_only=False,  # security-critical denial: fail closed, audited
        )
        raise Forbidden("you are not eligible for break-glass activation of this role")

    now = utcnow()
    expires_at = now + timedelta(hours=BREAK_GLASS_DURATION_HOURS)
    grant = AccessGrant(
        user_id=session.user_id,
        role_id=role.role_id,
        request_id=None,
        start_at=now,
        end_at=expires_at,
        status=GrantStatus.ACTIVE.value,
    )
    activation = BreakGlassActivation(
        user_id=session.user_id,
        emergency_role_id=role.role_id,
        incident_ref=body.incident_ref,
        reason=body.reason,
        expires_at=expires_at,
        review_status=REVIEW_PENDING,
    )
    db.add_all([grant, activation])
    await db.flush()
    # One transaction: the synchronous audit below commits grant + activation.
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=BREAK_GLASS_ACTIVATED,
        resource_type="BREAK_GLASS_ACTIVATION",
        resource_id=activation.bg_id,
        severity="HIGH",
        payload={
            "emergency_role": role.name,
            "grant_id": grant.grant_id,
            "reason": body.reason,
            "incident_ref": body.incident_ref,
            "expires_at": expires_at.isoformat(),
        },
        flush_only=False,
    )
    invalidate_permissions(session.user_id)
    for secadmin in await active_grant_holders(db, "Security Administrator"):
        await notify(
            db,
            secadmin.user_id,
            "BREAK_GLASS",
            "Break-glass activated",
            f"{session.email} activated emergency role '{role.name}' "
            f"(incident {body.incident_ref}); expires {expires_at.isoformat()}.",
        )
    await db.commit()
    return {
        "bg_id": activation.bg_id,
        "grant_id": grant.grant_id,
        "expires_at": expires_at.isoformat(),
    }


def _review_json(activation: BreakGlassActivation, user: User | None, role: Role | None) -> dict:
    return {
        "bg_id": activation.bg_id,
        "user": {"email": user.email if user else None},
        "emergency_role": role.name if role else None,
        "reason": activation.reason,
        "incident_ref": activation.incident_ref,
        "activated_at": as_utc(activation.activated_at).isoformat(),
        "expires_at": as_utc(activation.expires_at).isoformat(),
        "review_status": activation.review_status,
        "verdict": activation.verdict,
    }


@router.get("/break-glass/reviews")
async def list_reviews(
    session: SessionData = Depends(require_permission("BREAKGLASS_REVIEW")),
    db: AsyncSession = Depends(get_db),
):
    activations = (
        (
            await db.execute(
                select(BreakGlassActivation).order_by(BreakGlassActivation.activated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    items = []
    for activation in activations:
        user = await db.get(User, activation.user_id)
        role = await db.get(Role, activation.emergency_role_id)
        items.append(_review_json(activation, user, role))
    return {"items": items}


class VerdictRequest(BaseModel):
    verdict: Literal["JUSTIFIED", "ESCALATED"]
    comment: str = Field(min_length=1)


@router.post("/break-glass/reviews/{bg_id}/verdict")
async def record_verdict(
    bg_id: str,
    body: VerdictRequest,
    session: SessionData = Depends(require_permission("BREAKGLASS_REVIEW")),
    db: AsyncSession = Depends(get_db),
):
    activation = await db.get(BreakGlassActivation, bg_id)
    if activation is None:
        raise NotFound("break-glass activation not found")
    if activation.review_status != REVIEW_PENDING:
        raise StateConflict("this break-glass activation has already been reviewed")
    activation.review_status = REVIEW_REVIEWED
    activation.verdict = body.verdict
    activation.reviewed_by = session.user_id
    activation.reviewed_at = utcnow()
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=BREAK_GLASS_REVIEWED,
        resource_type="BREAK_GLASS_ACTIVATION",
        resource_id=activation.bg_id,
        severity="HIGH",
        payload={"verdict": body.verdict, "comment": body.comment},
        flush_only=False,  # break-glass events are security-critical
    )
    await notify(
        db,
        activation.user_id,
        "BREAK_GLASS",
        "Break-glass review completed",
        f"Your break-glass activation (incident {activation.incident_ref}) was "
        f"reviewed: {body.verdict}.",
    )
    await db.commit()
    user = await db.get(User, activation.user_id)
    role = await db.get(Role, activation.emergency_role_id)
    return _review_json(activation, user, role)
