"""Restricted-instrument list admin (A4, design 21).

SecAdmin-managed list of symbols blocked in pre-trade order validation
(orders reject with RESTRICTED_INSTRUMENT). Entries are never hard-deleted —
DELETE flips `active` off so the trail stays audit-friendly; a later POST for
the same symbol reactivates the row in place. Every add/remove is audited
with an immediately-committed event (fail closed). Gated on ROLE_MANAGE, so
no seed change is needed on live DBs.

Endpoints (mounted under /api/v1):
- GET    /restricted-instruments            list all rows (active first)
- POST   /restricted-instruments            add or reactivate a restriction
- DELETE /restricted-instruments/{symbol}   deactivate (never hard-delete)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit as audit_log
from app.core.db import get_db
from app.core.errors import NotFound, StateConflict
from app.core.models import Instrument, RestrictedInstrument
from app.core.security import SessionData, require_permission
from app.core.timeutil import as_utc

router = APIRouter(tags=["restricted"])

RESTRICTION_ADDED = "RESTRICTION_ADDED"
RESTRICTION_REMOVED = "RESTRICTION_REMOVED"


class RestrictionRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    reason: str = Field(default="", max_length=500)


def _row_json(row: RestrictedInstrument) -> dict:
    return {
        "symbol": row.symbol,
        "reason": row.reason,
        "active": row.active,
        "created_by": row.created_by,
        "created_at": as_utc(row.created_at).isoformat(),
    }


@router.get("/restricted-instruments")
async def list_restricted(
    session: SessionData = Depends(require_permission("ROLE_MANAGE")),
    db: AsyncSession = Depends(get_db),
):
    """All restricted-list rows, active entries first then alphabetical."""
    rows = (
        (
            await db.execute(
                select(RestrictedInstrument).order_by(
                    RestrictedInstrument.active.desc(), RestrictedInstrument.symbol
                )
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_row_json(r) for r in rows], "next_cursor": None}


@router.post("/restricted-instruments", status_code=201)
async def add_restriction(
    body: RestrictionRequest,
    session: SessionData = Depends(require_permission("ROLE_MANAGE")),
    db: AsyncSession = Depends(get_db),
):
    """Restrict a symbol, or reactivate/update an existing row (upsert)."""
    symbol = body.symbol.strip().upper()
    instrument = (
        await db.execute(select(Instrument).where(Instrument.symbol == symbol))
    ).scalar_one_or_none()
    if instrument is None:
        raise NotFound(f"unknown instrument symbol: {symbol}")

    row = (
        await db.execute(
            select(RestrictedInstrument).where(RestrictedInstrument.symbol == symbol)
        )
    ).scalar_one_or_none()
    if row is None:
        row = RestrictedInstrument(symbol=symbol)
        db.add(row)
    row.reason = body.reason
    row.active = True
    row.created_by = session.user_id
    await db.flush()
    # Security-critical: commits the row change together with the audit event.
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=RESTRICTION_ADDED,
        resource_type="RESTRICTED_INSTRUMENT",
        resource_id=symbol,
        payload={"symbol": symbol, "reason": body.reason},
        flush_only=False,
    )
    return _row_json(row)


@router.delete("/restricted-instruments/{symbol}")
async def remove_restriction(
    symbol: str,
    session: SessionData = Depends(require_permission("ROLE_MANAGE")),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a restriction. 404 if never listed, 409 if already inactive."""
    row = (
        await db.execute(
            select(RestrictedInstrument).where(
                RestrictedInstrument.symbol == symbol.strip().upper()
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(f"instrument not on the restricted list: {symbol}")
    if not row.active:
        raise StateConflict(f"restriction already inactive: {row.symbol}")
    row.active = False
    await db.flush()
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=RESTRICTION_REMOVED,
        resource_type="RESTRICTED_INSTRUMENT",
        resource_id=row.symbol,
        payload={"symbol": row.symbol},
        flush_only=False,
    )
    return _row_json(row)
