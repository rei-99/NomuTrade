"""Experience-module tests: notifications, reports, analytics, paper, assistant.

The conftest `client` fixture runs with RUN_WORKERS=False; tests that need the
background workers (notification delivery) use the local `worker_client`
fixture, which boots the app with RUN_WORKERS=True. Test data (PriceTicks,
Positions) is seeded directly via app.state.sessionmaker because the trading
team's modules are built in parallel — nothing here depends on their endpoints.
"""

import asyncio
import math
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

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
    Report,
    ReportSchedule,
)
from app.core.timeutil import as_utc, utcnow
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
        DATA_DIR=str(tmp_path / "no-such-data-dir"),  # fallback feed, not the real dataset
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
                "body": "TSLA filled",
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
        "body": "TSLA filled",
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
    instrument_id = await _instrument_id(app, "TSLA")
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
    await _insert_tick(app, "TSLA", "2600")

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
    assert "TSLA" in text
    assert "2600.00" in text  # last price
    assert "260000.00" in text  # market value = 100 x 2600
    assert "1260000.00" in text  # total incl. 1M cash

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


async def test_reports_holdings_bond_market_value(client, app, tmp_path, monkeypatch):
    """Bond cash math (§A2): holdings reports value bonds at qty x price / 100
    (quoted % of par, quantity = face), matching the portfolios module."""
    monkeypatch.setattr(reports_module, "REPORTS_DIR", tmp_path)
    headers = await login(client, CLIENT)

    portfolio_id = await _portfolio_id(app, "Client Portfolio A")
    instrument_id = await _instrument_id(app, "UST10Y")
    async with app.state.sessionmaker() as session:
        session.add(
            Position(
                portfolio_id=portfolio_id,
                instrument_id=instrument_id,
                quantity=Decimal("2000"),  # face value (2 x 1000 lots)
                avg_cost=Decimal("99.50"),
            )
        )
        await session.commit()
    await _insert_tick(app, "UST10Y", "99.25")

    response = await client.post(
        "/api/v1/reports",
        json={
            "type": "HOLDINGS",
            "portfolio_id": portfolio_id,
            "period_start": "2020-01-01T00:00:00Z",
            "period_end": "2030-01-01T00:00:00Z",
            "format": "CSV",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text

    response = await client.get(
        response.json()["download_url"], headers=headers
    )
    assert response.status_code == 200
    text = response.text
    assert "UST10Y" in text
    assert "99.25" in text  # last price, quoted % of par
    # market value = 2000 x 99.25 / 100 = 1985.00; the 100x bug would
    # render 198500.00.
    assert "1985.00" in text
    assert "198500.00" not in text
    assert "1001985.00" in text  # total incl. 1M cash


async def test_report_generation_failure_marks_failed(
    client, app, tmp_path, monkeypatch
):
    """A render failure marks the row FAILED (never stuck REQUESTED), cleans
    up the partial file, audits REPORT_FAILED, and download returns a clear
    409 — not the misleading 404 'report file is missing'."""
    monkeypatch.setattr(reports_module, "REPORTS_DIR", tmp_path)
    headers = await login(client, CLIENT)
    portfolio_id = await _portfolio_id(app, "Client Portfolio A")

    def _boom(path, title, header_row, rows, summary):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(reports_module, "_write_csv", _boom)

    response = await client.post(
        "/api/v1/reports",
        json={
            "type": "HOLDINGS",
            "portfolio_id": portfolio_id,
            "period_start": "2020-01-01T00:00:00Z",
            "period_end": "2030-01-01T00:00:00Z",
            "format": "CSV",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["status"] == "FAILED"

    # The partially rendered file is removed.
    assert not list(tmp_path.glob(f"{created['report_id']}.*"))

    # Metadata shows the terminal FAILED state...
    response = await client.get(
        f"/api/v1/reports/{created['report_id']}", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "FAILED"

    # ...the failure is audited...
    async with app.state.sessionmaker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "REPORT_FAILED",
                        AuditEvent.resource_id == created["report_id"],
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert "render exploded" in events[0].payload["error"]

    # ...and download answers a clear 409, not the 404 file-missing branch.
    response = await client.get(created["download_url"], headers=headers)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "STATE_CONFLICT"
    assert "failed" in error["message"].lower()


# ---------------------------------------------------------------------------
# 2b — Scheduled reports (design 23, TBD-13)
# ---------------------------------------------------------------------------


async def _create_schedule(client, headers, portfolio_id, **overrides):
    payload = {
        "portfolio_id": portfolio_id,
        "type": "HOLDINGS",
        "format": "CSV",
        "frequency": "DAILY",
        **overrides,
    }
    return await client.post(
        "/api/v1/report-schedules", json=payload, headers=headers
    )


async def test_report_schedule_crud(client, app):
    headers = await login(client, CLIENT)
    portfolio_id = await _portfolio_id(app, "Client Portfolio A")

    response = await _create_schedule(client, headers, portfolio_id)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["type"] == "HOLDINGS"
    assert created["format"] == "CSV"
    assert created["frequency"] == "DAILY"
    assert created["active"] is True
    assert created["last_run_at"] is None
    next_run = datetime.fromisoformat(created["next_run_at"])
    created_at = datetime.fromisoformat(created["created_at"])
    # First run one frequency boundary from creation (no backfill).
    assert timedelta(hours=23) < next_run - created_at <= timedelta(days=1)

    response = await client.get("/api/v1/report-schedules", headers=headers)
    assert response.status_code == 200
    assert response.json()["next_cursor"] is None
    ids = [item["schedule_id"] for item in response.json()["items"]]
    assert ids == [created["schedule_id"]]

    # Another user's schedule is invisible: empty list + 404 on delete.
    trader = await login(client, TRADER)
    response = await client.get("/api/v1/report-schedules", headers=trader)
    assert response.json()["items"] == []
    response = await client.delete(
        f"/api/v1/report-schedules/{created['schedule_id']}", headers=trader
    )
    assert response.status_code == 404

    # Trader (REPORT_VIEW but not owner, no PORTFOLIO_VIEW_ALL) may not
    # schedule against the client's portfolio — same check as POST /reports.
    response = await _create_schedule(client, trader, portfolio_id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # Owner deletes; a second delete and the list confirm it is gone.
    response = await client.delete(
        f"/api/v1/report-schedules/{created['schedule_id']}", headers=headers
    )
    assert response.status_code == 200
    response = await client.delete(
        f"/api/v1/report-schedules/{created['schedule_id']}", headers=headers
    )
    assert response.status_code == 404
    response = await client.get("/api/v1/report-schedules", headers=headers)
    assert response.json()["items"] == []


async def test_report_schedule_active_cap(client, app):
    headers = await login(client, CLIENT)
    portfolio_id = await _portfolio_id(app, "Client Portfolio A")
    combos = [
        {"type": t, "format": f, "frequency": freq}
        for t in ("HOLDINGS", "TRANSACTIONS", "PERFORMANCE")
        for f in ("CSV", "PDF")
        for freq in ("DAILY", "WEEKLY")
    ]
    created_ids = []
    for combo in combos[:10]:
        response = await _create_schedule(client, headers, portfolio_id, **combo)
        assert response.status_code == 201, response.text
        created_ids.append(response.json()["schedule_id"])
    response = await _create_schedule(client, headers, portfolio_id, **combos[10])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"

    # Deleting one frees the slot immediately (hard delete).
    response = await client.delete(
        f"/api/v1/report-schedules/{created_ids[0]}", headers=headers
    )
    assert response.status_code == 200
    response = await _create_schedule(client, headers, portfolio_id, **combos[10])
    assert response.status_code == 201, response.text


async def test_report_scheduler_generates_due_reports(
    client, app, tmp_path, monkeypatch
):
    """Due schedules generate reports on the sim clock (wall clock in tests).

    Deterministic: the due-processing path is invoked function-level rather
    than via the wall-clock worker loop.
    """
    monkeypatch.setattr(reports_module, "REPORTS_DIR", tmp_path)
    headers = await login(client, CLIENT)
    owner_id = await _user_id(client, headers)
    portfolio_id = await _portfolio_id(app, "Client Portfolio A")

    due_daily = utcnow() - timedelta(hours=1)
    due_weekly = utcnow() - timedelta(hours=2)
    async with app.state.sessionmaker() as session:
        session.add_all(
            [
                ReportSchedule(
                    user_id=owner_id,
                    portfolio_id=portfolio_id,
                    type="HOLDINGS",
                    format="CSV",
                    frequency="DAILY",
                    next_run_at=due_daily,
                ),
                ReportSchedule(
                    user_id=owner_id,
                    portfolio_id=portfolio_id,
                    type="PERFORMANCE",
                    format="CSV",
                    frequency="WEEKLY",
                    next_run_at=due_weekly,
                ),
            ]
        )
        await session.commit()

    generated = await reports_module.process_due_schedules(app.state.sessionmaker)
    assert generated == 2

    async with app.state.sessionmaker() as session:
        reports = (
            (await session.execute(select(Report).order_by(Report.type)))
            .scalars()
            .all()
        )
        assert [r.type for r in reports] == ["HOLDINGS", "PERFORMANCE"]
        by_type = {r.type: r for r in reports}
        daily = by_type["HOLDINGS"]
        assert daily.status == "DONE"
        assert daily.format == "CSV"
        assert daily.requested_by == owner_id
        assert as_utc(daily.period_end) == as_utc(due_daily)
        assert as_utc(daily.period_start) == as_utc(due_daily) - timedelta(days=1)
        assert Path(daily.file_ref).is_file()
        weekly = by_type["PERFORMANCE"]
        assert as_utc(weekly.period_end) == as_utc(due_weekly)
        assert as_utc(weekly.period_start) == as_utc(due_weekly) - timedelta(days=7)

        schedules = (
            (await session.execute(select(ReportSchedule))).scalars().all()
        )
        by_freq = {s.frequency: s for s in schedules}
        # Advanced by exactly one frequency step; last_run_at = processed due.
        assert as_utc(by_freq["DAILY"].next_run_at) == as_utc(due_daily) + timedelta(
            days=1
        )
        assert as_utc(by_freq["DAILY"].last_run_at) == as_utc(due_daily)
        assert as_utc(by_freq["WEEKLY"].next_run_at) == as_utc(
            due_weekly
        ) + timedelta(days=7)

        notifications = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.stream == "notify")
                )
            )
            .scalars()
            .all()
        )
        assert len(notifications) == 2
        assert all(e.payload["user_id"] == owner_id for e in notifications)
        assert all(e.payload["category"] == "REPORT" for e in notifications)
        assert all("scheduled" in e.payload["body"] for e in notifications)

    # Generated reports appear in the ordinary report history.
    response = await client.get("/api/v1/reports", headers=headers)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2

    # Not due any more: a second immediate run generates nothing.
    assert await reports_module.process_due_schedules(app.state.sessionmaker) == 0
    async with app.state.sessionmaker() as session:
        count = await session.scalar(select(func.count(Report.report_id)))
        assert count == 2


# ---------------------------------------------------------------------------
# 3 — Indicators
# ---------------------------------------------------------------------------


async def test_indicators_from_price_ticks(client, app):
    headers = await login(client, CLIENT)
    instrument_id = await _instrument_id(app, "TSLA")
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
        "/api/v1/instruments/TSLA/indicators",
        params={"timeframe": "MAX", "indicators": "SMA,EMA,RSI,MACD,BB"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "TSLA"
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
        "/api/v1/instruments/TSLA/indicators",
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
    instrument_id = await _instrument_id(app, "TSLA")

    response = await client.post(
        "/api/v1/analytics/alerts",
        json={"instrument": "TSLA", "condition": "ABOVE", "threshold": 999999},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    rule = response.json()
    assert rule["status"] == "ACTIVE"
    assert rule["instrument"] == "TSLA"

    response = await client.get("/api/v1/analytics/alerts", headers=headers)
    assert response.status_code == 200
    assert [item["rule_id"] for item in response.json()["items"]] == [
        rule["rule_id"]
    ]

    tick = {
        "instrument_id": instrument_id,
        "symbol": "TSLA",
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


async def test_paper_reset_cancels_open_orders(client, app):
    """Reset must cancel working orders: an OPEN/ACCEPTED order left alive
    would otherwise keep working and fill against the reset account."""
    trader = await login(client, TRADER)

    response = await client.post(
        "/api/v1/paper/accounts", json={"name": "Reset"}, headers=trader
    )
    assert response.status_code == 201, response.text
    account = response.json()

    # Resting LIMIT far from market (RUN_WORKERS=False: stays ACCEPTED —
    # the engine never runs to park it OPEN; reset must cancel both).
    await _insert_tick(app, "AAPL", "150")  # buying-power check needs a price
    response = await client.post(
        "/api/v1/orders",
        headers={**trader, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "portfolio_id": account["portfolio_id"],
            "instrument": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 1,
            "limit_price": 1,
        },
    )
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]
    assert response.json()["status"] == "ACCEPTED"

    response = await client.post(
        f"/api/v1/paper/accounts/{account['portfolio_id']}/reset", headers=trader
    )
    assert response.status_code == 200, response.text

    response = await client.get(f"/api/v1/orders/{order_id}", headers=trader)
    assert response.status_code == 200
    order = response.json()
    assert order["status"] == "CANCELLED"
    assert order["reject_reason"] == "PAPER_RESET"

    # The cancel is audited, mirroring the orders module's cancel path.
    async with app.state.sessionmaker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "ORDER_CANCELLED",
                        AuditEvent.resource_id == order_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].payload["reason"] == "PAPER_RESET"


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
    assert any(c["figures"]["total_value"] == 1000000 for c in valuation_cites)

    # Price intent after inserting a tick.
    await _insert_tick(app, "TSLA", "2600")
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "what is the price of TSLA?"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    price_cites = [c for c in body["citations"] if c["kind"] == "price"]
    assert price_cites and price_cites[0]["ref"] == "TSLA"
    assert price_cites[0]["figures"]["price"] == 2600

    # Buy intent -> suggested ticket only, NEVER an order (FR-AI-003 guardrail).
    async def _order_count() -> int:
        async with app.state.sessionmaker() as session:
            return await session.scalar(select(func.count(Order.order_id)))

    before = await _order_count()
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "buy 100 Tesla"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    ticket = body["suggested_ticket"]
    assert ticket is not None
    assert ticket["instrument"] == "TSLA"
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


# ---------------------------------------------------------------------------
# 7 — News & sentiment (dataset news pack, D-14/D-15)
# ---------------------------------------------------------------------------


async def _insert_news(app, ticker="TSLA"):
    from app.core.models import NewsItem, NewsSentiment

    # Anchor at noon UTC of the current day: both items then always share one
    # UTC day regardless of run time — relative-to-now offsets made the
    # sentiment-series assertions fail when run between 00:00-02:00 UTC.
    base = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    async with app.state.sessionmaker() as session:
        items = [
            NewsItem(
                ts=base - timedelta(hours=2),
                title=f"{ticker} rallies on strong guidance",
                topics=["Technology"],
                sentiments=[
                    NewsSentiment(
                        ticker=ticker,
                        relevance=Decimal("0.9"),
                        score=Decimal("0.42"),
                        label="Bullish",
                    )
                ],
            ),
            NewsItem(
                ts=base - timedelta(hours=1),
                title=f"{ticker} slips in quiet trade",
                topics=[],
                sentiments=[
                    NewsSentiment(
                        ticker=ticker,
                        relevance=Decimal("0.6"),
                        score=Decimal("-0.1"),
                        label="Neutral",
                    )
                ],
            ),
        ]
        session.add_all(items)
        await session.commit()


async def test_news_and_sentiment_endpoints(client, app):
    headers = await login(client, CLIENT)
    await _insert_news(app)

    # Instrument-scoped headlines, newest first.
    response = await client.get("/api/v1/instruments/TSLA/news", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["title"] == "TSLA slips in quiet trade"
    assert items[1]["sentiments"][0]["label"] == "Bullish"
    assert items[1]["sentiments"][0]["sentiment_score"] == 0.42

    # Cross-ticker latest feed.
    response = await client.get("/api/v1/news/latest?limit=5", headers=headers)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2

    # Unknown instrument -> 404.
    response = await client.get("/api/v1/instruments/NOPE/news", headers=headers)
    assert response.status_code == 404

    # Daily sentiment series: one day, mean of [0.42, -0.1] = 0.16.
    response = await client.get(
        "/api/v1/instruments/TSLA/sentiment", params={"timeframe": "1W"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "TSLA"
    assert len(body["series"]) == 1
    point = body["series"][0]
    assert point["article_count"] == 2
    assert point["mean_score"] == pytest.approx(0.16, abs=1e-4)
    assert point["label_counts"] == {"Bullish": 1, "Neutral": 1}

    # No news for GOOG -> empty series, not an error.
    response = await client.get(
        "/api/v1/instruments/GOOG/sentiment", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["series"] == []


async def test_assistant_news_intent(client, app):
    headers = await login(client, CLIENT)
    await _insert_news(app)

    # Instrument-scoped: grounded answer + news citations with figures.
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "what is the news on TSLA?"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "TSLA" in body["answer"]
    news_cites = [c for c in body["citations"] if c["kind"] == "news"]
    assert len(news_cites) == 2
    assert news_cites[0]["ref"] == "TSLA"
    assert any("strong guidance" in c["figures"]["title"] for c in news_cites)

    # Market-wide overview without an instrument mention.
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "what is the market sentiment today?"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "sentiment" in body["answer"].lower()
    assert any(c["kind"] == "news" for c in body["citations"])

    # No news for GOOG -> explicit non-fabricating decline (FR-AI-001).
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "any news about GOOG?"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "don't have any news" in body["answer"]
    assert body["citations"] == []


async def test_news_summary_endpoint(client, app):
    headers = await login(client, CLIENT)
    await _insert_news(app)

    response = await client.get(
        "/api/v1/assistant/news-summary",
        params={"symbol": "TSLA"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "TSLA"
    assert body["mock"] is True and body["model"] == "rules-v1"
    assert body["article_count_7d"] == 2
    assert body["sentiment_mean_7d"] == pytest.approx(0.16, abs=1e-4)
    assert body["label_mix"] == {"Bullish": 1, "Neutral": 1}
    assert body["as_of"] is not None
    assert "TSLA" in body["summary"] and "sentiment" in body["summary"]
    assert len(body["headlines"]) == 2
    assert body["headlines"][0]["label"] in ("Bullish", "Neutral")

    # Unknown symbol -> 404.
    response = await client.get(
        "/api/v1/assistant/news-summary",
        params={"symbol": "NOPE"},
        headers=headers,
    )
    assert response.status_code == 404

    # No news for GOOG -> graceful empty summary (not an error).
    response = await client.get(
        "/api/v1/assistant/news-summary",
        params={"symbol": "GOOG"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["article_count_7d"] == 0
    assert body["headlines"] == []
    assert "no news coverage" in body["summary"]

    # auditor@ lacks ASSISTANT_USE -> 403.
    auditor = await login(client, AUDITOR)
    response = await client.get(
        "/api/v1/assistant/news-summary",
        params={"symbol": "TSLA"},
        headers=auditor,
    )
    assert response.status_code == 403
