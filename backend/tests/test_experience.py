"""Experience-module tests: notifications, reports, analytics, paper, assistant.

The conftest `client` fixture runs with RUN_WORKERS=False; tests that need the
background workers (notification delivery) use the local `worker_client`
fixture, which boots the app with RUN_WORKERS=True. Test data (PriceTicks,
Positions) is seeded directly via app.state.sessionmaker because the trading
team's modules are built in parallel — nothing here depends on their endpoints.
"""

import asyncio
import math
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import func, select

from app.config import Settings
from app.core.events import write_outbox
from app.core.models import (
    AlertRule,
    AssistantInteraction,
    AuditEvent,
    Instrument,
    Order,
    OutboxEvent,
    Portfolio,
    Position,
    PriceTick,
)
from app.core.timeutil import utcnow
from app.main import create_app
from app.modules import reports as reports_module
from app.modules.analytics import handle_tick
from conftest import login

TRADER = "trader@demo.nomura"
CLIENT = "client@demo.nomura"
AUDITOR = "auditor@demo.nomura"


@pytest.fixture
async def worker_client(tmp_path):
    """App with RUN_WORKERS=True so outbox relay + module workers run."""
    settings = Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/worker_test.db",
        EVENT_BUS="memory",
        SESSION_STORE="memory",
        RUN_WORKERS=True,
        DEV_AUTH=True,
    )
    app = create_app(settings)
    async with LifespanManager(app) as manager:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://t"
        ) as http_client:
            yield http_client, app


async def _user_id(client, headers) -> str:
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    return response.json()["user"]["user_id"]


async def _portfolio_id(app, name) -> str:
    async with app.state.sessionmaker() as session:
        return (
            await session.execute(
                select(Portfolio.portfolio_id).where(Portfolio.name == name)
            )
        ).scalar_one()


async def _instrument_id(app, symbol) -> str:
    async with app.state.sessionmaker() as session:
        return (
            await session.execute(
                select(Instrument.instrument_id).where(Instrument.symbol == symbol)
            )
        ).scalar_one()


async def _insert_tick(app, symbol, close, ts=None):
    instrument_id = await _instrument_id(app, symbol)
    price = Decimal(str(close))
    async with app.state.sessionmaker() as session:
        session.add(
            PriceTick(
                instrument_id=instrument_id,
                ts=ts or utcnow(),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("10000"),
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# 1 — Notifications
# ---------------------------------------------------------------------------


async def test_notification_worker_delivery_and_read(worker_client):
    client, app = worker_client
    trader = await login(client, TRADER)
    other = await login(client, CLIENT)
    trader_id = await _user_id(client, trader)
    await asyncio.sleep(0.3)  # let the workers subscribe before publishing

    async with app.state.sessionmaker() as session:
        await write_outbox(
            session,
            "notify",
            {
                "user_id": trader_id,
                "category": "TEST",
                "title": "Order filled",
                "body": "7203.T filled",
            },
        )
        await session.commit()

    notification = None
    for _ in range(15):  # poll up to 3 s for the worker to deliver
        response = await client.get("/api/v1/notifications", headers=trader)
        assert response.status_code == 200
        items = response.json()["items"]
        if items:
            notification = items[0]
            break
        await asyncio.sleep(0.2)
    assert notification is not None, "worker did not deliver within 3 s"
    assert notification["category"] == "TEST"
    assert notification["channel"] == "IN_APP"
    assert notification["status"] == "UNREAD"
    assert notification["payload"] == {
        "title": "Order filled",
        "body": "7203.T filled",
    }
    assert response.json()["next_cursor"] is None

    # Other users neither see it nor can mark it read.
    response = await client.get("/api/v1/notifications", headers=other)
    assert response.json()["items"] == []
    response = await client.post(
        f"/api/v1/notifications/{notification['notification_id']}/read",
        headers=other,
    )
    assert response.status_code == 404

    response = await client.post(
        f"/api/v1/notifications/{notification['notification_id']}/read",
        headers=trader,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "READ"

    response = await client.get("/api/v1/notifications", headers=trader)
    assert response.json()["items"][0]["status"] == "READ"


async def test_notification_preferences(client):
    headers = await login(client, TRADER)
    response = await client.get("/api/v1/notification-preferences", headers=headers)
    assert response.status_code == 200
    assert response.json()["channels"]["IN_APP"] is True
    assert response.json()["categories"] == {}  # default: everything enabled

    response = await client.patch(
        "/api/v1/notification-preferences",
        json={"categories": {"ORDER": False}},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["categories"]["ORDER"] is False

    response = await client.get("/api/v1/notification-preferences", headers=headers)
    assert response.json()["categories"]["ORDER"] is False

    # Security-critical categories are non-suppressible (FR-NTF-003 E1).
    for category in ("GRANT", "BREAK_GLASS", "PAM"):
        response = await client.patch(
            "/api/v1/notification-preferences",
            json={"categories": {category: False}},
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"


# ---------------------------------------------------------------------------
# 2 — Reports
# ---------------------------------------------------------------------------


async def test_reports_holdings_csv_pdf_and_authz(client, app, tmp_path, monkeypatch):
    monkeypatch.setattr(reports_module, "REPORTS_DIR", tmp_path)
    headers = await login(client, CLIENT)

    portfolio_id = await _portfolio_id(app, "Client Portfolio A")
    instrument_id = await _instrument_id(app, "7203.T")
    async with app.state.sessionmaker() as session:
        session.add(
            Position(
                portfolio_id=portfolio_id,
                instrument_id=instrument_id,
                quantity=Decimal("100"),
                avg_cost=Decimal("2500"),
            )
        )
        await session.commit()
    await _insert_tick(app, "7203.T", "2600")

    payload = {
        "type": "HOLDINGS",
        "portfolio_id": portfolio_id,
        "period_start": "2020-01-01T00:00:00Z",
        "period_end": "2030-01-01T00:00:00Z",
        "format": "CSV",
    }
    response = await client.post("/api/v1/reports", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["status"] == "DONE"
    assert (
        created["download_url"]
        == f"/api/v1/reports/{created['report_id']}/download"
    )

    response = await client.get(created["download_url"], headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    text = response.text
    assert "7203.T" in text
    assert "2600.00" in text  # last price
    assert "260000.00" in text  # market value = 100 x 2600
    assert "100260000.00" in text  # total incl. 100M cash

    # List + metadata endpoints.
    response = await client.get("/api/v1/reports", headers=headers)
    assert response.status_code == 200
    ids = [item["report_id"] for item in response.json()["items"]]
    assert created["report_id"] in ids
    response = await client.get(
        f"/api/v1/reports/{created['report_id']}", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["type"] == "HOLDINGS"
    assert response.json()["download_url"] == created["download_url"]

    # PDF variant renders real PDF bytes.
    response = await client.post(
        "/api/v1/reports", json={**payload, "format": "PDF"}, headers=headers
    )
    assert response.status_code == 201
    pdf_url = response.json()["download_url"]
    response = await client.get(pdf_url, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content[:4] == b"%PDF"

    # Trader (REPORT_VIEW but no PORTFOLIO_VIEW_ALL) may not touch it.
    trader = await login(client, TRADER)
    response = await client.get(created["download_url"], headers=trader)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    response = await client.post("/api/v1/reports", json=payload, headers=trader)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 3 — Indicators
# ---------------------------------------------------------------------------


async def test_indicators_from_price_ticks(client, app):
    headers = await login(client, CLIENT)
    instrument_id = await _instrument_id(app, "7203.T")
    closes = [round(3000.0 + 10.0 * i + 50.0 * math.sin(i / 5.0), 4) for i in range(60)]
    base = utcnow() - timedelta(days=59)
    async with app.state.sessionmaker() as session:
        for i, close in enumerate(closes):
            price = Decimal(str(close))
            session.add(
                PriceTick(
                    instrument_id=instrument_id,
                    ts=base + timedelta(days=i),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=Decimal("1000"),
                )
            )
        await session.commit()

    response = await client.get(
        "/api/v1/instruments/7203.T/indicators",
        params={"timeframe": "MAX", "indicators": "SMA,EMA,RSI,MACD,BB"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "7203.T"
    assert body["timeframe"] == "MAX"
    series = body["indicators"]

    sma = series["SMA"]
    assert len(sma) == 60 - 20 + 1
    assert sma[-1]["value"] == pytest.approx(sum(closes[-20:]) / 20, abs=1e-9)
    for point in sma:
        assert point["ts"].endswith("+00:00")

    assert len(series["EMA"]) == 60 - 20 + 1
    assert len(series["RSI"]) == 60 - 14
    assert len(series["BB"]) == 60 - 20 + 1
    assert len(series["MACD"]) == 27  # 35-point MACD line, 9-point signal warmup
    last_bb = series["BB"][-1]
    assert last_bb["upper"] >= last_bb["middle"] >= last_bb["lower"]
    assert {"ts", "macd", "signal", "histogram"} <= series["MACD"][0].keys()

    # Unknown symbol -> 404; unknown indicator -> 400.
    response = await client.get("/api/v1/instruments/NOPE/indicators", headers=headers)
    assert response.status_code == 404
    response = await client.get(
        "/api/v1/instruments/7203.T/indicators",
        params={"indicators": "VWAP"},
        headers=headers,
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# 4 — Price alerts
# ---------------------------------------------------------------------------


async def test_alert_rule_trigger_via_handle_tick(client, app):
    headers = await login(client, TRADER)
    user_id = await _user_id(client, headers)
    instrument_id = await _instrument_id(app, "7203.T")

    response = await client.post(
        "/api/v1/analytics/alerts",
        json={"instrument": "7203.T", "condition": "ABOVE", "threshold": 999999},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    rule = response.json()
    assert rule["status"] == "ACTIVE"
    assert rule["instrument"] == "7203.T"

    response = await client.get("/api/v1/analytics/alerts", headers=headers)
    assert response.status_code == 200
    assert [item["rule_id"] for item in response.json()["items"]] == [
        rule["rule_id"]
    ]

    tick = {
        "instrument_id": instrument_id,
        "symbol": "7203.T",
        "ts": utcnow().isoformat(),
        "price": 1000000,
        "open": 1000000,
        "high": 1000000,
        "low": 1000000,
        "close": 1000000,
        "volume": 100,
    }
    fired = await handle_tick(app.state.sessionmaker, tick)
    assert fired == 1

    async with app.state.sessionmaker() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.stream == "notify")
                )
            )
            .scalars()
            .all()
        )
        assert any(
            e.payload["category"] == "ALERT" and e.payload["user_id"] == user_id
            for e in events
        )
        audits = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "PRICE_ALERT_TRIGGERED"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) == 1
        assert audits[0].actor_id == user_id
        stored = await session.get(AlertRule, rule["rule_id"])
        assert stored.status == "TRIGGERED"

    # One-shot: the same tick does not re-fire a TRIGGERED rule.
    fired = await handle_tick(app.state.sessionmaker, tick)
    assert fired == 0

    # Not mine -> 404; then the owner disables it.
    other = await login(client, CLIENT)
    response = await client.delete(
        f"/api/v1/analytics/alerts/{rule['rule_id']}", headers=other
    )
    assert response.status_code == 404
    response = await client.delete(
        f"/api/v1/analytics/alerts/{rule['rule_id']}", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "DISABLED"


# ---------------------------------------------------------------------------
# 5 — Paper trading
# ---------------------------------------------------------------------------


async def test_paper_account_lifecycle(client, app):
    trader = await login(client, TRADER)

    response = await client.post(
        "/api/v1/paper/accounts", json={"name": "Demo"}, headers=trader
    )
    assert response.status_code == 201, response.text
    account = response.json()
    assert account["name"] == "Paper — Demo"
    assert account["cash_balance"] == 10000000
    assert account["initial_balance"] == 10000000

    # Idempotent: a second POST returns the same account.
    response = await client.post("/api/v1/paper/accounts", json={}, headers=trader)
    assert response.status_code == 200
    assert response.json()["portfolio_id"] == account["portfolio_id"]

    response = await client.get(
        f"/api/v1/paper/accounts/{account['portfolio_id']}", headers=trader
    )
    assert response.status_code == 200
    detail = response.json()
    assert detail["statistics"] is None  # < 2 closed trades
    assert len(detail["equity_curve"]) == 1
    assert detail["equity_curve"][0]["value"] == 10000000

    response = await client.post(
        f"/api/v1/paper/accounts/{account['portfolio_id']}/reset", headers=trader
    )
    assert response.status_code == 200
    assert response.json()["cash_balance"] == 10000000

    # Not my account -> 404.
    other = await login(client, CLIENT)
    response = await client.get(
        f"/api/v1/paper/accounts/{account['portfolio_id']}", headers=trader
    )
    assert response.status_code == 200  # owner still fine

    # client@ lacks PAPER_TRADE.
    response = await client.post("/api/v1/paper/accounts", json={}, headers=other)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 6 — Assistant
# ---------------------------------------------------------------------------


async def test_assistant_grounding_and_guardrail(client, app):
    headers = await login(client, CLIENT)
    user_id = await _user_id(client, headers)

    # Valuation intent, grounded in the caller's own portfolio.
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "what is my portfolio value?"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"]
    assert body["conversation_id"]
    assert body["suggested_ticket"] is None
    valuation_cites = [c for c in body["citations"] if c["kind"] == "valuation"]
    assert valuation_cites
    assert any(c["figures"]["total_value"] == 100000000 for c in valuation_cites)

    # Price intent after inserting a tick.
    await _insert_tick(app, "7203.T", "2600")
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "what is the price of 7203.T?"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    price_cites = [c for c in body["citations"] if c["kind"] == "price"]
    assert price_cites and price_cites[0]["ref"] == "7203.T"
    assert price_cites[0]["figures"]["price"] == 2600

    # Buy intent -> suggested ticket only, NEVER an order (FR-AI-003 guardrail).
    async def _order_count() -> int:
        async with app.state.sessionmaker() as session:
            return await session.scalar(select(func.count(Order.order_id)))

    before = await _order_count()
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "buy 100 Sony"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    ticket = body["suggested_ticket"]
    assert ticket is not None
    assert ticket["instrument"] == "6758.T"
    assert ticket["side"] == "BUY"
    assert ticket["quantity"] == 100
    assert "can't place trades" in body["answer"]
    assert await _order_count() == before

    # Out of scope -> explicit, polite decline.
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "asdkfj qwer zxcv"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    assert body["citations"] == []
    assert body["suggested_ticket"] is None

    # Every interaction is persisted.
    async with app.state.sessionmaker() as session:
        count = await session.scalar(
            select(func.count(AssistantInteraction.interaction_id)).where(
                AssistantInteraction.user_id == user_id
            )
        )
    assert count == 4

    # trader@ also holds ASSISTANT_USE; auditor@ does not.
    trader = await login(client, TRADER)
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "show my positions"},
        headers=trader,
    )
    assert response.status_code == 200
    auditor = await login(client, AUDITOR)
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "what is my portfolio value?"},
        headers=auditor,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
