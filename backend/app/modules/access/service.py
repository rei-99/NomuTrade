"""Access-governance business logic: approval chains, SoD, grants, JIT sweep.

Approval-chain config (DESIGN TBD-01 defaults, docs/design/08):
- L1 "LINE_MANAGER"     -> users holding an ACTIVE grant for the "Approver" role
- L2 "RESOURCE_OWNER"   -> "Security Administrator" holders
- L3 "SECURITY_OFFICER" -> "Security Administrator" holders; appended when the
  target role is privileged ("System Administrator" / "Security Administrator")
  or the SoD pre-check flags the request.

Duration caps (TBD-02, capped silently at submission): 8 h privileged,
90 days (2160 h) standard. Grant extensions accumulate up to 2x the standard
cap. Grant expiry/revocation mechanics: docs/design/10.

The pam / breakglass / admin packages import the small shared helpers
(notify, role/user resolution, active-grant lookups) from here so the
governance rules live in exactly one place.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import audit as audit_log
from app.core.errors import BusinessRuleViolation, NotFound, StateConflict
from app.core.events import write_outbox
from app.core.models import (
    AccessGrant,
    AccessRequest,
    ApprovalStep,
    Decision,
    GrantStatus,
    RequestStatus,
    Role,
    SoDRule,
    User,
)
from app.core.security import invalidate_permissions
from app.core.timeutil import as_utc, utcnow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Audit event types emitted by this package
# ---------------------------------------------------------------------------

ACCESS_REQUEST_SUBMITTED = "ACCESS_REQUEST_SUBMITTED"
ACCESS_REQUEST_WITHDRAWN = "ACCESS_REQUEST_WITHDRAWN"
ACCESS_REQUEST_DECIDED = "ACCESS_REQUEST_DECIDED"
GRANT_CREATED = "GRANT_CREATED"
GRANT_EXTENDED = "GRANT_EXTENDED"
GRANT_REVOKED = "GRANT_REVOKED"
GRANT_EXPIRED = "GRANT_EXPIRED"
ROLE_CREATED = "ROLE_CREATED"
ROLE_UPDATED = "ROLE_UPDATED"

# ---------------------------------------------------------------------------
# Approval-chain / duration config (module constants)
# ---------------------------------------------------------------------------

LEVEL_LINE_MANAGER = "LINE_MANAGER"
LEVEL_RESOURCE_OWNER = "RESOURCE_OWNER"
LEVEL_SECURITY_OFFICER = "SECURITY_OFFICER"

#: Roles named exactly these are privileged: shorter cap + L3 security sign-off.
PRIVILEGED_ROLE_NAMES = frozenset({"System Administrator", "Security Administrator"})
PRIVILEGED_CAP_HOURS = 8
STANDARD_CAP_HOURS = 90 * 24  # 2160
EXTENSION_CAP_HOURS = 2 * STANDARD_CAP_HOURS  # cumulative cap per grant

#: Level label -> role whose ACTIVE grant holders resolve as its approvers.
LEVEL_APPROVER_ROLE = {
    LEVEL_LINE_MANAGER: "Approver",
    LEVEL_RESOURCE_OWNER: "Security Administrator",
    LEVEL_SECURITY_OFFICER: "Security Administrator",
}

#: SoD conflict matrix seeded idempotently by ensure_seed_data (FR-RBAC-004).
SOD_RULES: list[tuple[str, str, str]] = [
    ("Trader", "Operations Analyst", "FLAGGED"),
    ("Trader", "Security Administrator", "FLAGGED"),
    ("Operations Analyst", "Security Administrator", "BLOCKED"),
]

#: Extension mechanism (POST /grants/{id}/extend): an extension is an ordinary
#: AccessRequest whose justification carries this machine-readable prefix
#: naming the grant to extend ("EXTENSION grant <grant_id>: <free text>"). The
#: final-approval path detects the prefix and extends that grant's end_at
#: instead of creating a new AccessGrant.
EXTENSION_PREFIX = "EXTENSION grant "

OPEN_STATUSES = (RequestStatus.SUBMITTED.value, RequestStatus.PENDING_INFO.value)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def notify(db: AsyncSession, user_id: str, category: str, title: str, body: str) -> None:
    """Queue an in-app notification via the transactional outbox.

    The notifications team consumes the `notify` stream; payload contract:
    {"user_id", "category", "title", "body"}.
    """
    await write_outbox(
        db,
        "notify",
        {"user_id": user_id, "category": category, "title": title, "body": body},
    )


async def get_role_by_name(db: AsyncSession, name: str) -> Role | None:
    return (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()


async def active_grant_holders(db: AsyncSession, role_name: str) -> list[User]:
    """Users holding an ACTIVE, in-window grant for the named role (deduped)."""
    now = utcnow()
    stmt = (
        select(User, AccessGrant.start_at, AccessGrant.end_at)
        .join(AccessGrant, AccessGrant.user_id == User.user_id)
        .join(Role, Role.role_id == AccessGrant.role_id)
        .where(Role.name == role_name, AccessGrant.status == GrantStatus.ACTIVE.value)
    )
    holders: list[User] = []
    seen: set[str] = set()
    for user, start_at, end_at in (await db.execute(stmt)).all():
        if user.user_id in seen:
            continue
        if as_utc(start_at) <= now <= as_utc(end_at):
            seen.add(user.user_id)
            holders.append(user)
    return holders


async def active_grant_for_role(
    db: AsyncSession, user_id: str, role_name: str
) -> AccessGrant | None:
    """The user's ACTIVE, in-window grant for the named role, if any."""
    now = utcnow()
    stmt = (
        select(AccessGrant)
        .join(Role, Role.role_id == AccessGrant.role_id)
        .where(
            AccessGrant.user_id == user_id,
            Role.name == role_name,
            AccessGrant.status == GrantStatus.ACTIVE.value,
        )
    )
    for grant in (await db.execute(stmt)).scalars().all():
        if as_utc(grant.start_at) <= now <= as_utc(grant.end_at):
            return grant
    return None


async def _active_role_ids(db: AsyncSession, user_id: str) -> set[str]:
    now = utcnow()
    stmt = select(AccessGrant.role_id, AccessGrant.start_at, AccessGrant.end_at).where(
        AccessGrant.user_id == user_id,
        AccessGrant.status == GrantStatus.ACTIVE.value,
    )
    return {
        role_id
        for role_id, start_at, end_at in (await db.execute(stmt)).all()
        if as_utc(start_at) <= now <= as_utc(end_at)
    }


# ---------------------------------------------------------------------------
# SoD + approval chain
# ---------------------------------------------------------------------------


async def sod_effect(db: AsyncSession, requester_id: str, role_id: str) -> str | None:
    """Worst SoD effect between the target role and the requester's held roles.

    Returns "BLOCKED", "FLAGGED", or None. Rules are unordered pairs.
    """
    held = await _active_role_ids(db, requester_id)
    held.discard(role_id)  # holding the target role itself is not a conflict
    if not held:
        return None
    rules = (await db.execute(select(SoDRule))).scalars().all()
    effects = [
        rule.effect
        for rule in rules
        if (rule.role_a_id == role_id and rule.role_b_id in held)
        or (rule.role_b_id == role_id and rule.role_a_id in held)
    ]
    if "BLOCKED" in effects:
        return "BLOCKED"
    if "FLAGGED" in effects:
        return "FLAGGED"
    return None


def resolve_chain(role_name: str, sod_flagged: bool) -> list[str]:
    """Ordered approval-level labels for a role (+L3 when privileged/flagged)."""
    chain = [LEVEL_LINE_MANAGER, LEVEL_RESOURCE_OWNER]
    if role_name in PRIVILEGED_ROLE_NAMES or sod_flagged:
        chain.append(LEVEL_SECURITY_OFFICER)
    return chain


def cap_hours(role_name: str, requested_hours: int) -> int:
    """Duration cap, applied silently (TBD-02): 8 h privileged / 90 d standard."""
    cap = (
        PRIVILEGED_CAP_HOURS
        if role_name in PRIVILEGED_ROLE_NAMES
        else STANDARD_CAP_HOURS
    )
    return min(requested_hours, cap)


async def approvers_for_level(db: AsyncSession, level_label: str) -> list[User]:
    return await active_grant_holders(db, LEVEL_APPROVER_ROLE[level_label])


def current_level(request: AccessRequest) -> int:
    """The request's live level: the highest level for which steps exist."""
    return max((s.level for s in request.steps), default=1)


def is_current_approver(request: AccessRequest, user_id: str) -> bool:
    level = current_level(request)
    return any(
        s.approver_id == user_id and s.decision is None and s.level == level
        for s in request.steps
    )


def parse_extension_grant_id(justification: str) -> str | None:
    """Grant id embedded by POST /grants/{id}/extend, else None (see EXTENSION_PREFIX)."""
    if not justification.startswith(EXTENSION_PREFIX):
        return None
    rest = justification[len(EXTENSION_PREFIX) :]
    grant_id = rest.partition(":")[0].strip()
    return grant_id or None


# ---------------------------------------------------------------------------
# Request submission (shared by POST /access-requests and grant extension)
# ---------------------------------------------------------------------------


async def has_open_duplicate(
    db: AsyncSession, *, requester_id: str, on_behalf_of_id: str | None, role_id: str
) -> bool:
    stmt = select(func.count(AccessRequest.request_id)).where(
        AccessRequest.requester_id == requester_id,
        AccessRequest.on_behalf_of == on_behalf_of_id,  # None renders IS NULL
        AccessRequest.role_id == role_id,
        AccessRequest.status.in_(OPEN_STATUSES),
    )
    return bool(await db.scalar(stmt))


async def submit_access_request(
    db: AsyncSession,
    *,
    requester_id: str,
    requester_email: str,
    role: Role,
    justification: str,
    requested_hours: int,
    on_behalf_of_id: str | None,
) -> tuple[AccessRequest, list[str]]:
    """Create a SUBMITTED AccessRequest + its first-level ApprovalStep(s).

    Runs the duplicate check (409) and the SoD pre-check (422 when BLOCKED),
    caps the duration silently, audits ACCESS_REQUEST_SUBMITTED and notifies
    the first-level approvers. The caller (route) commits.
    Returns (request, chain labels).
    """
    if await has_open_duplicate(
        db, requester_id=requester_id, on_behalf_of_id=on_behalf_of_id, role_id=role.role_id
    ):
        raise StateConflict("an open access request already exists for this user and role")

    effect = await sod_effect(db, requester_id, role.role_id)
    if effect == "BLOCKED":
        raise BusinessRuleViolation(
            f"segregation-of-duties violation: role '{role.name}' conflicts "
            "with a role you already hold (BLOCKED)"
        )

    chain = resolve_chain(role.name, sod_flagged=effect == "FLAGGED")
    request = AccessRequest(
        requester_id=requester_id,
        on_behalf_of=on_behalf_of_id,
        role_id=role.role_id,
        justification=justification,
        requested_duration_hours=cap_hours(role.name, requested_hours),
        status=RequestStatus.SUBMITTED.value,
    )
    db.add(request)
    await db.flush()

    approvers = await approvers_for_level(db, chain[0])
    for approver in approvers:
        db.add(
            ApprovalStep(
                request_id=request.request_id,
                level=1,
                approver_id=approver.user_id,
            )
        )
    await db.flush()

    await audit_log.write_audit(
        db,
        actor_id=requester_id,
        event_type=ACCESS_REQUEST_SUBMITTED,
        resource_type="ACCESS_REQUEST",
        resource_id=request.request_id,
        payload={
            "role": role.name,
            "requested_duration_hours": request.requested_duration_hours,
            "on_behalf_of": on_behalf_of_id,
            "sod_effect": effect,
            "chain": chain,
        },
    )
    for approver in approvers:
        await notify(
            db,
            approver.user_id,
            "ACCESS",
            "Access request awaiting your approval",
            f"{requester_email} requested role '{role.name}'"
            + (f" on behalf of {on_behalf_of_id}." if on_behalf_of_id else "."),
        )
    return request, chain


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


async def apply_decision(
    db: AsyncSession,
    *,
    step: ApprovalStep,
    request: AccessRequest,
    role: Role,
    approver_id: str,
    decision: str,
    comment: str,
) -> str:
    """Apply an approver decision. Returns "REJECTED" | "NEXT_LEVEL" | "APPROVED".

    Final approval creates the AccessGrant (or extends an existing one for
    EXTENSION_PREFIX requests) in the same transaction as the request update;
    security-critical audits use flush_only=False.
    """
    now = utcnow()
    step.decision = decision
    step.comment = comment
    step.decided_at = now

    async def _audit_decision(payload: dict) -> None:
        await audit_log.write_audit(
            db,
            actor_id=approver_id,
            event_type=ACCESS_REQUEST_DECIDED,
            resource_type="ACCESS_REQUEST",
            resource_id=request.request_id,
            payload={"role": role.name, "level": step.level, **payload},
            flush_only=False,  # decisions are security-critical
        )

    if decision == Decision.REJECTED.value:
        request.status = RequestStatus.REJECTED.value
        request.decided_at = now
        await _audit_decision({"decision": "REJECTED", "comment": comment})
        await notify(
            db,
            request.requester_id,
            "ACCESS",
            "Access request rejected",
            f"Your request for role '{role.name}' was rejected: {comment}",
        )
        await db.commit()
        return "REJECTED"

    # APPROVED: more levels, or final?
    effect = await sod_effect(db, request.requester_id, role.role_id)
    chain = resolve_chain(role.name, sod_flagged=effect == "FLAGGED")

    if step.level < len(chain):
        next_approvers = await approvers_for_level(db, chain[step.level])
        for approver in next_approvers:
            db.add(
                ApprovalStep(
                    request_id=request.request_id,
                    level=step.level + 1,
                    approver_id=approver.user_id,
                )
            )
        await db.flush()
        await _audit_decision({"decision": "APPROVED", "next_level": step.level + 1})
        for approver in next_approvers:
            await notify(
                db,
                approver.user_id,
                "ACCESS",
                "Access request awaiting your approval",
                f"A request for role '{role.name}' needs your level {step.level + 1} decision.",
            )
        await db.commit()
        return "NEXT_LEVEL"

    # Final approval: request + grant in one transaction (the first
    # flush_only=False audit below commits step + request + grant atomically).
    request.status = RequestStatus.APPROVED.value
    request.decided_at = now

    extension_grant_id = parse_extension_grant_id(request.justification)
    if extension_grant_id is not None:
        # Extension path (see EXTENSION_PREFIX): end_at += requested hours,
        # cumulative duration capped at 2x the standard cap; no new grant row.
        grant = await db.get(AccessGrant, extension_grant_id)
        if grant is None:
            raise NotFound("extension target grant not found")
        if grant.status != GrantStatus.ACTIVE.value:
            raise StateConflict("extension target grant is no longer active")
        new_end = min(
            as_utc(grant.end_at) + timedelta(hours=request.requested_duration_hours),
            as_utc(grant.start_at) + timedelta(hours=EXTENSION_CAP_HOURS),
        )
        grant.end_at = new_end
        await _audit_decision({"decision": "APPROVED", "final": True, "extension_of": grant.grant_id})
        await audit_log.write_audit(
            db,
            actor_id=approver_id,
            event_type=GRANT_EXTENDED,
            resource_type="ACCESS_GRANT",
            resource_id=grant.grant_id,
            payload={
                "user_id": grant.user_id,
                "role": role.name,
                "added_hours": request.requested_duration_hours,
                "new_end_at": new_end.isoformat(),
            },
            flush_only=False,
        )
        invalidate_permissions(grant.user_id)
        await notify(
            db,
            request.requester_id,
            "GRANT",
            "Access grant extended",
            f"Your '{role.name}' grant was extended to {new_end.isoformat()}.",
        )
        await db.commit()
        return "APPROVED"

    beneficiary_id = request.on_behalf_of or request.requester_id
    grant = AccessGrant(
        user_id=beneficiary_id,
        role_id=role.role_id,
        request_id=request.request_id,
        start_at=now,
        end_at=now + timedelta(hours=request.requested_duration_hours),
        status=GrantStatus.ACTIVE.value,
    )
    db.add(grant)
    await db.flush()
    await _audit_decision({"decision": "APPROVED", "final": True, "grant_id": grant.grant_id})
    await audit_log.write_audit(
        db,
        actor_id=approver_id,
        event_type=GRANT_CREATED,
        resource_type="ACCESS_GRANT",
        resource_id=grant.grant_id,
        payload={
            "user_id": beneficiary_id,
            "role": role.name,
            "duration_hours": request.requested_duration_hours,
            "end_at": grant.end_at.isoformat(),
        },
        flush_only=False,
    )
    invalidate_permissions(beneficiary_id)
    await notify(
        db,
        request.requester_id,
        "GRANT",
        "Access grant active",
        f"Your request for role '{role.name}' was approved; grant active until {grant.end_at.isoformat()}.",
    )
    await db.commit()
    return "APPROVED"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


async def _users_by_id(db: AsyncSession, ids) -> dict[str, User]:
    unique = [i for i in set(ids) if i]
    if not unique:
        return {}
    rows = (await db.execute(select(User).where(User.user_id.in_(unique)))).scalars().all()
    return {u.user_id: u for u in rows}


async def request_json(db: AsyncSession, request: AccessRequest) -> dict:
    """The Request JSON shape consumed by the frontend."""
    users = await _users_by_id(
        db,
        [request.requester_id, request.on_behalf_of]
        + [s.approver_id for s in request.steps],
    )
    role = await db.get(Role, request.role_id)

    def ujson(user_id: str | None) -> dict | None:
        user = users.get(user_id) if user_id else None
        return {"email": user.email, "display_name": user.display_name} if user else None

    return {
        "request_id": request.request_id,
        "requester": ujson(request.requester_id),
        "on_behalf_of": ujson(request.on_behalf_of) if request.on_behalf_of else None,
        "role": {"role_id": role.role_id, "name": role.name} if role else None,
        "justification": request.justification,
        "requested_duration_hours": request.requested_duration_hours,
        "status": request.status,
        "created_at": as_utc(request.created_at).isoformat(),
        "decided_at": as_utc(request.decided_at).isoformat() if request.decided_at else None,
        "steps": [
            {
                "step_id": s.step_id,
                "level": s.level,
                "approver": ujson(s.approver_id),
                "decision": s.decision,
                "comment": s.comment,
                "decided_at": as_utc(s.decided_at).isoformat() if s.decided_at else None,
            }
            for s in sorted(request.steps, key=lambda s: s.level)
        ],
    }


def grant_json(grant: AccessGrant) -> dict:
    """The Grant JSON shape consumed by the frontend (user/role selectin-loaded)."""
    return {
        "grant_id": grant.grant_id,
        "user": {"email": grant.user.email, "display_name": grant.user.display_name},
        "role": {"role_id": grant.role.role_id, "name": grant.role.name},
        "request_id": grant.request_id,
        "start_at": as_utc(grant.start_at).isoformat(),
        "end_at": as_utc(grant.end_at).isoformat(),
        "status": grant.status,
        "revoked_by": grant.revoked_by,
        "revoked_reason": grant.revoked_reason,
    }


def role_json(role: Role) -> dict:
    return {
        "role_id": role.role_id,
        "name": role.name,
        "description": role.description,
        "built_in": role.built_in,
        "version": role.version,
        "status": role.status,
        "permission_actions": sorted(p.action for p in role.permissions),
    }


# ---------------------------------------------------------------------------
# Idempotent SoD seed (worker startup task)
# ---------------------------------------------------------------------------


async def ensure_seed_data(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Seed the SoD conflict matrix idempotently (safe to call concurrently).

    Startup runs this concurrently with other modules' seeders; SQLite
    serializes writers and concurrent seed transactions can deadlock until
    the (5 s default) busy timeout. A short per-connection busy timeout plus
    retry keeps seeding fast and, importantly for short-lived processes,
    keeps the wait bounded so task cancellation is not held up for seconds.
    """
    max_attempts = 10
    for attempt in range(max_attempts):
        try:
            async with sessionmaker() as session:
                await session.execute(text("PRAGMA busy_timeout=500"))
                for role_a_name, role_b_name, effect in SOD_RULES:
                    role_a = await get_role_by_name(session, role_a_name)
                    role_b = await get_role_by_name(session, role_b_name)
                    if role_a is None or role_b is None:
                        continue
                    existing = await session.get(
                        SoDRule, (role_a.role_id, role_b.role_id)
                    ) or await session.get(SoDRule, (role_b.role_id, role_a.role_id))
                    if existing is None:
                        session.add(
                            SoDRule(
                                role_a_id=role_a.role_id,
                                role_b_id=role_b.role_id,
                                effect=effect,
                            )
                        )
                await session.execute(text("PRAGMA busy_timeout=5000"))
                await session.commit()
            return
        except IntegrityError:
            # A concurrent seeder won the race; the rules are present either way.
            return
        except OperationalError:
            # Lock contention with another seeder: back off and retry.
            await asyncio.sleep(0.1 * (attempt + 1))
        except Exception:
            # Worker path must never take down the supervisor; direct callers
            # (tests) can re-invoke. Log for diagnosis.
            logger.exception("ensure_seed_data (SoD rules) failed")
            return
    logger.error("ensure_seed_data (SoD rules) gave up after %d attempts", max_attempts)


# ---------------------------------------------------------------------------
# JIT expiry sweep (worker + importable for tests)
# ---------------------------------------------------------------------------

SWEEP_INTERVAL_SECONDS = 30.0


async def sweep_expired_grants(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """One JIT sweep: ACTIVE grants past end_at -> EXPIRED.

    Writes GRANT_EXPIRED audit (flush_only=False: grant lifecycle changes are
    security-critical), invalidates the permission cache and notifies the
    grantee plus one Security Administrator. Returns the number expired.
    """
    async with sessionmaker() as session:
        now = utcnow()
        grants = (
            (await session.execute(select(AccessGrant).where(AccessGrant.status == GrantStatus.ACTIVE.value)))
            .scalars()
            .all()
        )
        expired = [g for g in grants if as_utc(g.end_at) <= now]
        if not expired:
            return 0
        secadmins = await active_grant_holders(session, "Security Administrator")
        for grant in expired:
            grant.status = GrantStatus.EXPIRED.value
            role_name = grant.role.name if grant.role else None
            await audit_log.write_audit(
                session,
                actor_id=None,
                event_type=GRANT_EXPIRED,
                resource_type="ACCESS_GRANT",
                resource_id=grant.grant_id,
                payload={
                    "user_id": grant.user_id,
                    "role": role_name,
                    "end_at": as_utc(grant.end_at).isoformat(),
                },
                flush_only=False,
            )
            invalidate_permissions(grant.user_id)
            await notify(
                session,
                grant.user_id,
                "GRANT",
                "Access grant expired",
                f"Your '{role_name}' grant has expired.",
            )
            for secadmin in secadmins[:1]:
                await notify(
                    session,
                    secadmin.user_id,
                    "GRANT",
                    "Access grant expired",
                    f"Grant {grant.grant_id} ('{role_name}') expired for user {grant.user_id}.",
                )
        await session.commit()
        return len(expired)


async def jit_expiry(bus, sessionmaker) -> None:
    """Worker: sweep expired grants every 30 s (docs/design/10).

    Sleeps before the first sweep so app startup and short-lived processes
    never see sweep DB traffic at t=0.
    """
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            await sweep_expired_grants(sessionmaker)
        except Exception:
            logger.exception("jit_expiry sweep failed; retrying next interval")
