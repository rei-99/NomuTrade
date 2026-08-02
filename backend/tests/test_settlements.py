"""Settlement visibility + STP exception remediation tests (FR-ORD-005 E1).

Same integration style as test_trading.py: the real background workers (tick
feed, execution engine, STP worker, settlement sweeper) run with fast timing
and tests poll instead of asserting on fixed sleeps. Covers:
- GET /settlements: shape, bond-aware value, lifecycle to SETTLED, filters;
- GET /trades carrying a matching settlement_state;
- portfolio scoping (own-only vs Operations Analyst view-all);
- POST /settlements/exceptions/{id}/retry: 403/404/409 + the happy path that
  re-creates the instruction and writes the STP_EXCEPTION_RETRY audit.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from app.config import Settings
from app.core.models import (
    AccessGrant,
    AuditEvent,
    Instrument,
    Portfolio,
    Role,
    SettlementInstruction,
    User,
)
from app.core.timeutil import utcnow
from app.main import create_app
from app.modules.orders import workers
from conftest import login
from test_trading import submit_market_buy, wait_for_prices, wait_order_status, wait_until

TRADER = "trader@demo.nomura"
CLIENT = "client@demo.nomura"
OPS = "ops@demo.nomura"
TRADER2 = "trader2@demo.nomura"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/settlements_test.db",
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
    # Same shutdown-tolerance pattern as test_trading.py.
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
async def ids(app, client):
    """Seed identifiers, read straight from the test database."""
    async with app.state.sessionmaker() as session:
        portfolios = (await session.execute(select(Portfolio))).scalars().all()
    return {
        "desk": next(p.portfolio_id for p in portfolios if p.name == "Desk Book 1"),
        "client_pf": next(
            p.portfolio_id for p in portfolios if p.name == "Client Portfolio A"
        ),
    }


async def get_settlement_item(client, headers, execution_id, **params):
    """Poll GET /settlements until the instruction for execution_id appears."""
    async def check():
        response = await client.get(
            "/api/v1/settlements", params=params, headers=headers
        )
        assert response.status_code == 200, response.text
        for item in response.json()["items"]:
            if item["execution_id"] == execution_id:
                return item
        return None

    return await wait_until(check)


# ---------------------------------------------------------------------------
# 1. Blotter: fill -> instruction with correct value -> SETTLED; /trades join
# ---------------------------------------------------------------------------


async def test_settlements_list_lifecycle_and_trades_join(client, trader, ids):
    await wait_for_prices(client, trader)
    order_id = await submit_market_buy(client, trader, ids["desk"])
    order = await wait_order_status(client, trader, order_id, "FILLED")
    execution = order["executions"][0]
    execution_id = execution["execution_id"]

    item = await get_settlement_item(client, trader, execution_id)
    assert item["settlement_id"]
    assert item["portfolio_id"] == ids["desk"]
    assert item["portfolio_name"] == "Desk Book 1"
    assert item["instrument_symbol"] == "TSLA"
    assert item["side"] == "BUY"
    assert item["quantity"] == execution["quantity"]
    assert item["price"] == pytest.approx(execution["price"])
    # Equity value: quantity x price.
    assert item["value"] == pytest.approx(
        execution["quantity"] * execution["price"]
    )
    assert item["lifecycle_state"] in {"EXECUTED", "AFFIRMED", "SETTLED"}
    assert item["created_at"]

    # The trade blotter carries the settlement lifecycle state too. The two
    # GETs race the sweeper (state advances ~every 1 s), so assert membership
    # here; terminal-state agreement is polled below.
    response = await client.get("/api/v1/trades", headers=trader)
    trade = next(
        t for t in response.json()["items"] if t["execution_id"] == execution_id
    )
    assert trade["settlement_state"] in {"EXECUTED", "AFFIRMED", "SETTLED"}

    # Reaches SETTLED (sweeper delay is 0.2 s) with settled_at populated;
    # exercises the lifecycle_state + portfolio_id filters.
    async def fully_settled():
        response = await client.get(
            "/api/v1/settlements",
            params={"lifecycle_state": "SETTLED", "portfolio_id": ids["desk"]},
            headers=trader,
        )
        assert response.status_code == 200, response.text
        for it in response.json()["items"]:
            if it["execution_id"] == execution_id and it["settled_at"] is not None:
                return it
        return None

    settled_item = await wait_until(fully_settled)
    assert settled_item["lifecycle_state"] == "SETTLED"

    async def trade_settled():
        response = await client.get("/api/v1/trades", headers=trader)
        trade = next(
            t
            for t in response.json()["items"]
            if t["execution_id"] == execution_id
        )
        return trade if trade["settlement_state"] == "SETTLED" else None

    await wait_until(trade_settled)

    # Invalid lifecycle_state -> 400 envelope; unknown portfolio -> 404.
    response = await client.get(
        "/api/v1/settlements", params={"lifecycle_state": "NOPE"}, headers=trader
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    response = await client.get(
        "/api/v1/settlements", params={"portfolio_id": "no-such-id"}, headers=trader
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 2. Bond-aware value: face x price / 100 (design 21 A2)
# ---------------------------------------------------------------------------


async def test_settlements_bond_value(client, trader, ids):
    await wait_for_prices(client, trader)
    order_id = await submit_market_buy(
        client, trader, ids["desk"], symbol="UST10Y", qty=2000
    )
    order = await wait_order_status(client, trader, order_id, "FILLED")
    execution = order["executions"][0]

    item = await get_settlement_item(client, trader, execution["execution_id"])
    assert item["instrument_symbol"] == "UST10Y"
    assert item["quantity"] == 2000
    expected = 2000 * execution["price"] / 100
    assert item["value"] == pytest.approx(expected)
    assert item["value"] < 2500  # face x price (~200k) would be wrong


# ---------------------------------------------------------------------------
# 3. Scoping: own-only without view-all perms; Operations Analyst sees all
# ---------------------------------------------------------------------------


async def test_settlements_scoping(client, app, trader, ids):
    await wait_for_prices(client, trader)
    # A second trader (Trader role: TRADE_VIEW but no view-all permission)
    # with their own funded portfolio.
    async with app.state.sessionmaker() as session:
        role = (
            await session.execute(select(Role).where(Role.name == "Trader"))
        ).scalar_one()
        user2 = User(
            upn=TRADER2,
            display_name="Second Trader",
            email=TRADER2,
            status="ACTIVE",
            synced_at=utcnow(),
        )
        session.add(user2)
        await session.flush()
        now = utcnow()
        session.add(
            AccessGrant(
                user_id=user2.user_id,
                role_id=role.role_id,
                request_id=None,
                start_at=now - timedelta(days=1),
                end_at=now + timedelta(days=10),
                status="ACTIVE",
            )
        )
        portfolio2 = Portfolio(
            name="Second Desk",
            type="HOUSE",
            owner_id=user2.user_id,
            cash_balance=Decimal("1000000"),
        )
        session.add(portfolio2)
        await session.commit()
        portfolio2_id = portfolio2.portfolio_id
    trader2 = await login(client, TRADER2)

    order1 = await submit_market_buy(client, trader, ids["desk"])
    await wait_order_status(client, trader, order1, "FILLED")
    order2 = await submit_market_buy(client, trader2, portfolio2_id)
    order2_body = await wait_order_status(client, trader2, order2, "FILLED")
    execution2_id = order2_body["executions"][0]["execution_id"]
    order1_body = (
        await client.get(f"/api/v1/orders/{order1}", headers=trader)
    ).json()
    execution1_id = order1_body["executions"][0]["execution_id"]

    # Each trader sees their own instruction once booked...
    await get_settlement_item(client, trader, execution1_id)
    await get_settlement_item(client, trader2, execution2_id)
    # ...and never the other trader's.
    items1 = (
        await client.get("/api/v1/settlements", headers=trader)
    ).json()["items"]
    assert {i["portfolio_id"] for i in items1} == {ids["desk"]}
    items2 = (
        await client.get("/api/v1/settlements", headers=trader2)
    ).json()["items"]
    assert {i["portfolio_id"] for i in items2} == {portfolio2_id}

    # Filtering by a portfolio the caller does not own -> 403.
    response = await client.get(
        "/api/v1/settlements",
        params={"portfolio_id": portfolio2_id},
        headers=trader,
    )
    assert response.status_code == 403

    # Operations Analyst (STP_EXCEPTION_HANDLE + PORTFOLIO_VIEW_ALL) sees all.
    ops = await login(client, OPS)
    item = await get_settlement_item(client, ops, execution1_id)
    assert item["portfolio_id"] == ids["desk"]
    ops_items = (
        await client.get("/api/v1/settlements", headers=ops)
    ).json()["items"]
    assert {ids["desk"], portfolio2_id} <= {i["portfolio_id"] for i in ops_items}

    # The Client role lacks TRADE_VIEW entirely -> 403 at the gate.
    client_user = await login(client, CLIENT)
    response = await client.get("/api/v1/settlements", headers=client_user)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 4. Retry: forced STP exception -> ops retry re-creates the instruction
# ---------------------------------------------------------------------------


async def test_stp_exception_retry(client, app, trader, ids, monkeypatch):
    await wait_for_prices(client, trader)
    ops = await login(client, OPS)

    # Force the STP worker's processing to fail: the event is dropped with an
    # STP_EXCEPTION audit (FR-ORD-005 E1), leaving no instruction behind.
    original = workers._process_execution

    async def boom(sessionmaker, event):
        raise RuntimeError("forced STP failure")

    monkeypatch.setattr(workers, "_process_execution", boom)
    order_id = await submit_market_buy(client, trader, ids["desk"])
    order = await wait_order_status(client, trader, order_id, "FILLED")
    execution_id = order["executions"][0]["execution_id"]

    async def exception_recorded():
        async with app.state.sessionmaker() as session:
            return (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "STP_EXCEPTION",
                        AuditEvent.resource_id == execution_id,
                    )
                )
            ).scalar_one_or_none()

    exception_audit = await wait_until(exception_recorded)
    assert exception_audit.severity == "HIGH"
    monkeypatch.setattr(workers, "_process_execution", original)

    async def instruction_row():
        async with app.state.sessionmaker() as session:
            return (
                await session.execute(
                    select(SettlementInstruction).where(
                        SettlementInstruction.execution_id == execution_id
                    )
                )
            ).scalar_one_or_none()

    assert await instruction_row() is None  # dropped, not processed

    # 403 without STP_EXCEPTION_HANDLE (the Trader role lacks it).
    response = await client.post(
        f"/api/v1/settlements/exceptions/{execution_id}/retry", headers=trader
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # 404 for a bogus execution id.
    response = await client.post(
        f"/api/v1/settlements/exceptions/{uuid.uuid4().hex}/retry", headers=ops
    )
    assert response.status_code == 404

    # Ops retry re-publishes the event -> the worker books the instruction.
    response = await client.post(
        f"/api/v1/settlements/exceptions/{execution_id}/retry", headers=ops
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"execution_id": execution_id, "republished": True}

    instruction = await wait_until(instruction_row)
    assert instruction.lifecycle_state in {"EXECUTED", "AFFIRMED", "SETTLED"}

    # STP_EXCEPTION_RETRY audit, actor = the ops caller.
    async with app.state.sessionmaker() as session:
        retries = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "STP_EXCEPTION_RETRY",
                        AuditEvent.resource_id == execution_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        ops_user = (
            await session.execute(select(User).where(User.email == OPS))
        ).scalar_one()
    assert len(retries) == 1
    assert retries[0].actor_id == ops_user.user_id
    assert retries[0].resource_type == "EXECUTION"
    assert retries[0].severity == "WARN"

    # The retried fill flows into the blotter too.
    item = await get_settlement_item(client, trader, execution_id)
    assert item["instrument_symbol"] == "TSLA"

    # Second retry -> 409 (instruction already exists, nothing to remediate).
    response = await client.post(
        f"/api/v1/settlements/exceptions/{execution_id}/retry", headers=ops
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"
