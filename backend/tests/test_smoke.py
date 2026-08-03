"""Foundation smoke tests: health, auth, authZ, audit chain, event bus, outbox."""

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.audit import AUTH_LOGIN_SUCCESS
from app.core.events import InProcessBus, outbox_relay, write_outbox
from app.core.models import AuditEvent, OutboxEvent
from app.core.security import SessionData, require_permission
from conftest import login


async def test_health(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get("x-trace-id")


async def test_dev_login_and_me(client):
    headers = await login(client, "trader@demo.nomura")
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["upn"] == "trader@demo.nomura"
    assert "Trader" in body["roles"]
    assert "ORDER_SUBMIT" in body["permissions"]


async def test_unknown_email_401_envelope(client):
    response = await client.post(
        "/api/v1/auth/dev-login", json={"email": "nobody@demo.nomura"}
    )
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "UNAUTHENTICATED"
    assert error["traceId"]
    assert isinstance(error["details"], list)
    assert response.headers["x-trace-id"] == error["traceId"]


def _mount_permission_probe(app) -> None:
    """Test-only route protected by require_permission (no business modules exist)."""
    router = APIRouter()

    @router.get("/perm-probe")
    async def perm_probe(user: SessionData = Depends(require_permission("ORDER_SUBMIT"))):
        return {"ok": True, "upn": user.upn}

    app.include_router(router, prefix="/api/v1")


async def test_require_permission_deny_and_allow(app, client):
    _mount_permission_probe(app)
    trader_headers = await login(client, "trader@demo.nomura")
    client_headers = await login(client, "client@demo.nomura")

    # Client lacks ORDER_SUBMIT -> 403 FORBIDDEN envelope.
    response = await client.get("/api/v1/perm-probe", headers=client_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # Trader holds ORDER_SUBMIT -> 200.
    response = await client.get("/api/v1/perm-probe", headers=trader_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True

    # No token -> 401.
    response = await client.get("/api/v1/perm-probe")
    assert response.status_code == 401


async def test_audit_hash_chain(app, client):
    await login(client, "trader@demo.nomura")
    await login(client, "client@demo.nomura")

    async with app.state.sessionmaker() as session:
        events = (
            (await session.execute(select(AuditEvent).order_by(AuditEvent.seq)))
            .scalars()
            .all()
        )
    assert len(events) >= 2
    assert events[0].event_type == AUTH_LOGIN_SUCCESS
    assert events[0].prev_hash is None
    assert events[1].prev_hash == events[0].payload_hash
    for previous, current in zip(events, events[1:]):
        assert current.prev_hash == previous.payload_hash


async def test_inprocess_bus_fanout():
    bus = InProcessBus()
    sub_a = await bus.subscribe("orders.accepted")
    sub_b = await bus.subscribe("orders.accepted")
    await bus.publish("orders.accepted", {"order_id": "o1"})
    assert await asyncio.wait_for(anext(sub_a), timeout=1) == {"order_id": "o1"}
    assert await asyncio.wait_for(anext(sub_b), timeout=1) == {"order_id": "o1"}


async def test_outbox_write_and_relay(app, client):
    bus = InProcessBus()
    subscription = await bus.subscribe("orders.accepted")
    sessionmaker = app.state.sessionmaker

    async with sessionmaker() as session:
        await write_outbox(session, "orders.accepted", {"order_id": "o2"})
        await session.commit()

    stop = asyncio.Event()
    relay = asyncio.create_task(
        outbox_relay(sessionmaker, bus, stop, poll_interval=0.05)
    )
    try:
        event = await asyncio.wait_for(anext(subscription), timeout=2)
        assert event == {"order_id": "o2"}
        async with sessionmaker() as session:
            rows = (await session.execute(select(OutboxEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].published_at is not None  # published exactly once
    finally:
        stop.set()
        await asyncio.wait_for(relay, timeout=2)


async def test_replay_skip_endpoint(client, app):
    """POST /marketdata/replay/skip: 401 unauthenticated; 409 on the fallback
    feed (tests never run dataset replay); 200 + audit when a dataset replay
    is active (flag simulated)."""
    from sqlalchemy import select

    from app.core.models import AuditEvent
    from app.modules.marketdata import worker as replay_worker
    from tests.conftest import login

    assert (await client.post("/api/v1/marketdata/replay/skip", json={"days": 1})).status_code == 401

    headers = await login(client, "trader@demo.nomura")
    conflict = await client.post(
        "/api/v1/marketdata/replay/skip", json={"days": 1}, headers=headers
    )
    assert conflict.status_code == 409

    replay_worker._dataset_replay_active = True
    replay_worker._skip_days = 0
    try:
        ok = await client.post(
            "/api/v1/marketdata/replay/skip", json={"days": 2}, headers=headers
        )
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"skipped_days": 2}
        assert replay_worker._skip_days == 2
        async with app.state.sessionmaker() as session:
            row = (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.event_type == "REPLAY_SKIP")
                )
            ).scalars().first()
        assert row is not None and row.payload["days"] == 2
    finally:
        replay_worker._dataset_replay_active = False
        replay_worker._skip_days = 0


async def test_demo_traders_seeded_and_idempotent(client, app):
    """Audience accounts (owner ask): trader_1..100@demo.nomura exist with the
    demo password, a Trader grant and an empty funded book; the startup patch
    is idempotent (second run creates nothing)."""
    from app.seed import DEMO_TRADER_COUNT, ensure_demo_traders

    # Password login as trader_7 works (demo password, no dev-login).
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "trader_7@demo.nomura", "password": "demo1234"},
    )
    assert response.status_code == 200, response.text

    headers = await login(client, "trader_42@demo.nomura")
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert "ORDER_SUBMIT" in me.json()["permissions"]

    portfolios = await client.get("/api/v1/portfolios", headers=headers)
    books = portfolios.json()["items"]
    assert len(books) == 1
    assert books[0]["name"] == "Trader 42 Book"
    assert books[0]["cash_balance"] == 100000.0
    positions = await client.get(
        f"/api/v1/portfolios/{books[0]['portfolio_id']}/positions", headers=headers
    )
    assert positions.json()["items"] == []

    # Idempotent: every trader_1..100 exists and a re-run creates none.
    async with app.state.sessionmaker() as session:
        from sqlalchemy import func, select

        from app.core.models import User

        count = await session.scalar(
            select(func.count(User.user_id)).where(
                User.email.startswith("trader_"), User.email.endswith("@demo.nomura")
            )
        )
    assert count >= DEMO_TRADER_COUNT
    assert await ensure_demo_traders(app.state.sessionmaker) == 0


async def test_demo_credential_round_robin(client):
    """Each call hands out the next trader_N credential (different visitors
    get different accounts); DEV_AUTH-gated like dev-login."""
    first = await client.get("/api/v1/auth/demo-credential")
    second = await client.get("/api/v1/auth/demo-credential")
    assert first.status_code == 200 and second.status_code == 200
    a, b = first.json(), second.json()
    assert a["password"] == b["password"] == "demo1234"
    assert a["email"] != b["email"]
    for cred in (a, b):
        name, domain = cred["email"].split("@")
        assert domain == "demo.nomura"
        assert name.startswith("trader_") and 1 <= int(name.split("_")[1]) <= 100
