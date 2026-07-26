"""Integration tests for the access-governance modules:
access (requests/approvals/roles/grants/JIT), pam, breakglass, auditlog, admin.

Own fixtures run the real app lifespan with RUN_WORKERS=True (jit expiry
sweep, SoD/eligibility seeders, outbox relay). Each test gets a fresh
SQLite DB via tmp_path. Other teams' module workers (tick replayer, order
pipeline, ...) are patched out: these tests must not depend on their
modules, and their periodic DB writes make lifespan teardown racy under
SQLite (cancellation landing mid-write can stall engine disposal).
"""

import asyncio
import importlib
import pkgutil
from datetime import timedelta

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

import app.core.security as security
from app.config import Settings
from app.core.models import (
    AccessGrant,
    AuditEvent,
    CredentialCheckout,
    GrantStatus,
    OutboxEvent,
    Role,
    User,
)
from app.core.timeutil import as_utc, utcnow
from app.main import create_app
from app.modules.access import ensure_seed_data as ensure_sod_rules
from app.modules.access import sweep_expired_grants
from app.modules.breakglass import ensure_seed_data as ensure_bg_eligibility
from conftest import login

API = "/api/v1"


# ---------------------------------------------------------------------------
# Fixtures (RUN_WORKERS=True so module workers run during the lifespan)
# ---------------------------------------------------------------------------


@pytest.fixture
def gov_settings(tmp_path):
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/governance.db",
        EVENT_BUS="memory",
        SESSION_STORE="memory",
        RUN_WORKERS=True,
        DEV_AUTH=True,
    )


@pytest.fixture
async def gov_app(gov_settings, monkeypatch):
    """App with RUN_WORKERS=True, but only the governance workers active.

    The outbox relay (core) plus the access/breakglass workers run for real;
    every other module's get_workers is patched to [] so these tests stay
    independent of parallel teams' code and their periodic DB writers.
    """
    import app.modules as modules_pkg

    keep = {"access", "breakglass"}
    for info in pkgutil.iter_modules(modules_pkg.__path__):
        if not info.ispkg or info.name in keep:
            continue
        module = importlib.import_module(f"app.modules.{info.name}")
        monkeypatch.setattr(module, "get_workers", lambda settings: [], raising=False)
    return create_app(gov_settings)


@pytest.fixture
async def gov_client(gov_app):
    manager = LifespanManager(gov_app)
    await manager.__aenter__()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://t"
        ) as http_client:
            yield http_client
    finally:
        # Graceful worker pre-stop: the lifespan's own teardown cancels the
        # worker supervisor and immediately disposes the engine; a worker
        # cancelled mid-query leaves a shielded session-close task that can
        # race engine disposal and stall it. Cancelling here, awaiting, and
        # allowing a short grace period lets those closes finish while the
        # engine is alive, so the lifespan exit below is fast and clean.
        worker_task = gov_app.state.worker_task
        if worker_task is not None and not worker_task.done():
            worker_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(worker_task, return_exceptions=True), timeout=5
                )
            except TimeoutError:
                pass
        await asyncio.sleep(0.1)
        await manager.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _me(client, headers):
    response = await client.get(f"{API}/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _submit_request(client, headers, role="Auditor", hours=720, justification="need it"):
    response = await client.post(
        f"{API}/access-requests",
        headers=headers,
        json={
            "target_role": role,
            "justification": justification,
            "requested_duration_hours": hours,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _first_pending_step(client, headers):
    response = await client.get(f"{API}/approvals", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, "expected a pending approval step"
    return items[0]


async def _decide(client, headers, step_id, decision, comment):
    response = await client.post(
        f"{API}/approvals/{step_id}/decision",
        headers=headers,
        json={"decision": decision, "comment": comment},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _approve_fully(client, approver_headers, secadmin_headers):
    """Approve L1 (Approver holder) then L2 (Security Administrator holder)."""
    step = await _first_pending_step(client, approver_headers)
    _decide_result = await _decide(client, approver_headers, step["step_id"], "APPROVED", "L1 ok")
    step = await _first_pending_step(client, secadmin_headers)
    await _decide(client, secadmin_headers, step["step_id"], "APPROVED", "L2 ok")
    return _decide_result


async def _audit_rows(app, *event_types):
    async with app.state.sessionmaker() as session:
        rows = (
            (await session.execute(select(AuditEvent).order_by(AuditEvent.seq)))
            .scalars()
            .all()
        )
    return [r for r in rows if not event_types or r.event_type in event_types]


async def _notify_payloads(app):
    async with app.state.sessionmaker() as session:
        rows = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.stream == "notify")))
            .scalars()
            .all()
        )
    return [r.payload for r in rows]


async def _grant_for(app, email, role_name):
    async with app.state.sessionmaker() as session:
        return (
            (
                await session.execute(
                    select(AccessGrant)
                    .join(User, User.user_id == AccessGrant.user_id)
                    .join(Role, Role.role_id == AccessGrant.role_id)
                    .where(
                        User.email == email,
                        Role.name == role_name,
                        AccessGrant.status == GrantStatus.ACTIVE.value,
                    )
                )
            )
            .scalars()
            .first()
        )


# ---------------------------------------------------------------------------
# 1. Full approval flow
# ---------------------------------------------------------------------------


async def test_full_approval_flow(gov_app, gov_client):
    trader = await login(gov_client, "trader@demo.nomura")
    approver = await login(gov_client, "approver@demo.nomura")
    secadmin = await login(gov_client, "secadmin@demo.nomura")

    created = await _submit_request(gov_client, trader, role="Auditor", hours=720)
    assert created["status"] == "SUBMITTED"
    assert created["current_level"] == 1
    assert created["levels"] == ["LINE_MANAGER", "RESOURCE_OWNER"]

    # L1: approver@ sees the step with full request context and approves.
    step = await _first_pending_step(gov_client, approver)
    assert step["level"] == 1
    assert step["request"]["requester"]["email"] == "trader@demo.nomura"
    assert step["request"]["role"]["name"] == "Auditor"
    await _decide(gov_client, approver, step["step_id"], "APPROVED", "manager ok")

    # L2: secadmin@ approves -> terminal APPROVED + grant.
    step = await _first_pending_step(gov_client, secadmin)
    assert step["level"] == 2
    final = await _decide(gov_client, secadmin, step["step_id"], "APPROVED", "security ok")
    assert final["status"] == "APPROVED"
    assert final["decided_at"] is not None
    assert len(final["steps"]) == 2

    # Effective permissions now include the Auditor role's AUDIT_VIEW.
    me = await _me(gov_client, trader)
    assert "Auditor" in me["roles"]
    assert "AUDIT_VIEW" in me["permissions"]

    # Audit trail: submission, two decisions, grant creation.
    types = [e.event_type for e in await _audit_rows(gov_app)]
    assert "ACCESS_REQUEST_SUBMITTED" in types
    assert types.count("ACCESS_REQUEST_DECIDED") == 2
    assert "GRANT_CREATED" in types

    # Notifications were queued on the outbox `notify` stream.
    payloads = await _notify_payloads(gov_app)
    trader_id = me["user"]["user_id"]
    assert any(p["category"] == "ACCESS" for p in payloads)
    assert any(p["category"] == "GRANT" and p["user_id"] == trader_id for p in payloads)


# ---------------------------------------------------------------------------
# 2. Rejection is terminal
# ---------------------------------------------------------------------------


async def test_rejection_is_terminal(gov_app, gov_client):
    trader = await login(gov_client, "trader@demo.nomura")
    approver = await login(gov_client, "approver@demo.nomura")

    created = await _submit_request(gov_client, trader, justification="rejection case")
    step = await _first_pending_step(gov_client, approver)

    # Comment is mandatory -> 400.
    response = await gov_client.post(
        f"{API}/approvals/{step['step_id']}/decision",
        headers=approver,
        json={"decision": "REJECTED", "comment": ""},
    )
    assert response.status_code == 400

    await _decide(gov_client, approver, step["step_id"], "REJECTED", "not justified")

    response = await gov_client.get(f"{API}/access-requests", headers=trader)
    request = [r for r in response.json()["items"] if r["request_id"] == created["request_id"]][0]
    assert request["status"] == "REJECTED"
    assert request["decided_at"] is not None


# ---------------------------------------------------------------------------
# 3. Withdraw
# ---------------------------------------------------------------------------


async def test_withdraw(gov_app, gov_client):
    trader = await login(gov_client, "trader@demo.nomura")

    created = await _submit_request(gov_client, trader, justification="withdraw case")
    response = await gov_client.post(
        f"{API}/access-requests/{created['request_id']}/withdraw", headers=trader
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "WITHDRAWN"

    # Second withdraw -> 409.
    response = await gov_client.post(
        f"{API}/access-requests/{created['request_id']}/withdraw", headers=trader
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# 4. Duplicate open request -> 409
# ---------------------------------------------------------------------------


async def test_duplicate_open_request(gov_app, gov_client):
    trader = await login(gov_client, "trader@demo.nomura")
    await _submit_request(gov_client, trader, justification="first")
    response = await gov_client.post(
        f"{API}/access-requests",
        headers=trader,
        json={
            "target_role": "Auditor",
            "justification": "duplicate",
            "requested_duration_hours": 720,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"


# ---------------------------------------------------------------------------
# 5. SoD BLOCKED -> 422
# ---------------------------------------------------------------------------


async def test_sod_blocked(gov_app, gov_client):
    await ensure_sod_rules(gov_app.state.sessionmaker)  # deterministic (worker also seeds)
    ops = await login(gov_client, "ops@demo.nomura")
    response = await gov_client.post(
        f"{API}/access-requests",
        headers=ops,
        json={
            "target_role": "Security Administrator",
            "justification": "ops requesting secadmin",
            "requested_duration_hours": 8,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"


# ---------------------------------------------------------------------------
# 6. Revocation
# ---------------------------------------------------------------------------


async def test_revocation(gov_app, gov_client):
    trader = await login(gov_client, "trader@demo.nomura")
    approver = await login(gov_client, "approver@demo.nomura")
    secadmin = await login(gov_client, "secadmin@demo.nomura")

    await _submit_request(gov_client, trader, role="Auditor", justification="revocation case")
    await _approve_fully(gov_client, approver, secadmin)
    me = await _me(gov_client, trader)
    assert "AUDIT_VIEW" in me["permissions"]

    response = await gov_client.get(
        f"{API}/grants",
        headers=secadmin,
        params={"user_email": "trader@demo.nomura", "role": "Auditor", "status": "ACTIVE"},
    )
    assert response.status_code == 200, response.text
    grants = response.json()["items"]
    assert len(grants) == 1
    grant_id = grants[0]["grant_id"]

    # Reason is mandatory -> 400.
    response = await gov_client.post(
        f"{API}/grants/{grant_id}/revoke", headers=secadmin, json={"reason": ""}
    )
    assert response.status_code == 400

    response = await gov_client.post(
        f"{API}/grants/{grant_id}/revoke",
        headers=secadmin,
        json={"reason": "access no longer needed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REVOKED"
    assert response.json()["revoked_reason"] == "access no longer needed"

    # Effective immediately: permission gone, audit endpoint denies.
    me = await _me(gov_client, trader)
    assert "AUDIT_VIEW" not in me["permissions"]
    now = utcnow()
    response = await gov_client.get(
        f"{API}/audit-events",
        headers=trader,
        params={
            "from": (now - timedelta(days=1)).isoformat(),
            "to": (now + timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 403

    # Second revoke -> 409.
    response = await gov_client.post(
        f"{API}/grants/{grant_id}/revoke", headers=secadmin, json={"reason": "again"}
    )
    assert response.status_code == 409

    revoked = await _audit_rows(gov_app, "GRANT_REVOKED")
    assert revoked and revoked[0].severity == "HIGH"


# ---------------------------------------------------------------------------
# 7. JIT expiry sweep (direct call of the importable sweep function)
# ---------------------------------------------------------------------------


async def test_jit_expiry_sweep(gov_app, gov_client):
    trader = await login(gov_client, "trader@demo.nomura")

    # Warm the permission cache for trader via a protected route (403 expected,
    # but require_permission computes + caches the effective set first).
    response = await gov_client.get(f"{API}/roles", headers=trader)
    assert response.status_code == 403
    me = await _me(gov_client, trader)
    trader_id = me["user"]["user_id"]
    assert trader_id in security._permission_cache

    # Force the Trader grant into the past directly in the DB.
    grant = await _grant_for(gov_app, "trader@demo.nomura", "Trader")
    assert grant is not None
    async with gov_app.state.sessionmaker() as session:
        row = await session.get(AccessGrant, grant.grant_id)
        row.end_at = utcnow() - timedelta(seconds=1)
        await session.commit()

    expired = await sweep_expired_grants(gov_app.state.sessionmaker)
    assert expired == 1

    async with gov_app.state.sessionmaker() as session:
        row = await session.get(AccessGrant, grant.grant_id)
        assert row.status == GrantStatus.EXPIRED.value
    assert await _audit_rows(gov_app, "GRANT_EXPIRED")

    # Permission cache invalidated and roles gone at request time.
    assert trader_id not in security._permission_cache
    me = await _me(gov_client, trader)
    assert "Trader" not in me["roles"]
    assert "ORDER_SUBMIT" not in me["permissions"]


# ---------------------------------------------------------------------------
# 8. Break-glass
# ---------------------------------------------------------------------------


async def test_break_glass(gov_app, gov_client):
    await ensure_bg_eligibility(gov_app.state.sessionmaker)  # deterministic
    sysadmin = await login(gov_client, "sysadmin@demo.nomura")
    secadmin = await login(gov_client, "secadmin@demo.nomura")
    client_user = await login(gov_client, "client@demo.nomura")

    # client@ lacks BREAKGLASS_ELIGIBLE -> 403.
    response = await gov_client.post(
        f"{API}/break-glass/activate",
        headers=client_user,
        json={"emergency_role": "System Administrator", "reason": "x", "incident_ref": "INC-0"},
    )
    assert response.status_code == 403

    response = await gov_client.post(
        f"{API}/break-glass/activate",
        headers=sysadmin,
        json={
            "emergency_role": "System Administrator",
            "reason": "production database restore",
            "incident_ref": "INC-1234",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["bg_id"] and body["grant_id"] and body["expires_at"]

    # Grant is active immediately and bounded to 4 h.
    me = await _me(gov_client, sysadmin)
    assert "System Administrator" in me["roles"]
    async with gov_app.state.sessionmaker() as session:
        grant = await session.get(AccessGrant, body["grant_id"])
        assert grant.status == GrantStatus.ACTIVE.value
        hours = (as_utc(grant.end_at) - as_utc(grant.start_at)).total_seconds() / 3600
        assert 3.9 < hours <= 4.0

    # secadmin@ sees the pending review and records a verdict.
    response = await gov_client.get(f"{API}/break-glass/reviews", headers=secadmin)
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["review_status"] == "PENDING"
    assert items[0]["user"]["email"] == "sysadmin@demo.nomura"
    assert items[0]["incident_ref"] == "INC-1234"

    response = await gov_client.post(
        f"{API}/break-glass/reviews/{body['bg_id']}/verdict",
        headers=secadmin,
        json={"verdict": "JUSTIFIED", "comment": "legitimate emergency"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == "JUSTIFIED"

    # Second verdict -> 409.
    response = await gov_client.post(
        f"{API}/break-glass/reviews/{body['bg_id']}/verdict",
        headers=secadmin,
        json={"verdict": "ESCALATED", "comment": "second attempt"},
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# 9. PAM checkout / check-in (incl. fail-closed CyberArk outage)
# ---------------------------------------------------------------------------


async def test_pam_checkout(gov_app, gov_client, monkeypatch):
    sysadmin = await login(gov_client, "sysadmin@demo.nomura")
    trader = await login(gov_client, "trader@demo.nomura")

    # trader@ lacks PAM_CHECKOUT -> 403.
    response = await gov_client.post(
        f"{API}/pam/checkouts",
        headers=trader,
        json={"safe_name": "INFRA", "account_id": "root-db"},
    )
    assert response.status_code == 403

    response = await gov_client.post(
        f"{API}/pam/checkouts",
        headers=sysadmin,
        json={"safe_name": "INFRA", "account_id": "root-db"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["credential"]
    assert body["checked_out_at"]
    checkout_id = body["checkout_id"]

    # Metadata row exists; the credential is never persisted (no such column).
    async with gov_app.state.sessionmaker() as session:
        row = await session.get(CredentialCheckout, checkout_id)
        assert row is not None
        assert row.checked_in_at is None

    response = await gov_client.post(
        f"{API}/pam/checkouts/{checkout_id}/checkin", headers=sysadmin
    )
    assert response.status_code == 200, response.text
    assert response.json()["checked_in_at"]

    # Second check-in -> 409.
    response = await gov_client.post(
        f"{API}/pam/checkouts/{checkout_id}/checkin", headers=sysadmin
    )
    assert response.status_code == 409

    # CyberArk down -> fail closed with 503 (env read per call).
    monkeypatch.setenv("CYBERARK_AVAILABLE", "false")
    response = await gov_client.post(
        f"{API}/pam/checkouts",
        headers=sysadmin,
        json={"safe_name": "INFRA", "account_id": "svc-deploy"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 10. Audit search & export
# ---------------------------------------------------------------------------


async def test_audit_search_and_export(gov_app, gov_client):
    auditor = await login(gov_client, "auditor@demo.nomura")
    trader = await login(gov_client, "trader@demo.nomura")

    now = utcnow()
    wide = {
        "from": (now - timedelta(days=1)).isoformat(),
        "to": (now + timedelta(days=1)).isoformat(),
    }
    narrow = {
        "from": (now - timedelta(minutes=5)).isoformat(),
        "to": (now + timedelta(minutes=1)).isoformat(),
    }

    for params in (wide, narrow):
        response = await gov_client.get(f"{API}/audit-events", headers=auditor, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"items", "next_cursor"}
        assert body["items"], "expected audit events in range"
        item = body["items"][0]
        assert set(item) == {
            "event_id", "ts", "actor_email", "event_type", "resource_type",
            "resource_id", "severity", "source_ip", "correlation_id", "payload",
        }

    # Missing date range -> 400 with guidance.
    response = await gov_client.get(f"{API}/audit-events", headers=auditor)
    assert response.status_code == 400
    assert "from" in response.json()["error"]["message"]

    # CSV export -> attachment.
    response = await gov_client.get(
        f"{API}/audit-events/export", headers=auditor, params={**wide, "format": "csv"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "AUTH_LOGIN_SUCCESS" in response.text

    # trader@ lacks AUDIT_VIEW -> 403.
    response = await gov_client.get(f"{API}/audit-events", headers=trader, params=wide)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 11. Admin: governance summary + integration health
# ---------------------------------------------------------------------------


async def test_admin_endpoints(gov_app, gov_client):
    secadmin = await login(gov_client, "secadmin@demo.nomura")
    ops = await login(gov_client, "ops@demo.nomura")
    client_user = await login(gov_client, "client@demo.nomura")

    response = await gov_client.get(f"{API}/admin/governance-summary", headers=secadmin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "active_grants", "pending_approvals", "oldest_age_hours",
        "grants_expiring_24h",
        "break_glass_pending_review", "authorization_denials_24h", "recent_break_glass",
    }
    assert body["active_grants"] >= 8  # seeded demo grants
    assert body["pending_approvals"] == 0

    response = await gov_client.get(f"{API}/admin/health", headers=ops)
    assert response.status_code == 200, response.text
    body = response.json()
    names = {i["name"] for i in body["integrations"]}
    assert {"directory", "cyberark", "smtp", "market_feed"} <= names
    assert "outbox_unpublished" in body
    assert "stp_exceptions" in body

    # client@ has neither GOVERNANCE_VIEW nor INTEGRATION_MONITOR -> 403.
    response = await gov_client.get(f"{API}/admin/governance-summary", headers=client_user)
    assert response.status_code == 403
    response = await gov_client.get(f"{API}/admin/health", headers=client_user)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 12. Role management
# ---------------------------------------------------------------------------


async def test_role_management(gov_app, gov_client):
    secadmin = await login(gov_client, "secadmin@demo.nomura")
    trader = await login(gov_client, "trader@demo.nomura")

    # Catalog + role list (trader lacks ROLE_VIEW -> 403).
    response = await gov_client.get(f"{API}/permissions", headers=secadmin)
    assert response.status_code == 200
    assert "AUDIT_VIEW" in {p["action"] for p in response.json()}
    response = await gov_client.get(f"{API}/roles", headers=trader)
    assert response.status_code == 403

    response = await gov_client.post(
        f"{API}/roles",
        headers=secadmin,
        json={
            "name": "Read Only Auditor",
            "description": "audit search only",
            "permission_actions": ["AUDIT_VIEW"],
        },
    )
    assert response.status_code == 201, response.text
    role = response.json()
    assert role["version"] == 1
    assert role["permission_actions"] == ["AUDIT_VIEW"]

    # Duplicate name -> 409; unknown action -> 400.
    response = await gov_client.post(
        f"{API}/roles", headers=secadmin, json={"name": "Read Only Auditor", "permission_actions": []}
    )
    assert response.status_code == 409
    response = await gov_client.post(
        f"{API}/roles", headers=secadmin, json={"name": "Bogus", "permission_actions": ["NOPE"]}
    )
    assert response.status_code == 400

    # PATCH bumps the version and replaces the permission set.
    response = await gov_client.patch(
        f"{API}/roles/{role['role_id']}",
        headers=secadmin,
        json={"permission_actions": ["AUDIT_VIEW", "AUDIT_EXPORT"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert response.json()["permission_actions"] == ["AUDIT_EXPORT", "AUDIT_VIEW"]


# ---------------------------------------------------------------------------
# 13. Grant extension via the normal approval chain
# ---------------------------------------------------------------------------


async def test_grant_extension(gov_app, gov_client):
    trader = await login(gov_client, "trader@demo.nomura")
    approver = await login(gov_client, "approver@demo.nomura")
    secadmin = await login(gov_client, "secadmin@demo.nomura")

    # Give trader an Auditor grant (720 h), then extend it by 24 h.
    await _submit_request(gov_client, trader, role="Auditor", justification="extension base")
    await _approve_fully(gov_client, approver, secadmin)
    grant = await _grant_for(gov_app, "trader@demo.nomura", "Auditor")
    assert grant is not None
    original_end = as_utc(grant.end_at)

    response = await gov_client.post(
        f"{API}/grants/{grant.grant_id}/extend",
        headers=trader,
        json={"additional_hours": 24, "justification": "audit overran"},
    )
    assert response.status_code == 201, response.text

    # Not the grantee -> 403 (fresh grant below belongs to trader).
    response = await gov_client.post(
        f"{API}/grants/{grant.grant_id}/extend",
        headers=secadmin,
        json={"additional_hours": 24, "justification": "not mine"},
    )
    assert response.status_code == 403

    await _approve_fully(gov_client, approver, secadmin)

    async with gov_app.state.sessionmaker() as session:
        row = await session.get(AccessGrant, grant.grant_id)
        extended_end = as_utc(row.end_at)
    assert extended_end == original_end + timedelta(hours=24)
    assert await _audit_rows(gov_app, "GRANT_EXTENDED")
