"""Hash-chained, append-only audit writer (single choke point).

payload_hash = sha256(canonical_json(actor, ts, type, resource, payload) + prev_hash)
where prev_hash is the payload_hash of the most recent AuditEvent (ordered by
the autoincrement `seq` column). The chain makes tampering evident.

Security-critical callers (authorization denials, login success/failure,
checkout, break-glass) should use flush_only=False so the record is committed
immediately, in the request path (fail closed). Lower-value events may flush
only and commit with the surrounding business transaction.
"""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import current_trace_id
from app.core.models import AuditEvent
from app.core.timeutil import utcnow

# Event types used by the foundation.
AUTH_LOGIN_SUCCESS = "AUTH_LOGIN_SUCCESS"
AUTH_LOGIN_FAILURE = "AUTH_LOGIN_FAILURE"
AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"


def _canonical_json(
    actor_id: str | None,
    ts,
    event_type: str,
    resource_type: str | None,
    resource_id: str | None,
    payload: dict | None,
) -> str:
    return json.dumps(
        {
            "actor_id": actor_id,
            "ts": ts.isoformat(),
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


async def write_audit(
    session: AsyncSession,
    *,
    actor_id: str | None,
    event_type: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    severity: str = "INFO",
    source_ip: str | None = None,
    correlation_id: str | None = None,
    payload: dict | None = None,
    flush_only: bool = True,
) -> AuditEvent:
    """Append a hash-chained AuditEvent.

    flush_only=True: flush within the caller's transaction (caller commits).
    flush_only=False: commit immediately (security-critical events).
    """
    prev_hash = (
        await session.execute(
            select(AuditEvent.payload_hash).order_by(AuditEvent.seq.desc()).limit(1)
        )
    ).scalar_one_or_none()
    ts = utcnow()
    canonical = _canonical_json(
        actor_id, ts, event_type, resource_type, resource_id, payload
    )
    payload_hash = hashlib.sha256(
        (canonical + (prev_hash or "")).encode("utf-8")
    ).hexdigest()
    event = AuditEvent(
        ts=ts,
        actor_id=actor_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        severity=str(severity),
        source_ip=source_ip,
        correlation_id=correlation_id or current_trace_id(),
        payload_hash=payload_hash,
        prev_hash=prev_hash,
        payload=payload,
    )
    session.add(event)
    await session.flush()
    if not flush_only:
        await session.commit()
    return event
