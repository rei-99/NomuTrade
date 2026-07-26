"""Shared test fixtures.

Lifespan handling: the `client` fixture runs the app's real lifespan via
asgi-lifespan's LifespanManager (create_all + auto-seed + component wiring),
then serves requests with httpx's ASGITransport (which does not trigger
lifespan itself). RUN_WORKERS=False keeps tests free of background tasks;
the outbox relay is exercised explicitly in test_smoke instead.
"""

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        EVENT_BUS="memory",
        SESSION_STORE="memory",
        RUN_WORKERS=False,
        DEV_AUTH=True,
    )


@pytest.fixture
async def app(settings):
    return create_app(settings)


@pytest.fixture
async def client(app):
    async with LifespanManager(app) as manager:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://t"
        ) as http_client:
            yield http_client


async def login(client: httpx.AsyncClient, email: str) -> dict:
    """Dev-login helper; returns an Authorization header dict."""
    response = await client.post("/api/v1/auth/dev-login", json={"email": email})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}
