"""Admin & governance (FR-ADM, docs/design/15).

Governance dashboard aggregates (grants, approvals, expiries, break-glass,
denial stats), the integration health view (directory / CyberArk / SMTP /
market feed probes + outbox depth + STP exceptions), and the who-has-what
access-review CSV export (writes its own GOVERNANCE_EXPORT audit event).
"""

from __future__ import annotations

import csv
import io
import os
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.core import audit as audit_log
from app.core.db import get_db
from app.core.errors import ValidationError
from app.core.models import (
    AccessGrant,
    AccessRequest,
    AuditEvent,
    BreakGlassActivation,
    GrantStatus,
    LifecycleState,
    OutboxEvent,
    PriceTick,
    RequestStatus,
    Role,
    SettlementInstruction,
    User,
)
from app.core.security import SessionData, require_permission
from app.core.timeutil import as_utc, utcnow

router = APIRouter(tags=["admin"])

GOVERNANCE_EXPORT = "GOVERNANCE_EXPORT"

MARKET_FEED_STALE_SECONDS = 60
STP_EXCEPTION_AGE_SECONDS = 120
RECENT_BREAK_GLASS_LIMIT = 5


# ---------------------------------------------------------------------------
# Governance summary
# ---------------------------------------------------------------------------


@router.get("/admin/governance-summary")
async def governance_summary(
    session: SessionData = Depends(require_permission("GOVERNANCE_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    now = utcnow()

    active_grants = await db.scalar(
        select(func.count(AccessGrant.grant_id)).where(
            AccessGrant.status == GrantStatus.ACTIVE.value
        )
    )

    # Pending approvals: undecided steps at their request's current level.
    open_requests = (
        (
            await db.execute(
                select(AccessRequest).where(
                    AccessRequest.status.in_(
                        [RequestStatus.SUBMITTED.value, RequestStatus.PENDING_INFO.value]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    pending_count = 0
    pending_ages_hours: list[float] = []
    for request in open_requests:
        level = max((s.level for s in request.steps), default=1)
        pending = [s for s in request.steps if s.decision is None and s.level == level]
        if pending:
            pending_count += len(pending)
            pending_ages_hours.append(
                (now - as_utc(request.created_at)).total_seconds() / 3600
            )

    grants = (
        (
            await db.execute(
                select(AccessGrant).where(AccessGrant.status == GrantStatus.ACTIVE.value)
            )
        )
        .scalars()
        .all()
    )
    expiring_24h = sum(
        1 for g in grants if now <= as_utc(g.end_at) <= now + timedelta(hours=24)
    )

    bg_pending = await db.scalar(
        select(func.count(BreakGlassActivation.bg_id)).where(
            BreakGlassActivation.review_status == "PENDING"
        )
    )

    denials = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == audit_log.AUTHORIZATION_DENIED
                )
            )
        )
        .scalars()
        .all()
    )
    denials_24h = sum(
        1 for e in denials if as_utc(e.ts) >= now - timedelta(hours=24)
    )

    recent_bg = (
        (
            await db.execute(
                select(BreakGlassActivation)
                .order_by(BreakGlassActivation.activated_at.desc())
                .limit(RECENT_BREAK_GLASS_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    bg_user_ids = {b.user_id for b in recent_bg}
    bg_users = (
        {
            u.user_id: u
            for u in (
                (await db.execute(select(User).where(User.user_id.in_(bg_user_ids))))
                .scalars()
                .all()
            )
        }
        if bg_user_ids
        else {}
    )

    return {
        "active_grants": active_grants or 0,
        "pending_approvals": pending_count,
        "oldest_age_hours": (
            round(max(pending_ages_hours), 2) if pending_ages_hours else None
        ),
        "grants_expiring_24h": expiring_24h,
        "break_glass_pending_review": bg_pending or 0,
        "authorization_denials_24h": denials_24h,
        "recent_break_glass": [
            {
                "bg_id": b.bg_id,
                "user_email": bg_users[b.user_id].email if b.user_id in bg_users else None,
                "activated_at": as_utc(b.activated_at).isoformat(),
            }
            for b in recent_bg
        ],
    }


# ---------------------------------------------------------------------------
# Integration health
# ---------------------------------------------------------------------------


@router.get("/admin/health")
async def integration_health(
    session: SessionData = Depends(require_permission("INTEGRATION_MONITOR")),
    db: AsyncSession = Depends(get_db),
):
    now = utcnow()

    cyberark_up = os.environ.get("CYBERARK_AVAILABLE", "true").strip().lower() != "false"

    newest_tick_ts = (
        await db.execute(select(PriceTick.ts).order_by(PriceTick.ts.desc()).limit(1))
    ).scalar_one_or_none()
    if newest_tick_ts is None:
        feed_status, feed_last_success, feed_detail = "DOWN", None, "no ticks received"
    else:
        tick_ts = as_utc(newest_tick_ts)
        age_seconds = (now - tick_ts).total_seconds()
        feed_status = "UP" if age_seconds < MARKET_FEED_STALE_SECONDS else "DOWN"
        feed_last_success = tick_ts.isoformat()
        feed_detail = f"latest tick age {int(age_seconds)}s"

    integrations = [
        {
            "name": "directory",
            "status": "UP",
            "last_success": now.isoformat(),
            "detail": "mock: last sync simulated",
        },
        {
            "name": "cyberark",
            "status": "UP" if cyberark_up else "DOWN",
            "last_success": now.isoformat() if cyberark_up else None,
            "detail": f"CYBERARK_AVAILABLE={'true' if cyberark_up else 'false (fail-closed)'}",
        },
        {
            "name": "smtp",
            "status": "UP",
            "last_success": now.isoformat(),
            "detail": "mock: delivery simulated",
        },
        {
            "name": "market_feed",
            "status": feed_status,
            "last_success": feed_last_success,
            "detail": feed_detail,
        },
    ]

    outbox_unpublished = await db.scalar(
        select(func.count(OutboxEvent.id)).where(OutboxEvent.published_at.is_(None))
    )

    stuck = (
        (
            await db.execute(
                select(SettlementInstruction).where(
                    SettlementInstruction.lifecycle_state.in_(
                        [LifecycleState.EXECUTED.value, LifecycleState.AFFIRMED.value]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    exceptions = [
        {
            "settlement_id": s.settlement_id,
            "execution_id": s.execution_id,
            "lifecycle_state": s.lifecycle_state,
            "age_seconds": int((now - as_utc(s.created_at)).total_seconds()),
        }
        for s in stuck
        if (now - as_utc(s.created_at)).total_seconds() > STP_EXCEPTION_AGE_SECONDS
    ]

    return {
        "integrations": integrations,
        "outbox_unpublished": outbox_unpublished or 0,
        "stp_exceptions": exceptions,
    }


# ---------------------------------------------------------------------------
# Access-review (who-has-what) export
# ---------------------------------------------------------------------------


@router.get("/admin/access-review")
async def access_review(
    session: SessionData = Depends(require_permission("GOVERNANCE_VIEW")),
    db: AsyncSession = Depends(get_db),
    format: str = Query("csv"),
):
    if format.lower() != "csv":
        raise ValidationError("only format=csv is supported")
    rows = (
        (
            await db.execute(
                select(User, Role, AccessGrant)
                .join(AccessGrant, AccessGrant.user_id == User.user_id)
                .join(Role, Role.role_id == AccessGrant.role_id)
                .where(AccessGrant.status == GrantStatus.ACTIVE.value)
                .order_by(User.email, Role.name)
            )
        )
        .all()
    )

    # The export writes its own audit event, persisted immediately.
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=GOVERNANCE_EXPORT,
        severity="INFO",
        payload={"format": "csv", "rows": len(rows)},
        flush_only=False,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["user_email", "display_name", "role_name", "grant_id", "start_at", "end_at", "status"]
    )
    for user, role, grant in rows:
        writer.writerow(
            [
                user.email,
                user.display_name,
                role.name,
                grant.grant_id,
                as_utc(grant.start_at).isoformat(),
                as_utc(grant.end_at).isoformat(),
                grant.status,
            ]
        )
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="access-review.csv"'},
    )
