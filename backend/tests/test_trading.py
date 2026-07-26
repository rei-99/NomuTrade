"""Trading-core integration tests: market data, order pipeline, STP, portfolios.

Unlike the smoke tests these run the real background workers (tick replayer,
execution engine, STP worker, settlement sweeper, valuation projector) via
the app lifespan, with fast timing settings. Ticks flow automatically once
the replayer is up; tests poll instead of asserting on fixed sleeps.
"""

import asyncio
import time
import uuid
from decimal import Decimal

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from app.config import Settings
from app.core.models import (
    AuditEvent,
    Execution,
    Instrument,
    Order,
    Portfolio,
    Position,
    SettlementInstruction,
)
from app.main import create_app
from conftest import login

TRADER = "trader@demo.nomura"
CLIENT = "client@demo.nomura"
DESK_CASH_INITIAL = Decimal("50000000")

SYMBOLS = {
    "7203.T", "6758.T", "9984.T", "8306.T", "9433.T",
    "6861.T", "6501.T", "7974.T", "4063.T", "8001.T",
}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/trading_test.db",
        EVENT_BUS="memory",
        SESSION_STORE="memory",
        RUN_WORKERS=True,
        TICK_INTERVAL_MS=50,
        SETTLEMENT_DELAY_SECONDS=0.2,
        DEV_AUTH=True,
    )


@pytest.fixture
async def app(settings):
    return create_app(settings)


@pytest.fixture
async def client(app):
    # Module auto-discovery runs *all* teams' workers in this app instance.
    # Trading-core workers shut down promptly (verified standalone); the
    # generous shutdown timeout + tolerated residual TimeoutError keep this
    # suite stable even if a parallel team's worker blocks app shutdown (a
    # wedged connection never recovers, so waiting longer buys nothing).
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
async def client_user(client):
    return await login(client, CLIENT)


@pytest.fixture
async def ids(app, client):
    """Seed identifiers, read straight from the test database."""
    async with app.state.sessionmaker() as session:
        portfolios = (await session.execute(select(Portfolio))).scalars().all()
        instruments = (await session.execute(select(Instrument))).scalars().all()
    return {
        "desk": next(p.portfolio_id for p in portfolios if p.name == "Desk Book 1"),
        "client_pf": next(
            p.portfolio_id for p in portfolios if p.name == "Client Portfolio A"
        ),
        "instrument_id": {
            i.symbol: i.instrument_id for i in instruments
        },
    }


# ---------------------------------------------------------------------------
# Helpers
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


async def submit_market_buy(client, headers, portfolio_id, symbol="7203.T", qty=100):
    response = await client.post(
        "/api/v1/orders",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": portfolio_id,
            "instrument": symbol,
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": qty,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["order_id"]


async def wait_order_status(client, headers, order_id, status, timeout=5.0):
    async def check():
        response = await client.get(f"/api/v1/orders/{order_id}", headers=headers)
        assert response.status_code == 200
        body = response.json()
        return body if body["status"] == status else None

    return await wait_until(check, timeout=timeout)


# ---------------------------------------------------------------------------
# 1. Seed integrity + live prices
# ---------------------------------------------------------------------------


async def test_seed_integrity_and_live_prices(client, trader):
    items = await wait_for_prices(client, trader)  # appears within ~2 s
    assert len(items) == 10
    assert {i["symbol"] for i in items} == SYMBOLS
    for item in items:
        assert item["latest_price"] > 0
        assert item["lot_size"] == 100
        assert item["tick_size"] == 0.5
        assert item["tradable"] is True
        assert item["currency"] == "JPY"

    response = await client.get(
        "/api/v1/instruments/7203.T/prices?timeframe=1M", headers=trader
    )
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "7203.T"
    assert body["timeframe"] == "1M"
    assert len(body["candles"]) >= 25  # ~30 daily rows from generated history
    candle = body["candles"][0]
    assert {"ts", "open", "high", "low", "close", "volume"} <= set(candle)


# ---------------------------------------------------------------------------
# 2. MARKET BUY happy path: fill -> position -> cash -> audit
# ---------------------------------------------------------------------------


async def test_market_buy_end_to_end(client, app, trader, ids):
    await wait_for_prices(client, trader)
    order_id = await submit_market_buy(client, trader, ids["desk"])

    order = await wait_order_status(client, trader, order_id, "FILLED")
    assert order["executions"], "expected at least one execution"
    execution = order["executions"][0]
    assert execution["price"] > 0
    assert execution["quantity"] == 100

    # Position created and cash reduced (STP worker ran).
    async def position_ready():
        response = await client.get(
            f"/api/v1/portfolios/{ids['desk']}/positions", headers=trader
        )
        assert response.status_code == 200
        for item in response.json()["items"]:
            if item["instrument_symbol"] == "7203.T" and item["quantity"] == 100:
                return item
        return None

    position = await wait_until(position_ready)
    assert position["avg_cost"] == pytest.approx(execution["price"])
    assert position["market_value"] == pytest.approx(
        position["quantity"] * position["latest_price"]
    )

    async def cash_reduced():
        response = await client.get(
            f"/api/v1/portfolios/{ids['desk']}/valuation", headers=trader
        )
        assert response.status_code == 200
        body = response.json()
        return body if body["cash"] < float(DESK_CASH_INITIAL) else None

    valuation = await wait_until(cash_reduced)
    assert valuation["cash"] == pytest.approx(
        float(DESK_CASH_INITIAL - Decimal("100") * Decimal(str(execution["price"])))
    )

    # Audit trail contains both the submission and the fill.
    async with app.state.sessionmaker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEvent.event_type).where(
                        AuditEvent.resource_id == order_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "ORDER_SUBMITTED" in events
    assert "ORDER_FILLED" in events


# ---------------------------------------------------------------------------
# 3. Validation: absurd BUY -> 422 BUSINESS_RULE_VIOLATION + REJECTED order
# ---------------------------------------------------------------------------


async def test_validation_rejection(client, trader, ids):
    await wait_for_prices(client, trader)
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "7203.T",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 10_000_000_000,  # cost >> cash balance
        },
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "BUSINESS_RULE_VIOLATION"
    assert error["details"]
    assert error["details"][0]["code"] == "INSUFFICIENT_BUYING_POWER"

    # The rejected order is persisted and visible.
    response = await client.get(
        "/api/v1/orders", params={"status": "REJECTED"}, headers=trader
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "REJECTED"
    assert items[0]["reject_reason"] == "INSUFFICIENT_BUYING_POWER"


# ---------------------------------------------------------------------------
# 4. Idempotency: duplicate key -> 200, single order row
# ---------------------------------------------------------------------------


async def test_idempotency_key_replay(client, trader, ids):
    await wait_for_prices(client, trader)
    key = uuid.uuid4().hex
    payload = {
        "portfolio_id": ids["desk"],
        "instrument": "6758.T",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 100,
    }
    first = await client.post(
        "/api/v1/orders", json=payload, headers={**trader, "Idempotency-Key": key}
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/api/v1/orders", json=payload, headers={**trader, "Idempotency-Key": key}
    )
    assert second.status_code == 200
    assert second.json()["order_id"] == first.json()["order_id"]

    response = await client.get(
        "/api/v1/orders", params={"portfolio_id": ids["desk"]}, headers=trader
    )
    assert len(response.json()["items"]) == 1

    # Missing header -> 400 envelope.
    missing = await client.post("/api/v1/orders", json=payload, headers=trader)
    assert missing.status_code == 400


# ---------------------------------------------------------------------------
# 5. Permissions: client cannot trade; portfolio scoping enforced
# ---------------------------------------------------------------------------


async def test_permissions(client, client_user, ids):
    response = await client.post(
        "/api/v1/orders",
        headers={**client_user, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["client_pf"],
            "instrument": "7203.T",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 100,
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # Not the owner of Desk Book 1 -> 403 even with PORTFOLIO_VIEW.
    response = await client.get(
        f"/api/v1/portfolios/{ids['desk']}/positions", headers=client_user
    )
    assert response.status_code == 403

    # Own portfolio -> 200.
    response = await client.get(
        f"/api/v1/portfolios/{ids['client_pf']}/positions", headers=client_user
    )
    assert response.status_code == 200
    body = response.json()
    assert body["portfolio_id"] == ids["client_pf"]
    assert body["items"] == []
    assert body["totals"] == {"market_value": 0.0, "unrealized_pnl": 0.0}


# ---------------------------------------------------------------------------
# 6. STP: fill -> SettlementInstruction EXECUTED -> AFFIRMED -> SETTLED
# ---------------------------------------------------------------------------


async def test_stp_settlement_lifecycle(client, app, trader, ids):
    await wait_for_prices(client, trader)
    order_id = await submit_market_buy(client, trader, ids["desk"])
    order = await wait_order_status(client, trader, order_id, "FILLED")
    assert order["executions"]
    execution_id = order["executions"][0]["execution_id"]

    async def settled():
        async with app.state.sessionmaker() as session:
            instruction = (
                await session.execute(
                    select(SettlementInstruction).where(
                        SettlementInstruction.execution_id == execution_id
                    )
                )
            ).scalar_one_or_none()
            return instruction if (
                instruction is not None
                and instruction.lifecycle_state == "SETTLED"
            ) else None

    instruction = await wait_until(settled, timeout=5.0)
    assert instruction.settled_at is not None


# ---------------------------------------------------------------------------
# 7. LIMIT order: rests OPEN, cancel -> CANCELLED, second cancel -> 409
# ---------------------------------------------------------------------------


async def test_limit_order_open_then_cancel(client, trader, ids):
    await wait_for_prices(client, trader)
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "7203.T",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 100,
            "limit_price": 1.0,  # far below market: never crosses
        },
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]

    order = await wait_order_status(client, trader, order_id, "OPEN")
    assert order["limit_price"] == 1.0
    await asyncio.sleep(0.5)
    order = await client.get(f"/api/v1/orders/{order_id}", headers=trader)
    assert order.json()["status"] == "OPEN"  # still working, not filled

    cancelled = await client.post(
        f"/api/v1/orders/{order_id}/cancel", headers=trader
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    again = await client.post(f"/api/v1/orders/{order_id}/cancel", headers=trader)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "STATE_CONFLICT"


# ---------------------------------------------------------------------------
# 8. STP worker correctness: avg_cost == exec price, cash exact
# ---------------------------------------------------------------------------


async def test_stp_position_and_cash_exact(client, app, trader, ids):
    await wait_for_prices(client, trader)
    order_id = await submit_market_buy(client, trader, ids["desk"], qty=200)
    order = await wait_order_status(client, trader, order_id, "FILLED")

    async def stp_done():
        async with app.state.sessionmaker() as session:
            execution = (
                await session.execute(
                    select(Execution).where(Execution.order_id == order_id)
                )
            ).scalar_one()
            position = await session.get(
                Position, (ids["desk"], ids["instrument_id"]["7203.T"])
            )
            portfolio = await session.get(Portfolio, ids["desk"])
            if position is None or position.quantity != Decimal("200"):
                return None
            return execution, position, portfolio

    execution, position, portfolio = await wait_until(stp_done)
    price = Decimal(str(execution.price))
    assert position.avg_cost == pytest.approx(float(price))
    assert portfolio.cash_balance == pytest.approx(
        float(DESK_CASH_INITIAL - Decimal("200") * price)
    )
