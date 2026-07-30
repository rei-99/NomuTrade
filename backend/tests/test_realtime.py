"""Real-time WebSocket push channel tests (design 22).

Live-socket approach: the app fixture's lifespan has already run (workers
included, RUN_WORKERS=True with the fallback feed), then an ephemeral uvicorn
server with lifespan="off" serves the same app instance so the installed
`websockets` client library connects over a real TCP socket.

Note on the 4401 close: a pre-accept close is translated by uvicorn into an
HTTP 403 handshake rejection, so over the wire clients observe
`InvalidStatus(403)`, not the 4401 code. The exact close codes (4401 bad
token, 4403 channel disabled) are asserted server-side by calling the
endpoint directly with a fake WebSocket.
"""

import asyncio
import json
import time
import warnings
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
import websockets
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from app.config import Settings
from app.core.models import Portfolio
from app.core.security import InMemorySessionStore
from app.main import create_app
from app.modules.push import (
    CLOSE_DISABLED,
    CLOSE_UNAUTHORIZED,
    websocket_endpoint,
)
from app.modules.push.manager import ConnectionManager

TRADER = "trader@demo.nomura"
CLIENT = "client@demo.nomura"

# Distinctive symbol so the published tick cannot be confused with the
# fallback feed's own ticks (unknown instrument_id: consumers guard on None).
TEST_TICK = {
    "instrument_id": "test-instrument-ws",
    "symbol": "ZZZ",
    "ts": "2026-07-15T09:30:00+00:00",
    "price": 123.45,
    "open": 120.0,
    "high": 124.0,
    "low": 119.5,
    "close": 123.45,
    "volume": 1000.0,
}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/realtime_test.db",
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
async def live(app):
    """Run the real lifespan, then serve the app on an ephemeral uvicorn socket.

    Yields (http_client, ws_base_url). Lifespan off on the server: startup
    already ran under LifespanManager, so app.state (bus, session store,
    workers) is shared as-is.
    """
    manager = LifespanManager(app, shutdown_timeout=6)
    await manager.__aenter__()
    server = None
    serve_task = None
    try:
        config = uvicorn.Config(
            manager.app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            lifespan="off",
        )
        server = uvicorn.Server(config)
        serve_task = asyncio.create_task(server.serve())
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn test server did not start")
            await asyncio.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://t"
        ) as http_client:
            yield http_client, f"ws://127.0.0.1:{port}"
    finally:
        if server is not None:
            server.should_exit = True
        if serve_task is not None:
            await asyncio.wait_for(serve_task, timeout=10)
        try:
            await manager.__aexit__(None, None, None)
        except TimeoutError:
            warnings.warn("app shutdown exceeded 6s (see test_trading)")


async def _token(client: httpx.AsyncClient, email: str) -> str:
    response = await client.post("/api/v1/auth/dev-login", json={"email": email})
    assert response.status_code == 200, response.text
    return response.json()["token"]


async def _recv_until(ws, predicate, timeout=8.0):
    """Receive messages until predicate(msg) is true; fail bounded."""
    deadline = time.monotonic() + timeout
    seen_types: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for WS message; saw {seen_types}")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        seen_types.append(msg.get("type"))
        if predicate(msg):
            return msg


# ---------------------------------------------------------------------------
# Auth: rejected handshakes (live socket) + exact close codes (unit)
# ---------------------------------------------------------------------------


async def test_ws_rejects_missing_and_bad_token(live):
    _client, ws_base = live
    for url in (f"{ws_base}/api/v1/ws", f"{ws_base}/api/v1/ws?token=bogus"):
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(url):
                pass
        # Pre-accept close surfaces through ASGI as an HTTP 403 rejection.
        assert exc_info.value.response.status_code == 403


class _FakeWebSocket:
    """Duck-typed stand-in for starlette.WebSocket (endpoint unit tests)."""

    def __init__(self, app_state, params):
        self.app = SimpleNamespace(state=app_state)
        self.query_params = params
        self.accepted = False
        self.closed_with: int | None = None

    async def accept(self):
        self.accepted = True

    async def close(self, code: int = 1000):
        self.closed_with = code


def _state(settings: Settings, store) -> SimpleNamespace:
    return SimpleNamespace(settings=settings, session_store=store)


async def test_ws_endpoint_close_codes():
    store = InMemorySessionStore()
    # Missing / bad token -> 4401, never accepted.
    for params in ({}, {"token": "bogus"}):
        ws = _FakeWebSocket(_state(Settings(), store), params)
        await websocket_endpoint(ws)
        assert ws.closed_with == CLOSE_UNAUTHORIZED
        assert not ws.accepted
    # Channel disabled -> 4403 even with a token present.
    ws = _FakeWebSocket(
        _state(Settings(WS_PUSH_ENABLED=False), store), {"token": "whatever"}
    )
    await websocket_endpoint(ws)
    assert ws.closed_with == CLOSE_DISABLED
    assert not ws.accepted


# ---------------------------------------------------------------------------
# Live broadcast / filtering
# ---------------------------------------------------------------------------


async def test_tick_broadcast(live, app):
    client, ws_base = live
    token = await _token(client, TRADER)
    async with websockets.connect(f"{ws_base}/api/v1/ws?token={token}") as ws:
        await asyncio.sleep(0.2)  # let the server register the connection
        await app.state.bus.publish("market.ticks", TEST_TICK)
        msg = await _recv_until(
            ws, lambda m: m.get("type") == "tick" and m["data"].get("symbol") == "ZZZ"
        )
        assert msg["data"]["price"] == TEST_TICK["price"]


async def test_notify_filtered_per_user(live, app):
    client, ws_base = live
    trader_token = await _token(client, TRADER)
    client_token = await _token(client, CLIENT)
    trader_id = (await app.state.session_store.get(trader_token)).user_id
    client_id = (await app.state.session_store.get(client_token)).user_id

    async with websockets.connect(f"{ws_base}/api/v1/ws?token={client_token}") as ws:
        await asyncio.sleep(0.2)
        # A's notification first, then B's; B's socket must only see B's.
        await app.state.bus.publish(
            "notify",
            {"user_id": trader_id, "category": "TEST", "title": "for trader", "body": "x"},
        )
        await asyncio.sleep(0.3)
        await app.state.bus.publish(
            "notify",
            {"user_id": client_id, "category": "TEST", "title": "for client", "body": "y"},
        )
        notifications: list[dict] = []
        msg = await _recv_until(ws, lambda m: m.get("type") == "notification")
        notifications.append(msg)
        # Drain a little longer: a late cross-delivery must also be caught.
        try:
            await _recv_until(
                ws, lambda m: m.get("type") == "notification", timeout=0.6
            )
        except TimeoutError:
            pass
        user_ids = [n["data"]["user_id"] for n in notifications]
        assert client_id in user_ids
        assert trader_id not in user_ids


async def test_execution_delivered_to_portfolio_owner(live, app):
    client, ws_base = live
    trader_token = await _token(client, TRADER)
    client_token = await _token(client, CLIENT)
    async with app.state.sessionmaker() as session:
        portfolio_id = await session.scalar(
            select(Portfolio.portfolio_id).where(Portfolio.name == "Desk Book 1")
        )
    assert portfolio_id is not None  # seeded, owner = trader@demo.nomura
    event = {
        "execution_id": "exec-ws-test-1",
        "order_id": "order-ws-test-1",
        "portfolio_id": portfolio_id,
        "portfolio_type": "HOUSE",
        "instrument_id": "test-instrument-ws",
        "symbol": "ZZZ",
        "side": "BUY",
        "price": 123.45,
        "quantity": 10.0,
        "executed_at": "2026-07-15T09:30:00+00:00",
    }
    async with websockets.connect(f"{ws_base}/api/v1/ws?token={trader_token}") as trader_ws:
        async with websockets.connect(f"{ws_base}/api/v1/ws?token={client_token}") as client_ws:
            await asyncio.sleep(0.2)
            await app.state.bus.publish("trading.executions", event)
            msg = await _recv_until(
                trader_ws, lambda m: m.get("type") == "execution"
            )
            assert msg["data"]["execution_id"] == "exec-ws-test-1"
            # The non-owner connection receives ticks but never this execution.
            with pytest.raises(TimeoutError):
                await _recv_until(
                    client_ws, lambda m: m.get("type") == "execution", timeout=1.5
                )


# ---------------------------------------------------------------------------
# ConnectionManager unit behavior
# ---------------------------------------------------------------------------


class _FakeSendSocket:
    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send_json(self, message: dict):
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(message)


async def test_connection_manager_filtering_and_drop_on_failure():
    mgr = ConnectionManager()
    a, b, dead = _FakeSendSocket(), _FakeSendSocket(), _FakeSendSocket(fail=True)
    mgr.add(a, "user-a")
    mgr.add(b, "user-b")
    mgr.add(dead, "user-b")

    await mgr.broadcast({"type": "tick", "data": {}})
    assert len(a.sent) == 1 and len(b.sent) == 1
    # The failing connection was dropped from the registry.
    assert mgr.count == 2

    await mgr.send_to_user("user-b", {"type": "notification", "data": {}})
    assert len(a.sent) == 1  # untouched: per-user filtering
    assert len(b.sent) == 2
    await mgr.send_to_user("nobody", {"type": "notification", "data": {}})  # no-op

    mgr.remove(a)
    assert mgr.count == 1
    mgr.remove(a)  # idempotent
    assert mgr.count == 1
