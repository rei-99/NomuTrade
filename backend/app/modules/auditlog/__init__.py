"""Audit log search & export (FR-AUD-003, docs/design/13).

Read-only API over the append-only AuditEvent store: a mandatory date range,
optional filters, opaque-offset cursor pagination, and file export that writes
its own AUDIT_EXPORTED audit event.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.core import audit as audit_log
from app.core.db import get_db
from app.core.errors import BusinessRuleViolation, ValidationError
from app.core.models import AuditEvent, User
from app.core.security import SessionData, require_permission
from app.core.timeutil import as_utc

router = APIRouter(tags=["audit"])

AUDIT_EXPORTED = "AUDIT_EXPORTED"
EXPORT_ROW_CAP = 10_000

CSV_COLUMNS = [
    "event_id",
    "ts",
    "actor_email",
    "event_type",
    "resource_type",
    "resource_id",
    "severity",
    "source_ip",
    "correlation_id",
    "payload",
]


# ---------------------------------------------------------------------------
# Parameter parsing / cursor
# ---------------------------------------------------------------------------


def _parse_bound(value: str, name: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(
            f"invalid '{name}' parameter: use ISO 8601 UTC "
            f"(e.g. 2026-01-01T00:00:00Z), got {value!r}"
        )
    return as_utc(dt)  # naive inputs are treated as UTC


def _require_range(from_: str | None, to: str | None) -> tuple[datetime, datetime]:
    if not from_ or not to:
        raise ValidationError(
            "both 'from' and 'to' query parameters are required (ISO 8601 UTC, "
            "e.g. ?from=2026-01-01T00:00:00Z&to=2026-01-02T00:00:00Z)"
        )
    start, end = _parse_bound(from_, "from"), _parse_bound(to, "to")
    if end < start:
        raise ValidationError("'to' must be at or after 'from'")
    return start, end


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode()


def _decode_cursor(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        decoded = base64.urlsafe_b64decode(raw.encode()).decode()
        kind, _, value = decoded.partition(":")
        if kind != "offset":
            raise ValueError(decoded)
        return max(0, int(value))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValidationError("invalid 'cursor' parameter")


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


class _Filters:
    def __init__(self, start, end, actor, event_type, resource_type, resource_id, severity):
        self.start = start
        self.end = end
        self.actor = actor
        self.event_type = event_type
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.severity = severity

    def conditions(self) -> list:
        # AuditEvent.ts is stored as UTC; the SQLite dialect binds datetimes
        # without tzinfo, so tz-aware bounds compare correctly as UTC.
        conds = [AuditEvent.ts >= self.start, AuditEvent.ts <= self.end]
        if self.event_type:
            conds.append(AuditEvent.event_type == self.event_type)
        if self.resource_type:
            conds.append(AuditEvent.resource_type == self.resource_type)
        if self.resource_id:
            conds.append(AuditEvent.resource_id == self.resource_id)
        if self.severity:
            conds.append(AuditEvent.severity == self.severity.upper())
        if self.actor:
            conds.append(User.email.ilike(f"%{self.actor}%"))
        return conds

    def as_payload(self, from_: str, to: str) -> dict:
        return {
            "from": from_,
            "to": to,
            "actor": self.actor,
            "event_type": self.event_type,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "severity": self.severity,
        }


def _base_stmt(conds: list):
    return (
        select(AuditEvent, User.email)
        .join(User, User.user_id == AuditEvent.actor_id, isouter=True)
        .where(*conds)
    )


def _event_json(event: AuditEvent, actor_email: str | None) -> dict:
    return {
        "event_id": event.event_id,
        "ts": as_utc(event.ts).isoformat(),
        "actor_email": actor_email,
        "event_type": event.event_type,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "severity": event.severity,
        "source_ip": event.source_ip,
        "correlation_id": event.correlation_id,
        "payload": event.payload,
    }


def _filters(
    from_: str | None,
    to: str | None,
    actor: str | None,
    event_type: str | None,
    resource_type: str | None,
    resource_id: str | None,
    severity: str | None,
) -> _Filters:
    start, end = _require_range(from_, to)
    return _Filters(start, end, actor, event_type, resource_type, resource_id, severity)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/audit-events")
async def search_audit_events(
    session: SessionData = Depends(require_permission("AUDIT_VIEW")),
    db: AsyncSession = Depends(get_db),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    actor: str | None = Query(None),
    event_type: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    severity: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
):
    filters = _filters(from_, to, actor, event_type, resource_type, resource_id, severity)
    offset = _decode_cursor(cursor)
    stmt = (
        _base_stmt(filters.conditions())
        .order_by(AuditEvent.seq.desc())
        .offset(offset)
        .limit(limit + 1)  # one extra row tells us whether a next page exists
    )
    rows = (await db.execute(stmt)).all()
    page = rows[:limit]
    next_cursor = _encode_cursor(offset + limit) if len(rows) > limit else None
    return {
        "items": [_event_json(event, email) for event, email in page],
        "next_cursor": next_cursor,
    }


@router.get("/audit-events/export")
async def export_audit_events(
    session: SessionData = Depends(require_permission("AUDIT_EXPORT")),
    db: AsyncSession = Depends(get_db),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    actor: str | None = Query(None),
    event_type: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    severity: str | None = Query(None),
    format: str = Query("json"),
):
    filters = _filters(from_, to, actor, event_type, resource_type, resource_id, severity)
    fmt = format.lower()
    if fmt not in ("csv", "json"):
        raise ValidationError("format must be 'csv' or 'json'")
    conds = filters.conditions()
    total = await db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .join(User, User.user_id == AuditEvent.actor_id, isouter=True)
        .where(*conds)
    )
    if total and total > EXPORT_ROW_CAP:
        raise BusinessRuleViolation(
            f"export would return {total} rows (cap {EXPORT_ROW_CAP}); "
            "narrow the date range or add filters"
        )
    rows = (await db.execute(_base_stmt(conds).order_by(AuditEvent.seq))).all()
    items = [_event_json(event, email) for event, email in rows]

    # The export writes its own audit event (FR-AUD-003), persisted immediately.
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=AUDIT_EXPORTED,
        severity="INFO",
        payload={
            "format": fmt,
            "rows": len(items),
            "filters": filters.as_payload(from_, to),
        },
        flush_only=False,
    )

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    **item,
                    "payload": json.dumps(item["payload"]) if item["payload"] is not None else "",
                }
            )
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="audit-events.csv"'},
        )
    return StreamingResponse(
        iter([json.dumps(items, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="audit-events.json"'},
    )
