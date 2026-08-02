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
    PriceTick,
    RestrictedInstrument,
    SettlementInstruction,
)
from app.main import create_app
from conftest import login

TRADER = "trader@demo.nomura"
CLIENT = "client@demo.nomura"
DESK_CASH_INITIAL = Decimal("500000")

SYMBOLS = {
    "AAPL", "GOOG", "IBM", "MSFT", "TSLA", "UL", "WMT",
}

# Generated bond universe (design 21 §A2): % of par, lot 1000 face value.
BOND_SYMBOLS = {"UST10Y", "UST2Y", "AAPL29", "MSFT31"}


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
        DATA_DIR=str(tmp_path / "no-such-data-dir"),  # fallback feed, not the real dataset
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


async def submit_market_buy(client, headers, portfolio_id, symbol="TSLA", qty=100):
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
    assert len(items) == 11  # 7 dataset equities + 4 generated bonds (§A2)
    assert {i["symbol"] for i in items} == SYMBOLS | BOND_SYMBOLS
    for item in items:
        assert item["latest_price"] > 0
        assert item["tick_size"] == 0.01
        assert item["tradable"] is True
        assert item["currency"] == "USD"
    for item in items:
        if item["asset_class"] == "EQUITY":
            assert item["lot_size"] == 1
        else:
            assert item["asset_class"] == "BOND"
            assert item["lot_size"] == 1000  # face value per lot

    response = await client.get(
        "/api/v1/instruments/TSLA/prices?timeframe=1M", headers=trader
    )
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "TSLA"
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
            if item["instrument_symbol"] == "TSLA" and item["quantity"] == 100:
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
            "instrument": "TSLA",
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
        "instrument": "GOOG",
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
            "instrument": "TSLA",
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
            "instrument": "TSLA",
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
                Position, (ids["desk"], ids["instrument_id"]["TSLA"])
            )
            portfolio = await session.get(Portfolio, ids["desk"])
            if position is None or position.quantity != Decimal("200"):
                return None
            return execution, position, portfolio

    execution, position, portfolio = await wait_until(stp_done)
    price = Decimal(str(execution.price))
    assert position.avg_cost == pytest.approx(price)
    assert portfolio.cash_balance == pytest.approx(
        DESK_CASH_INITIAL - Decimal("200") * price
    )


# ---------------------------------------------------------------------------
# 9. Bonds (A2): universe present, tradable, generated prices stay near par
# ---------------------------------------------------------------------------


async def test_bond_universe_and_calm_prices(client, app, trader, ids):
    items = await wait_for_prices(client, trader)
    bonds = {i["symbol"]: i for i in items if i["asset_class"] == "BOND"}
    assert set(bonds) == BOND_SYMBOLS
    for bond in bonds.values():
        assert bond["tradable"] is True
        assert bond["currency"] == "USD"
        assert bond["lot_size"] == 1000  # face value per lot
        assert bond["tick_size"] == 0.01
        # Mean-reverting around par 100: visibly calmer than equities.
        assert 90 <= bond["latest_price"] <= 110

    # Fallback mode: the generated 120-day daily history is persisted and
    # stays near par (dataset mode would add minute bars on the live window).
    async with app.state.sessionmaker() as session:
        closes = (
            (
                await session.execute(
                    select(PriceTick.close).where(
                        PriceTick.instrument_id == ids["instrument_id"]["UST10Y"]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(closes) >= 100
    assert all(90 <= float(close) <= 110 for close in closes)

    # The candles endpoint serves bonds like any equity.
    response = await client.get(
        "/api/v1/instruments/UST10Y/prices?timeframe=1M", headers=trader
    )
    assert response.status_code == 200
    assert len(response.json()["candles"]) >= 25


# ---------------------------------------------------------------------------
# 10. Bond cash math (A2): face value × price / 100 in validation + STP cash
# ---------------------------------------------------------------------------


async def test_bond_cash_math(client, app, trader, ids):
    await wait_for_prices(client, trader)

    # Buying power uses the bond convention: 600M face costs ~600M at par,
    # far beyond the desk's 500k cash (600M × price, unscaled, would be ~60B).
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "UST10Y",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 600_000_000,
        },
    )
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details[0]["code"] == "INSUFFICIENT_BUYING_POWER"

    # BUY 2000 face fills; the position quantity is the face value.
    order_id = await submit_market_buy(
        client, trader, ids["desk"], symbol="UST10Y", qty=2000
    )
    order = await wait_order_status(client, trader, order_id, "FILLED")
    execution = order["executions"][0]
    exec_price = Decimal(str(execution["price"]))

    async def stp_done():
        async with app.state.sessionmaker() as session:
            position = await session.get(
                Position, (ids["desk"], ids["instrument_id"]["UST10Y"])
            )
            portfolio = await session.get(Portfolio, ids["desk"])
            if position is None or position.quantity != Decimal("2000"):
                return None
            return position, portfolio

    position, portfolio = await wait_until(stp_done)
    expected_cost = Decimal("2000") * exec_price / Decimal("100")
    assert position.avg_cost == pytest.approx(exec_price)
    # Cash moves by face × price / 100 (~2k), not face × price (~200k).
    assert expected_cost < 2500
    assert portfolio.cash_balance == pytest.approx(
        DESK_CASH_INITIAL - expected_cost
    )

    # Position valuation uses the same convention.
    response = await client.get(
        f"/api/v1/portfolios/{ids['desk']}/positions", headers=trader
    )
    assert response.status_code == 200
    item = next(
        i for i in response.json()["items"] if i["instrument_symbol"] == "UST10Y"
    )
    assert item["quantity"] == 2000
    assert item["market_value"] == pytest.approx(
        2000 * item["latest_price"] / 100
    )


# ---------------------------------------------------------------------------
# 11. STOP validation (A3): stop_price required; STOP_LIMIT needs limit too
# ---------------------------------------------------------------------------


async def test_stop_price_required(client, trader, ids):
    await wait_for_prices(client, trader)
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "TSLA",
            "side": "BUY",
            "order_type": "STOP",
            "quantity": 100,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "STOP_PRICE_REQUIRED"

    # STOP_LIMIT also requires a positive limit_price.
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "TSLA",
            "side": "BUY",
            "order_type": "STOP_LIMIT",
            "quantity": 100,
            "stop_price": 500.0,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "LIMIT_PRICE_REQUIRED"


# ---------------------------------------------------------------------------
# 12. STOP SELL (A3): rests below market, triggers at/above the stop
# ---------------------------------------------------------------------------


async def test_stop_sell_resting_then_triggered(client, trader, ids):
    await wait_for_prices(client, trader)
    buy_id = await submit_market_buy(client, trader, ids["desk"], qty=100)
    await wait_order_status(client, trader, buy_id, "FILLED")

    async def position_ready():
        response = await client.get(
            f"/api/v1/portfolios/{ids['desk']}/positions", headers=trader
        )
        for item in response.json()["items"]:
            if item["instrument_symbol"] == "TSLA" and item["quantity"] == 100:
                return item
        return None

    await wait_until(position_ready)  # STP worker must book the position first

    # Stop far below the market: rests OPEN, never triggers.
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "TSLA",
            "side": "SELL",
            "order_type": "STOP",
            "quantity": 100,
            "stop_price": 1.0,
        },
    )
    assert response.status_code == 201, response.text
    resting_id = response.json()["order_id"]
    order = await wait_order_status(client, trader, resting_id, "OPEN")
    assert order["order_type"] == "STOP"
    assert order["stop_price"] == 1.0
    await asyncio.sleep(0.5)
    order = (
        await client.get(f"/api/v1/orders/{resting_id}", headers=trader)
    ).json()
    assert order["status"] == "OPEN"  # tick (100+) never falls to 1.0

    # Stop far above the market: the next tick is at/below it -> the STOP
    # fills immediately as MARKET at the tick price.
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "TSLA",
            "side": "SELL",
            "order_type": "STOP",
            "quantity": 100,
            "stop_price": 100000.0,
        },
    )
    assert response.status_code == 201, response.text
    order = await wait_order_status(
        client, trader, response.json()["order_id"], "FILLED"
    )
    assert order["executions"][0]["quantity"] == 100


# ---------------------------------------------------------------------------
# 13. STOP BUY (A3): triggers at/above the stop; far stop rests + cancels
# ---------------------------------------------------------------------------


async def test_stop_buy_triggered_and_resting(client, trader, ids):
    await wait_for_prices(client, trader)
    # Stop below the market: every tick is at/above it -> immediate fill.
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "TSLA",
            "side": "BUY",
            "order_type": "STOP",
            "quantity": 50,
            "stop_price": 1.0,
        },
    )
    assert response.status_code == 201, response.text
    order = await wait_order_status(
        client, trader, response.json()["order_id"], "FILLED"
    )
    assert order["executions"][0]["quantity"] == 50

    # Stop far above the market: rests OPEN and can be cancelled.
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "TSLA",
            "side": "BUY",
            "order_type": "STOP",
            "quantity": 50,
            "stop_price": 100000.0,
        },
    )
    assert response.status_code == 201, response.text
    resting_id = response.json()["order_id"]
    order = await wait_order_status(client, trader, resting_id, "OPEN")
    assert order["stop_price"] == 100000.0
    cancelled = await client.post(
        f"/api/v1/orders/{resting_id}/cancel", headers=trader
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


# ---------------------------------------------------------------------------
# 14. STOP_LIMIT (A3): rests untriggered; amended stop -> converts to LIMIT
# ---------------------------------------------------------------------------


async def test_stop_limit_converts_to_limit_on_trigger(client, app, trader, ids):
    await wait_for_prices(client, trader)
    # Stop far above the market: rests OPEN without triggering.
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "TSLA",
            "side": "BUY",
            "order_type": "STOP_LIMIT",
            "quantity": 100,
            "limit_price": 1.0,
            "stop_price": 100000.0,
        },
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]
    order = await wait_order_status(client, trader, order_id, "OPEN")
    assert order["order_type"] == "STOP_LIMIT"
    assert order["stop_price"] == 100000.0
    assert order["limit_price"] == 1.0

    # Amend the stop below the market: the next tick triggers and the order
    # converts in place to a resting LIMIT (limit 1.0 never crosses).
    response = await client.patch(
        f"/api/v1/orders/{order_id}", headers=trader, json={"stop_price": 1.0}
    )
    assert response.status_code == 200, response.text

    async def converted():
        body = (
            await client.get(f"/api/v1/orders/{order_id}", headers=trader)
        ).json()
        return body if body["order_type"] == "LIMIT" else None

    order = await wait_until(converted)
    assert order["status"] == "OPEN"  # BUY limit 1.0 is never marketable
    assert order["stop_price"] == 1.0  # stop kept on the record
    assert order["limit_price"] == 1.0

    # The trigger is audited with the tick price.
    async with app.state.sessionmaker() as session:
        audits = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.resource_id == order_id,
                        AuditEvent.event_type == "STOP_TRIGGERED",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(audits) == 1
    assert audits[0].payload["tick_price"]
    assert float(audits[0].payload["stop_price"]) == 1.0


# ---------------------------------------------------------------------------
# 15. Restricted list (A4): active entry blocks orders; inactive does not
# ---------------------------------------------------------------------------


async def test_restricted_instrument_blocks_orders(client, app, trader, ids):
    await wait_for_prices(client, trader)
    async with app.state.sessionmaker() as session:
        session.add(
            RestrictedInstrument(
                symbol="TSLA",
                reason="test restriction",
                active=True,
                created_by="test",
            )
        )
        await session.commit()

    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "TSLA",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 100,
        },
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "BUSINESS_RULE_VIOLATION"
    assert error["details"][0]["code"] == "RESTRICTED_INSTRUMENT"
    assert error["details"][0]["reason"] == "test restriction"

    # Deactivated entries no longer block.
    async with app.state.sessionmaker() as session:
        row = (
            await session.execute(
                select(RestrictedInstrument).where(
                    RestrictedInstrument.symbol == "TSLA"
                )
            )
        ).scalar_one()
        row.active = False
        await session.commit()

    order_id = await submit_market_buy(client, trader, ids["desk"])
    await wait_order_status(client, trader, order_id, "FILLED")


# ---------------------------------------------------------------------------
# 16. Max notional (A4): ORDER_MAX_NOTIONAL cap -> 422 with limit + notional
# ---------------------------------------------------------------------------


async def test_max_notional_exceeded(client, app, trader, ids):
    await wait_for_prices(client, trader)
    app.state.settings.ORDER_MAX_NOTIONAL = 1000.0  # tiny cap for this test
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": ids["desk"],
            "instrument": "TSLA",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 100,  # notional 100 × price (>= 100) >> 1000
        },
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "BUSINESS_RULE_VIOLATION"
    detail = error["details"][0]
    assert detail["code"] == "MAX_NOTIONAL_EXCEEDED"
    assert detail["limit"] == 1000.0
    assert detail["notional"] > 1000.0


# ---------------------------------------------------------------------------
# 17. Per-position day change (A5): prev_day_open / day_change / pct fields
# ---------------------------------------------------------------------------


async def test_positions_day_change_fields(client, trader, ids):
    await wait_for_prices(client, trader)
    order_id = await submit_market_buy(client, trader, ids["desk"], qty=100)
    await wait_order_status(client, trader, order_id, "FILLED")

    async def position_item():
        response = await client.get(
            f"/api/v1/portfolios/{ids['desk']}/positions", headers=trader
        )
        assert response.status_code == 200
        for item in response.json()["items"]:
            if item["instrument_symbol"] == "TSLA":
                return item
        return None

    item = await wait_until(position_item)
    # Workers are running, so the registry snapshot exists -> non-null (§A5).
    assert item["prev_day_open"] is not None
    assert item["day_change"] is not None
    assert item["day_change_pct"] is not None
    expected = 100 * (item["latest_price"] - item["prev_day_open"])
    assert item["day_change"] == pytest.approx(expected)
    assert item["day_change_pct"] == pytest.approx(
        item["day_change"] / (100 * item["prev_day_open"]) * 100
    )


# ---------------------------------------------------------------------------
# 9. Risk KPIs: VaR-95 and max drawdown (from valuation snapshot history)
# ---------------------------------------------------------------------------


async def test_risk_kpis_var_and_drawdown(client, app, trader, ids):
    import statistics
    from datetime import timedelta

    from app.core.models import ValuationSnapshot
    from app.core.timeutil import utcnow

    # 13 daily points with a known drawdown: peak 108 → trough 90 = 16.67%.
    series = [100, 102, 101, 105, 103, 108, 90, 95, 100, 99, 101, 104, 103]
    base = utcnow() - timedelta(days=len(series))
    async with app.state.sessionmaker() as session:
        for i, total in enumerate(series):
            session.add(
                ValuationSnapshot(
                    portfolio_id=ids["desk"],
                    ts=base + timedelta(days=i),
                    market_value=Decimal(total),
                    cash=Decimal("0"),
                    realized_pnl=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                )
            )
        await session.commit()

    response = await client.get(
        f"/api/v1/portfolios/{ids['desk']}/valuation", headers=trader
    )
    assert response.status_code == 200, response.text
    kpis = response.json()["kpis"]

    # Max drawdown: (108 - 90) / 108 * 100.
    assert kpis["max_drawdown_pct"] == pytest.approx((108 - 90) / 108 * 100)

    # VaR-95: -5th percentile of daily returns, computed independently.
    returns = [b / a - 1 for a, b in zip(series, series[1:]) if a]
    expected_var = max(0.0, -statistics.quantiles(returns, n=20)[0] * 100)
    assert kpis["var_95_1d_pct"] == pytest.approx(expected_var)
    assert kpis["var_95_1d_pct"] > 0  # the 108→90 crash lands in the left tail


async def test_risk_kpis_insufficient_history(client, ids):
    """No snapshot history → metrics are null, never a crash."""
    headers = await login(client, CLIENT)
    response = await client.get(
        f"/api/v1/portfolios/{ids['client_pf']}/valuation", headers=headers
    )
    assert response.status_code == 200, response.text
    kpis = response.json()["kpis"]
    assert kpis["var_95_1d_pct"] is None
    assert kpis["max_drawdown_pct"] is None


async def test_risk_kpis_repriced_history_fresh_book(client, app, trader, ids):
    """Fresh book: risk KPIs come from the repriced-history fallback (the
    current book marked through stored daily closes), not N/A — while the
    cash-only client portfolio (no positions) still gets N/A."""
    await wait_for_prices(client, trader)
    order_id = await submit_market_buy(client, trader, ids["desk"])
    await wait_order_status(client, trader, order_id, "FILLED")

    async def positions_present():
        response = await client.get(
            f"/api/v1/portfolios/{ids['desk']}/positions", headers=trader
        )
        items = response.json()["items"]
        return items or None

    await wait_until(positions_present)

    response = await client.get(
        f"/api/v1/portfolios/{ids['desk']}/valuation", headers=trader
    )
    assert response.status_code == 200, response.text
    kpis = response.json()["kpis"]
    assert kpis["var_95_1d_pct"] is not None
    assert kpis["max_drawdown_pct"] is not None
    assert kpis["var_95_1d_pct"] >= 0

