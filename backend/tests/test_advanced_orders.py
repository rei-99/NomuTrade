"""Advanced orders (design 24): time-in-force, TRAILING_STOP, bond analytics.

Two test styles, mirroring the existing suites:
- worker-driven integration tests (same fixtures/patterns as
  test_trading.py): the random-walk fallback feed drives fills; extreme
  trail/TIF parameters make outcomes deterministic;
- engine-drive tests: ticks are injected straight into the registry and
  `_on_tick` is invoked directly, so reference tracking, DAY expiry and IOC
  cancellation are exercised with exact prices — no randomness.
Plus pure-math unit tests for analytics/bonds.py.
"""

import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from app.config import Settings
from app.core.models import AuditEvent, Instrument, Order, Portfolio, Position
from app.core.timeutil import utcnow
from app.main import create_app
from app.modules.analytics import bonds as bond_math
from app.modules.marketdata.registry import set_tick
from app.modules.orders import workers
from conftest import login
from test_trading import wait_until, wait_for_prices, wait_order_status

TRADER = "trader@demo.nomura"

# TSLA fallback random walk starts 100–500; a trail this wide can never be
# crossed from below/above, a trail this tight triggers within a few ticks.
WIDE_TRAIL = 100000.0
TIGHT_TRAIL = 0.01


@pytest.fixture
def settings(tmp_path):
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/adv_orders_test.db",
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
        instruments = (await session.execute(select(Instrument))).scalars().all()
    return {
        "desk": next(p.portfolio_id for p in portfolios if p.name == "Desk Book 1"),
        "instrument_id": {i.symbol: i.instrument_id for i in instruments},
    }


# --- no-worker variant (engine-drive tests) --------------------------------
# Deterministic: no replayer and no engine consume ticks/events on their own,
# so tests inject ticks into the registry and drive the engine by hand.


@pytest.fixture
def nw_settings(tmp_path):
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/adv_orders_nw_test.db",
        EVENT_BUS="memory",
        SESSION_STORE="memory",
        RUN_WORKERS=False,
        DEV_AUTH=True,
        DATA_DIR=str(tmp_path / "no-such-data-dir"),
    )


@pytest.fixture
async def nw_app(nw_settings):
    return create_app(nw_settings)


@pytest.fixture
async def nw_client(nw_app):
    async with LifespanManager(nw_app) as manager:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://t"
        ) as http_client:
            yield http_client


@pytest.fixture
async def nw_ids(nw_app, nw_client):
    async with nw_app.state.sessionmaker() as session:
        portfolios = (await session.execute(select(Portfolio))).scalars().all()
        instruments = (await session.execute(select(Instrument))).scalars().all()
    return {
        "desk": next(p.portfolio_id for p in portfolios if p.name == "Desk Book 1"),
        "instrument_id": {i.symbol: i.instrument_id for i in instruments},
    }


async def submit_order(client, headers, portfolio_id, **fields):
    payload = {
        "portfolio_id": portfolio_id,
        "instrument": "TSLA",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 100,
        **fields,
    }
    return await client.post(
        "/api/v1/orders",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json=payload,
    )


async def audit_events(app, order_id, event_type=None):
    async with app.state.sessionmaker() as session:
        stmt = select(AuditEvent).where(AuditEvent.resource_id == order_id)
        if event_type is not None:
            stmt = stmt.where(AuditEvent.event_type == event_type)
        return (await session.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# TIF — validation + order_json exposure
# ---------------------------------------------------------------------------


async def test_tif_defaults_to_gtc_and_is_exposed(client, trader, ids):
    await wait_for_prices(client, trader)
    response = await submit_order(
        client, trader, ids["desk"], order_type="LIMIT", limit_price=1.0
    )
    assert response.status_code == 201, response.text
    order = await wait_order_status(
        client, trader, response.json()["order_id"], "OPEN"
    )
    assert order["time_in_force"] == "GTC"  # omitted -> GTC
    assert order["expire_after"] is None
    assert order["trail_amount"] is None
    assert order["trail_pct"] is None
    assert order["trail_reference"] is None

    # Garbage TIF is a schema error (400 envelope, not a 422 rule).
    response = await submit_order(client, trader, ids["desk"], time_in_force="FOK")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# TIF — IOC: cancel-if-not-immediately-marketable, fill when marketable
# ---------------------------------------------------------------------------


async def test_ioc_limit_beyond_market_cancelled(client, app, trader, ids):
    await wait_for_prices(client, trader)
    response = await submit_order(
        client,
        trader,
        ids["desk"],
        order_type="LIMIT",
        limit_price=1.0,  # far below market: never marketable
        time_in_force="IOC",
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]
    order = await wait_order_status(client, trader, order_id, "CANCELLED")
    assert order["reject_reason"] == "IOC_UNFILLED"

    events = await audit_events(app, order_id, "ORDER_CANCELLED")
    assert len(events) == 1
    assert events[0].payload["reason"] == "IOC_UNFILLED"


async def test_ioc_marketable_limit_fills(client, trader, ids):
    await wait_for_prices(client, trader)
    response = await submit_order(
        client,
        trader,
        ids["desk"],
        order_type="LIMIT",
        limit_price=100000.0,  # far above market: immediately marketable
        time_in_force="IOC",
    )
    assert response.status_code == 201, response.text
    order = await wait_order_status(
        client, trader, response.json()["order_id"], "FILLED"
    )
    assert order["executions"][0]["quantity"] == 100


# ---------------------------------------------------------------------------
# TIF — DAY: expire_after set from the sim clock; ORDER_EXPIRED on expiry
# ---------------------------------------------------------------------------


async def test_day_order_sets_expire_after(client, trader, ids):
    await wait_for_prices(client, trader)
    response = await submit_order(
        client,
        trader,
        ids["desk"],
        order_type="LIMIT",
        limit_price=1.0,
        time_in_force="DAY",
    )
    assert response.status_code == 201, response.text
    order = await wait_order_status(
        client, trader, response.json()["order_id"], "OPEN"
    )
    assert order["time_in_force"] == "DAY"
    # Fallback feed: the sim clock is wall time -> expiry is end of today.
    assert order["expire_after"].startswith(utcnow().date().isoformat())
    assert order["expire_after"].endswith("+00:00")


async def test_day_order_expires_on_tick_beyond_expire_after(client, app, trader, ids):
    """DAY expiry, engine-driven deterministically: a tick whose ts passes
    expire_after cancels the resting order with an ORDER_EXPIRED audit."""
    await wait_for_prices(client, trader)
    response = await submit_order(
        client,
        trader,
        ids["desk"],
        order_type="LIMIT",
        limit_price=1.0,
        time_in_force="DAY",
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]
    await wait_order_status(client, trader, order_id, "OPEN")

    # Pretend the market day ended: expire the order, then let the next live
    # tick (always stamped "now") cross the deadline.
    async with app.state.sessionmaker() as session:
        order = await session.get(Order, order_id)
        order.expire_after = utcnow() - timedelta(seconds=1)
        await session.commit()

    order = await wait_order_status(client, trader, order_id, "CANCELLED")
    assert order["reject_reason"] is None  # expiry is not a rejection

    events = await audit_events(app, order_id, "ORDER_EXPIRED")
    assert len(events) == 1
    assert events[0].payload["symbol"] == "TSLA"
    assert events[0].payload["expire_after"].endswith("+00:00")


# ---------------------------------------------------------------------------
# GTC — unchanged behavior: rests; book rebuild re-enters it
# ---------------------------------------------------------------------------


async def test_gtc_rests_and_book_rebuild_reenters(client, app, trader, ids):
    await wait_for_prices(client, trader)
    response = await submit_order(
        client, trader, ids["desk"], order_type="LIMIT", limit_price=1.0
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]
    await wait_order_status(client, trader, order_id, "OPEN")

    # Simulate an engine restart: rebuild from the DB re-enters the order.
    book = defaultdict(set)
    await workers._rebuild_book(app.state.sessionmaker, book)
    assert order_id in book[ids["instrument_id"]["TSLA"]]
    order = (
        await client.get(f"/api/v1/orders/{order_id}", headers=trader)
    ).json()
    assert order["status"] == "OPEN"  # rebuild leaves GTC orders working


# ---------------------------------------------------------------------------
# TRAILING_STOP — validation (422s)
# ---------------------------------------------------------------------------


async def test_trailing_stop_validation(client, trader, ids):
    await wait_for_prices(client, trader)

    async def submit(**fields):
        return await submit_order(
            client, trader, ids["desk"], order_type="TRAILING_STOP", **fields
        )

    # Neither trail param.
    response = await submit()
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "TRAIL_PARAM_REQUIRED"

    # Both trail params.
    response = await submit(trail_amount=1.0, trail_pct=5.0)
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "TRAIL_PARAM_CONFLICT"

    # Non-positive trail param.
    response = await submit(trail_amount=0)
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "TRAIL_PARAM_REQUIRED"

    # Fixed prices are forbidden on TRAILING_STOP.
    response = await submit(trail_amount=1.0, stop_price=100.0)
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "PRICE_FIELD_FORBIDDEN"

    # Trail params are forbidden on other order types.
    response = await submit_order(
        client, trader, ids["desk"], order_type="LIMIT",
        limit_price=100.0, trail_amount=1.0,
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "TRAIL_PARAM_FORBIDDEN"


# ---------------------------------------------------------------------------
# TRAILING_STOP — reference tracking + trigger, engine-driven with exact ticks
# ---------------------------------------------------------------------------


async def _accept(nw_app, order_id):
    """Mirror the live accept path: fill attempt (initializes the trail
    reference) + park OPEN, returning the engine book for tick drives."""
    book = defaultdict(set)
    await workers._on_accepted(nw_app.state.sessionmaker, book, order_id)
    return book


async def test_trailing_sell_reference_tracking_then_trigger(nw_app, nw_client, nw_ids):
    """SELL trail, deterministic ticks: up-moves only raise the water-mark
    (never trigger); the first tick through the trail fills as MARKET."""
    headers = await login(nw_client, TRADER)
    tsla = nw_ids["instrument_id"]["TSLA"]
    set_tick(tsla, "TSLA", Decimal("100"), utcnow(), Decimal("100"))
    async with nw_app.state.sessionmaker() as session:
        session.add(
            Position(
                portfolio_id=nw_ids["desk"], instrument_id=tsla,
                quantity=Decimal("100"), avg_cost=Decimal("100"),
            )
        )
        await session.commit()

    response = await submit_order(
        nw_client, headers, nw_ids["desk"],
        side="SELL", order_type="TRAILING_STOP", quantity=100, trail_amount=1.0,
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]
    book = await _accept(nw_app, order_id)
    assert order_id in book[tsla]

    async def drive(price):
        set_tick(tsla, "TSLA", Decimal(str(price)), utcnow(), Decimal("100"))
        await workers._on_tick(nw_app.state.sessionmaker, book, tsla)

    async def current():
        async with nw_app.state.sessionmaker() as session:
            return await session.get(Order, order_id)

    # First tick seen after acceptance initializes the water-mark.
    order = await current()
    assert order.status == "OPEN" and order.trail_reference == Decimal("100")

    # Up-moves: the reference tracks the high-water mark; nothing triggers.
    await drive(600.0)
    order = await current()
    assert order.status == "OPEN" and order.trail_reference == Decimal("600")
    await drive(700.0)
    order = await current()
    assert order.status == "OPEN" and order.trail_reference == Decimal("700")

    # 699.5 is above the 699.0 trigger: rests, water-mark unchanged.
    await drive(699.5)
    order = await current()
    assert order.status == "OPEN" and order.trail_reference == Decimal("700")

    # 699.0 <= 700 - 1.0: triggered -> FILLED as MARKET at the tick.
    await drive(699.0)
    order = await current()
    assert order.status == "FILLED"
    assert order.order_type == "TRAILING_STOP"  # fills in place, no conversion
    assert order.trail_reference == Decimal("700")  # water-mark kept
    assert order.executions[0].price == Decimal("699")
    assert order.executions[0].quantity == Decimal("100")

    async with nw_app.state.sessionmaker() as session:
        events = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.resource_id == order_id,
                    AuditEvent.event_type == "STOP_TRIGGERED",
                )
            )
        ).scalars().all()
    assert len(events) == 1
    payload = events[0].payload
    assert float(payload["trail_amount"]) == 1.0
    assert float(payload["trail_reference"]) == 700.0
    assert float(payload["tick_price"]) == 699.0
    assert payload["trail_pct"] is None


async def test_trailing_buy_pct_triggers(nw_app, nw_client, nw_ids):
    """BUY trail_pct mirror: the reference tracks the low-water mark; the
    fill fires at reference x (1 + pct/100)."""
    headers = await login(nw_client, TRADER)
    tsla = nw_ids["instrument_id"]["TSLA"]
    set_tick(tsla, "TSLA", Decimal("100"), utcnow(), Decimal("100"))

    response = await submit_order(
        nw_client, headers, nw_ids["desk"],
        side="BUY", order_type="TRAILING_STOP", quantity=100, trail_pct=10.0,
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]
    book = await _accept(nw_app, order_id)

    async def drive(price):
        set_tick(tsla, "TSLA", Decimal(str(price)), utcnow(), Decimal("100"))
        await workers._on_tick(nw_app.state.sessionmaker, book, tsla)

    async def current():
        async with nw_app.state.sessionmaker() as session:
            return await session.get(Order, order_id)

    order = await current()  # acceptance tick initialized the low-water mark
    assert order.status == "OPEN" and order.trail_reference == Decimal("100")

    await drive(90.0)  # new low: reference follows, trigger now 99.0
    order = await current()
    assert order.status == "OPEN" and order.trail_reference == Decimal("90")
    await drive(95.0)  # below the 99.0 trigger: rests, water-mark kept
    order = await current()
    assert order.status == "OPEN" and order.trail_reference == Decimal("90")
    await drive(99.0)  # 90 x 1.10: triggered -> FILLED as MARKET at the tick
    order = await current()
    assert order.status == "FILLED"
    assert order.executions[0].price == Decimal("99")

    async with nw_app.state.sessionmaker() as session:
        events = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.resource_id == order_id,
                    AuditEvent.event_type == "STOP_TRIGGERED",
                )
            )
        ).scalars().all()
    assert float(events[0].payload["trail_pct"]) == 10.0
    assert events[0].payload["trail_amount"] is None


async def test_trailing_stop_wide_trail_rests_and_amends(client, trader, ids):
    """Live-feed integration: a wide trail never triggers (rests OPEN) and
    the trail is amendable; a tightened trail then triggers on the walk."""
    await wait_for_prices(client, trader)
    response = await submit_order(
        client, trader, ids["desk"],
        order_type="TRAILING_STOP", trail_amount=WIDE_TRAIL,
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]
    order = await wait_order_status(client, trader, order_id, "OPEN")
    assert order["order_type"] == "TRAILING_STOP"
    assert order["trail_amount"] == WIDE_TRAIL

    async def reference_set():
        body = (
            await client.get(f"/api/v1/orders/{order_id}", headers=trader)
        ).json()
        return body if body["trail_reference"] is not None else None

    order = await wait_until(reference_set)  # first tick initializes it
    assert order["status"] == "OPEN"
    assert order["trail_reference"] > 0

    # Amending the trail: providing trail_pct replaces trail_amount.
    response = await client.patch(
        f"/api/v1/orders/{order_id}", headers=trader, json={"trail_pct": 95.0}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trail_pct"] == 95.0
    assert body["trail_amount"] is None

    # Tighten to one tick: the random walk crosses it within a few ticks.
    response = await client.patch(
        f"/api/v1/orders/{order_id}", headers=trader, json={"trail_amount": TIGHT_TRAIL}
    )
    assert response.status_code == 200, response.text
    order = await wait_order_status(client, trader, order_id, "FILLED")
    assert order["executions"][0]["quantity"] == 100


# ---------------------------------------------------------------------------
# Bond analytics — pure math (hand-computed values)
# ---------------------------------------------------------------------------


def test_bond_price_at_coupon_yield_is_par():
    # Integer periods: yield == coupon -> price exactly 100 (any n).
    assert bond_math.price_from_yield(4.25, 9, 4.25) == pytest.approx(100.0)
    assert bond_math.price_from_yield(3.75, 1, 3.75) == pytest.approx(100.0)
    # Yield below coupon -> above par, and vice versa.
    assert bond_math.price_from_yield(4.25, 9, 3.25) > 100.0
    assert bond_math.price_from_yield(4.25, 9, 5.25) < 100.0


def test_bond_ytm_at_par_is_coupon():
    assert bond_math.solve_ytm(100.0, 4.25, 9) == pytest.approx(4.25, abs=1e-6)
    assert bond_math.solve_ytm(100.0, 3.75, 1) == pytest.approx(3.75, abs=1e-6)
    # Round-trip: implied price at a yield solves back to that yield.
    price = bond_math.price_from_yield(4.25, 9, 5.0)
    assert bond_math.solve_ytm(price, 4.25, 9) == pytest.approx(5.0, abs=1e-6)


def test_bond_duration_hand_computed():
    # 2-year 5% annual bond at 5% yield: price 100.
    # MacDur = (1*5/1.05 + 2*105/1.05^2) / 100 = 1.952380...
    r = 0.05
    mac = (1 * 5 / (1 + r) + 2 * 105 / (1 + r) ** 2) / 100.0
    assert bond_math.macaulay_duration(5.0, 2, 5.0) == pytest.approx(mac)
    assert bond_math.modified_duration(5.0, 2, 5.0) == pytest.approx(mac / 1.05)
    # Zero-coupon profile (coupon 0): duration equals maturity.
    assert bond_math.macaulay_duration(0.0, 5, 4.0) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Bond analytics — endpoint
# ---------------------------------------------------------------------------


async def test_bond_analytics_endpoint(client, app, trader, ids):
    await wait_for_prices(client, trader)
    response = await client.get(
        "/api/v1/instruments/UST10Y/bond-analytics", headers=trader
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "UST10Y"
    assert body["coupon_rate"] == 4.25
    assert body["maturity_date"] == "2035-08-15"
    assert 8.0 < body["years_to_maturity"] < 10.0
    assert body["payments_remaining"] == 9
    assert 90 <= body["latest_price"] <= 110  # generated prices stay near par
    # A bond trading within ~10% of par yields within ~1.3% of its coupon.
    assert body["ytm"] == pytest.approx(4.25, abs=1.5)
    assert 6.0 < body["modified_duration"] < 8.0
    assert "implied_price" not in body  # only present when yield supplied

    # yield == coupon -> implied price exactly par (integer periods).
    response = await client.get(
        "/api/v1/instruments/UST10Y/bond-analytics?yield=4.25", headers=trader
    )
    assert response.status_code == 200, response.text
    assert response.json()["implied_price"] == pytest.approx(100.0, abs=1e-3)
    # Lower yield -> above par.
    response = await client.get(
        "/api/v1/instruments/UST10Y/bond-analytics?yield=3.25", headers=trader
    )
    assert response.json()["implied_price"] > 100.0


async def test_bond_analytics_404_for_non_bonds(client, trader, ids):
    await wait_for_prices(client, trader)
    response = await client.get(
        "/api/v1/instruments/AAPL/bond-analytics", headers=trader
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_loader_backfills_bond_fields(app, ids):
    """Existing rows missing the design-24 bond fields are backfilled by the
    loader's boot upsert (never overwritten once set)."""
    async with app.state.sessionmaker() as session:
        bond = (
            await session.execute(
                select(Instrument).where(Instrument.symbol == "AAPL29")
            )
        ).scalar_one()
        bond.coupon_rate = None
        bond.maturity_date = None
        ust = (
            await session.execute(
                select(Instrument).where(Instrument.symbol == "UST10Y")
            )
        ).scalar_one()
        ust.coupon_rate = Decimal("9.99")  # must survive the backfill
        await session.commit()

        from app.modules.marketdata.loader import ensure_dataset_instruments

        await ensure_dataset_instruments(session)
        await session.commit()

        bond = (
            await session.execute(
                select(Instrument).where(Instrument.symbol == "AAPL29")
            )
        ).scalar_one()
        assert bond.coupon_rate == Decimal("3.40")
        assert bond.maturity_date is not None
        assert bond.maturity_date.year == 2029
        ust = (
            await session.execute(
                select(Instrument).where(Instrument.symbol == "UST10Y")
            )
        ).scalar_one()
        assert ust.coupon_rate == Decimal("9.99")  # not overwritten
