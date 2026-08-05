"""Operations order-visibility + requeue tests.

The Operations Analyst role holds TRADE_VIEW / PORTFOLIO_VIEW_ALL /
STP_EXCEPTION_HANDLE / INTEGRATION_MONITOR — no ORDER_VIEW. These tests cover
the two capabilities built for ops triage:

- order read endpoints accept STP_EXCEPTION_HANDLE as an alternative to
  ORDER_VIEW (OR gate), keeping the owner-scoping semantics;
- POST /orders/{id}/requeue re-validates a REJECTED order (with optional
  amendments) and re-enters it into the execution pipeline.

Like test_trading.py these run the real background workers with fast timing,
so a successfully requeued MARKET order actually fills.
"""

import asyncio
import time
import uuid

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from app.config import Settings
from app.core.models import AuditEvent, Portfolio, User
from app.main import create_app
from conftest import login

TRADER = "trader@demo.nomura"
CLIENT = "client@demo.nomura"
OPS = "ops@demo.nomura"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/ops_requeue_test.db",
        EVENT_BUS="memory",
        SESSION_STORE="memory",
        RUN_WORKERS=True,
        TICK_INTERVAL_MS=50,
        SETTLEMENT_DELAY_SECONDS=0.2,
        DEV_AUTH=True,
        DATA_DIR=str(tmp_path / "no-such-data-dir"),  # fallback feed
    )


@pytest.fixture
async def app(settings):
    return create_app(settings)


@pytest.fixture
async def client(app):
    # Same shutdown posture as test_trading.py (cross-team workers).
    manager = LifespanManager(app, shutdown_timeout=6)
    await manager.__aenter__()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://t"
        ) as http_client:
            yield http_client
    finally:
        try:
            await manager.__aexit__(None, None, None)
        except TimeoutError:
            import warnings

            warnings.warn("app shutdown exceeded 6s (see report: cross-team worker)")


@pytest.fixture
async def trader(client):
    return await login(client, TRADER)


@pytest.fixture
async def ops(client):
    return await login(client, OPS)


@pytest.fixture
async def desk_id(app, client):
    async with app.state.sessionmaker() as session:
        portfolios = (await session.execute(select(Portfolio))).scalars().all()
    return next(p.portfolio_id for p in portfolios if p.name == "Desk Book 1")


# ---------------------------------------------------------------------------
# Helpers (mirrors of the test_trading.py idioms)
# ---------------------------------------------------------------------------


async def wait_until(fn, timeout=5.0, interval=0.1):
    """Poll an async fn until it returns a truthy value; else fail."""
    deadline = time.monotonic() + timeout
    while True:
        value = await fn()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(interval)


async def wait_for_prices(client, headers, timeout=3.0):
    async def check():
        response = await client.get("/api/v1/instruments", headers=headers)
        assert response.status_code == 200
        items = response.json()["items"]
        if items and all(i["latest_price"] is not None for i in items):
            return items
        return None

    return await wait_until(check, timeout=timeout)


async def submit_order(client, headers, portfolio_id, quantity, symbol="TSLA"):
    """MARKET BUY submission; returns the raw response (201 or 422)."""
    return await client.post(
        "/api/v1/orders",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": portfolio_id,
            "instrument": symbol,
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": quantity,
        },
    )


async def rejected_order_id(client, trader, desk_id):
    """An order validation rejects deterministically: 10.5 is not a multiple
    of the lot size 1 (INVALID_QUANTITY) — independent of the price level."""
    response = await submit_order(client, trader, desk_id, 10.5)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["code"] == "INVALID_QUANTITY"
    # The rejection persists a REJECTED order row; find it via the blotter.
    response = await client.get(
        "/api/v1/orders", params={"status": "REJECTED"}, headers=trader
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["reject_reason"] == "INVALID_QUANTITY"
    return items[0]["order_id"]


# ---------------------------------------------------------------------------
# 1. Read access: ops sees orders (incl. REJECTED); the OR gate keeps the
#    403 for users holding neither ORDER_VIEW nor STP_EXCEPTION_HANDLE.
# ---------------------------------------------------------------------------


async def test_ops_reads_orders_including_rejected(client, app, trader, ops, desk_id):
    await wait_for_prices(client, trader)
    order_id = await rejected_order_id(client, trader, desk_id)

    # Ops (STP_EXCEPTION_HANDLE, no ORDER_VIEW) lists orders…
    response = await client.get("/api/v1/orders", headers=ops)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert any(
        i["order_id"] == order_id and i["status"] == "REJECTED" for i in items
    )

    # …with the status filter…
    response = await client.get(
        "/api/v1/orders", params={"status": "REJECTED"}, headers=ops
    )
    assert response.status_code == 200
    assert [i["order_id"] for i in response.json()["items"]] == [order_id]

    # …and reads the single order (PORTFOLIO_VIEW_ALL -> cross-portfolio).
    response = await client.get(f"/api/v1/orders/{order_id}", headers=ops)
    assert response.status_code == 200
    assert response.json()["reject_reason"] == "INVALID_QUANTITY"

    # The client (neither ORDER_VIEW nor STP_EXCEPTION_HANDLE) stays denied.
    client_headers = await login(client, CLIENT)
    response = await client.get("/api/v1/orders", headers=client_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    response = await client.get(f"/api/v1/orders/{order_id}", headers=client_headers)
    assert response.status_code == 403

    # The denials are audited (OR-gate keeps the AUTHORIZATION_DENIED trail).
    async with app.state.sessionmaker() as session:
        denials = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "AUTHORIZATION_DENIED"
                    )
                )
            )
            .scalars()
            .all()
        )
    denied_paths = {d.payload.get("path") for d in denials}
    assert "/api/v1/orders" in denied_paths


# ---------------------------------------------------------------------------
# 2. Requeue happy path: fix the quantity -> ACCEPTED -> engine fills.
# ---------------------------------------------------------------------------


async def test_requeue_fixed_order_fills(client, app, trader, ops, desk_id):
    await wait_for_prices(client, trader)
    order_id = await rejected_order_id(client, trader, desk_id)

    response = await client.post(
        f"/api/v1/orders/{order_id}/requeue",
        headers=ops,
        json={"quantity": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ACCEPTED"
    assert body["quantity"] == 10
    assert body["reject_reason"] is None

    # The orders.accepted outbox event re-enters the execution pipeline: the
    # MARKET order fills on the live tick snapshot.
    async def filled():
        response = await client.get(f"/api/v1/orders/{order_id}", headers=ops)
        assert response.status_code == 200
        body = response.json()
        return body if body["status"] == "FILLED" else None

    order = await wait_until(filled)
    assert order["executions"][0]["quantity"] == 10


# ---------------------------------------------------------------------------
# 3. Requeue still-invalid: stays REJECTED, reject_reason updated, 422.
# ---------------------------------------------------------------------------


async def test_requeue_still_invalid_keeps_rejected(client, trader, ops, desk_id):
    await wait_for_prices(client, trader)
    # Cost >> cash -> INSUFFICIENT_BUYING_POWER at submission.
    response = await submit_order(client, trader, desk_id, 10_000_000_000)
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "INSUFFICIENT_BUYING_POWER"
    response = await client.get(
        "/api/v1/orders", params={"status": "REJECTED"}, headers=trader
    )
    order_id = response.json()["items"][0]["order_id"]

    # Requeue with a non-lot-multiple quantity: affordable but still invalid —
    # the persisted reason changes to the new rejection.
    response = await client.post(
        f"/api/v1/orders/{order_id}/requeue",
        headers=ops,
        json={"quantity": 10.5},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "BUSINESS_RULE_VIOLATION"
    assert error["details"][0]["code"] == "INVALID_QUANTITY"

    order = (
        await client.get(f"/api/v1/orders/{order_id}", headers=ops)
    ).json()
    assert order["status"] == "REJECTED"
    assert order["reject_reason"] == "INVALID_QUANTITY"
    assert order["quantity"] == 10_000_000_000  # amendment not applied


# ---------------------------------------------------------------------------
# 4. Requeue gate: trader (no STP_EXCEPTION_HANDLE) -> 403; non-REJECTED
#    -> 409; unknown -> 404.
# ---------------------------------------------------------------------------


async def test_requeue_permission_and_state_guards(client, trader, ops, desk_id):
    await wait_for_prices(client, trader)
    order_id = await rejected_order_id(client, trader, desk_id)

    # Trader holds ORDER_VIEW/ORDER_CANCEL but not STP_EXCEPTION_HANDLE.
    response = await client.post(
        f"/api/v1/orders/{order_id}/requeue",
        headers=trader,
        json={"quantity": 10},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # Unknown order id -> 404 for ops.
    response = await client.post(
        "/api/v1/orders/no-such-order/requeue", headers=ops, json={}
    )
    assert response.status_code == 404

    # A live (non-REJECTED) order cannot be requeued.
    response = await submit_order(client, trader, desk_id, 100)
    assert response.status_code == 201, response.text
    live_id = response.json()["order_id"]

    async def closed():
        body = (
            await client.get(f"/api/v1/orders/{live_id}", headers=ops)
        ).json()
        return body if body["status"] == "FILLED" else None

    await wait_until(closed)
    response = await client.post(
        f"/api/v1/orders/{live_id}/requeue", headers=ops, json={}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"


# ---------------------------------------------------------------------------
# 5. Requeue writes the ORDER_REQUEUED audit row (actor = ops user) and
#    notifies the order owner.
# ---------------------------------------------------------------------------


async def test_requeue_audit_and_owner_notification(client, app, trader, ops, desk_id):
    await wait_for_prices(client, trader)
    order_id = await rejected_order_id(client, trader, desk_id)

    response = await client.post(
        f"/api/v1/orders/{order_id}/requeue",
        headers=ops,
        json={"quantity": 10},
    )
    assert response.status_code == 200, response.text

    async with app.state.sessionmaker() as session:
        audits = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.resource_id == order_id,
                        AuditEvent.event_type == "ORDER_REQUEUED",
                    )
                )
            )
            .scalars()
            .all()
        )
        ops_user = (
            await session.execute(select(User).where(User.email == OPS))
        ).scalar_one()
    assert len(audits) == 1
    audit = audits[0]
    assert audit.actor_id == ops_user.user_id
    assert audit.payload["symbol"] == "TSLA"
    assert audit.payload["amended"] == {"quantity": "10"}
    assert audit.payload["prior_reject_reason"] == "INVALID_QUANTITY"

    # The owner (trader) gets the "requeued by operations" notification.
    async def notified():
        response = await client.get("/api/v1/notifications", headers=trader)
        assert response.status_code == 200
        for item in response.json()["items"]:
            if item["payload"]["title"] == "Order requeued by operations":
                return item
        return None

    notification = await wait_until(notified)
    assert order_id in notification["payload"]["body"]
