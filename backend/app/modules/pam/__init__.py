"""CyberArk PAM (FR-CPAM): mock PVWA adapter + credential checkout endpoints.

The MockCyberArkClient mirrors the PVWA interface (logon / retrieve / check_in
/ request_rotation) against in-memory safes. Credentials live only in
request-scoped memory and are never logged or persisted (CredentialCheckout
stores metadata only). Fail-closed: when env CYBERARK_AVAILABLE=false every
adapter call raises DependencyUnavailable (503), which is also what
/admin/health reports.
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit as audit_log
from app.core.db import get_db
from app.core.errors import (
    DependencyUnavailable,
    Forbidden,
    NotFound,
    StateConflict,
)
from app.core.models import AccessGrant, CredentialCheckout
from app.core.security import (
    SessionData,
    get_current_user,
    require_permission,
)
from app.core.timeutil import as_utc, utcnow
from app.modules.access.service import active_grant_for_role

router = APIRouter(tags=["pam"])

PAM_CHECKOUT = "PAM_CHECKOUT"
PAM_CHECKOUT_FAILED = "PAM_CHECKOUT_FAILED"
PAM_CHECKIN = "PAM_CHECKIN"
PAM_CHECKIN_FAILED = "PAM_CHECKIN_FAILED"

#: Checkout requires an ACTIVE, in-window grant for this role (docs/design/11).
CHECKOUT_GRANT_ROLE = "System Administrator"


class MockCyberArkClient:
    """In-memory PVWA-style adapter: logon / retrieve / check_in / request_rotation.

    Availability is read from the CYBERARK_AVAILABLE env var on every call:
    "false" -> DependencyUnavailable (the fail-closed path).
    """

    SAFES: dict[str, list[str]] = {
        "INFRA": ["root-db", "svc-deploy"],
        "APP": ["stp-db-owner"],
    }

    def __init__(self) -> None:
        self._token: str | None = None

    @staticmethod
    def _check_available() -> None:
        if os.environ.get("CYBERARK_AVAILABLE", "true").strip().lower() == "false":
            raise DependencyUnavailable("CyberArk PVWA is unavailable")

    async def logon(self) -> str:
        """Application logon (cert-based in production; mocked here)."""
        self._check_available()
        self._token = f"mock-pvwa-session-{uuid.uuid4().hex}"
        return self._token

    async def retrieve(self, safe_name: str, account_id: str) -> str:
        """Retrieve (check out) an account's credential. Memory only."""
        self._check_available()
        if self._token is None:
            await self.logon()
        if account_id not in self.SAFES.get(safe_name, []):
            raise NotFound(f"account '{account_id}' not found in safe '{safe_name}'")
        return f"mock-credential:{safe_name}/{account_id}:{uuid.uuid4().hex}"

    async def check_in(self, safe_name: str, account_id: str) -> None:
        self._check_available()

    async def request_rotation(self, safe_name: str, account_id: str) -> None:
        """Ask the CPM to rotate the credential after check-in."""
        self._check_available()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class CreateCheckout(BaseModel):
    safe_name: str = Field(min_length=1)
    account_id: str = Field(min_length=1)


@router.post("/pam/checkouts", status_code=201)
async def create_checkout(
    body: CreateCheckout,
    request: Request,
    session: SessionData = Depends(require_permission("PAM_CHECKOUT")),
    db: AsyncSession = Depends(get_db),
):
    grant = await active_grant_for_role(db, session.user_id, CHECKOUT_GRANT_ROLE)
    if grant is None:
        raise Forbidden(
            f"an active '{CHECKOUT_GRANT_ROLE}' grant is required for credential checkout"
        )
    source_ip = request.client.host if request.client else None
    client = MockCyberArkClient()
    try:
        await client.logon()
        credential = await client.retrieve(body.safe_name, body.account_id)
    except DependencyUnavailable:
        # Fail closed, and audit the failed checkout attempt synchronously.
        await audit_log.write_audit(
            db,
            actor_id=session.user_id,
            event_type=PAM_CHECKOUT_FAILED,
            resource_type="PAM_ACCOUNT",
            resource_id=f"{body.safe_name}/{body.account_id}",
            severity="HIGH",
            source_ip=source_ip,
            payload={"safe_name": body.safe_name, "account_id": body.account_id,
                     "grant_id": grant.grant_id, "reason": "CyberArk unavailable"},
            flush_only=False,
        )
        raise
    # Metadata only — the credential itself is never persisted (FR-CPAM-001).
    checkout = CredentialCheckout(
        grant_id=grant.grant_id,
        safe_name=body.safe_name,
        account_id=body.account_id,
        source_ip=source_ip,
    )
    db.add(checkout)
    await db.flush()
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=PAM_CHECKOUT,
        resource_type="CREDENTIAL_CHECKOUT",
        resource_id=checkout.checkout_id,
        severity="HIGH",
        source_ip=source_ip,
        payload={
            "safe_name": body.safe_name,
            "account_id": body.account_id,
            "grant_id": grant.grant_id,
        },
        flush_only=False,  # checkout audit is synchronous, fail-closed (FR-CPAM-003 E1)
    )
    return {
        "checkout_id": checkout.checkout_id,
        "safe_name": checkout.safe_name,
        "account_id": checkout.account_id,
        "credential": credential,
        "checked_out_at": as_utc(checkout.checked_out_at).isoformat(),
    }


@router.post("/pam/checkouts/{checkout_id}/checkin")
async def checkin(
    checkout_id: str,
    request: Request,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    checkout = await db.get(CredentialCheckout, checkout_id)
    if checkout is None:
        raise NotFound("checkout not found")
    grant = await db.get(AccessGrant, checkout.grant_id)
    if grant is None or grant.user_id != session.user_id:
        raise Forbidden("only the checkout owner may check in")
    if checkout.checked_in_at is not None:
        raise StateConflict("checkout already checked in")
    source_ip = request.client.host if request.client else None
    client = MockCyberArkClient()
    try:
        await client.logon()
        await client.check_in(checkout.safe_name, checkout.account_id)
        await client.request_rotation(checkout.safe_name, checkout.account_id)
    except DependencyUnavailable:
        await audit_log.write_audit(
            db,
            actor_id=session.user_id,
            event_type=PAM_CHECKIN_FAILED,
            resource_type="CREDENTIAL_CHECKOUT",
            resource_id=checkout.checkout_id,
            severity="HIGH",
            source_ip=source_ip,
            payload={"reason": "CyberArk unavailable"},
            flush_only=False,
        )
        raise
    checkout.checked_in_at = utcnow()
    await audit_log.write_audit(
        db,
        actor_id=session.user_id,
        event_type=PAM_CHECKIN,
        resource_type="CREDENTIAL_CHECKOUT",
        resource_id=checkout.checkout_id,
        severity="HIGH",
        source_ip=source_ip,
        payload={"safe_name": checkout.safe_name, "account_id": checkout.account_id},
        flush_only=False,
    )
    return {
        "checkout_id": checkout.checkout_id,
        "safe_name": checkout.safe_name,
        "account_id": checkout.account_id,
        "checked_out_at": as_utc(checkout.checked_out_at).isoformat(),
        "checked_in_at": as_utc(checkout.checked_in_at).isoformat(),
    }
