"""Product-owner feedback round (design 21): restricted list (A4) + news provider seam (A6)."""

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from app.config import Settings
from app.core.errors import DependencyUnavailable
from app.core.models import AuditEvent
from app.main import create_app
from app.modules.analytics.news_providers import (
    AlphaVantageNewsProvider,
    DatasetNewsProvider,
    get_news_provider,
)
from conftest import login

# ---------------------------------------------------------------------------
# A4 — restricted-instrument admin endpoints
# ---------------------------------------------------------------------------


async def test_restricted_list_crud_and_audit(app, client):
    headers = await login(client, "secadmin@demo.nomura")

    # Empty initially.
    response = await client.get("/api/v1/restricted-instruments", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}

    # Add a restriction.
    response = await client.post(
        "/api/v1/restricted-instruments",
        headers=headers,
        json={"symbol": "TSLA", "reason": "sanctions"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["symbol"] == "TSLA"
    assert body["reason"] == "sanctions"
    assert body["active"] is True
    assert body["created_by"]
    assert body["created_at"].endswith("+00:00")

    # Listed as active.
    response = await client.get("/api/v1/restricted-instruments", headers=headers)
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["symbol"] == "TSLA"
    assert items[0]["active"] is True

    # Re-POST is an upsert: same row, updated reason, still one entry.
    response = await client.post(
        "/api/v1/restricted-instruments",
        headers=headers,
        json={"symbol": "TSLA", "reason": "updated reason"},
    )
    assert response.status_code in (200, 201)
    response = await client.get("/api/v1/restricted-instruments", headers=headers)
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["reason"] == "updated reason"
    assert items[0]["active"] is True

    # DELETE deactivates (never hard-deletes).
    response = await client.delete(
        "/api/v1/restricted-instruments/TSLA", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["active"] is False
    response = await client.get("/api/v1/restricted-instruments", headers=headers)
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["active"] is False

    # Second DELETE conflicts.
    response = await client.delete(
        "/api/v1/restricted-instruments/TSLA", headers=headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_CONFLICT"

    # Both mutations are audited.
    async with app.state.sessionmaker() as session:
        events = (
            (await session.execute(select(AuditEvent).order_by(AuditEvent.seq)))
            .scalars()
            .all()
        )
    event_types = [e.event_type for e in events]
    assert "RESTRICTION_ADDED" in event_types
    assert "RESTRICTION_REMOVED" in event_types
    added = next(e for e in events if e.event_type == "RESTRICTION_ADDED")
    assert added.payload["symbol"] == "TSLA"
    assert "reason" in added.payload


async def test_restricted_unknown_symbol_404(client):
    headers = await login(client, "secadmin@demo.nomura")
    response = await client.post(
        "/api/v1/restricted-instruments",
        headers=headers,
        json={"symbol": "NOPE", "reason": "not a real ticker"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_restricted_delete_missing_404(client):
    headers = await login(client, "secadmin@demo.nomura")
    response = await client.delete(
        "/api/v1/restricted-instruments/TSLA", headers=headers
    )
    assert response.status_code == 404


async def test_restricted_requires_role_manage(client):
    headers = await login(client, "trader@demo.nomura")  # no ROLE_MANAGE
    response = await client.post(
        "/api/v1/restricted-instruments",
        headers=headers,
        json={"symbol": "TSLA", "reason": "sanctions"},
    )
    assert response.status_code == 403
    response = await client.get("/api/v1/restricted-instruments", headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# A6 — news provider seam
# ---------------------------------------------------------------------------


async def test_dataset_provider_is_default(app, client):
    assert isinstance(get_news_provider(app.state.settings), DatasetNewsProvider)
    # The endpoint keeps its envelope through the seam.
    headers = await login(client, "trader@demo.nomura")
    response = await client.get("/api/v1/instruments/TSLA/news", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert isinstance(body["items"], list)


def test_provider_selection_alphavantage():
    settings = Settings(NEWS_PROVIDER="alphavantage", ALPHAVANTAGE_API_KEY="k")
    assert isinstance(get_news_provider(settings), AlphaVantageNewsProvider)


AV_PAYLOAD = {
    "items": "2",
    "sentiment_score_definition": "x <= -0.35: Bearish; ...",
    "relevance_score_definition": "0 < x <= 1",
    "feed": [
        {
            "title": "Tesla Jumps On Record Deliveries",
            "url": "https://example.com/news/1",
            "time_published": "20260701T062006",
            "authors": ["Reporter One"],
            "summary": "...",
            "source": "Example Wire",
            "topics": [
                {"topic": "Technology", "relevance_score": "0.8"},
                {"topic": "Financial Markets", "relevance_score": "0.4"},
            ],
            "overall_sentiment_score": 0.31,
            "overall_sentiment_label": "Somewhat-Bullish",
            "ticker_sentiment": [
                {
                    "ticker": "TSLA",
                    "relevance_score": "0.85",
                    "ticker_sentiment_score": "0.42",
                    "ticker_sentiment_label": "Bullish",
                },
                {
                    "ticker": "AAPL",
                    "relevance_score": "0.10",
                    "ticker_sentiment_score": "-0.05",
                    "ticker_sentiment_label": "Neutral",
                },
            ],
        },
        {
            "title": "EV Market Cools In June",
            "url": "https://example.com/news/2",
            "time_published": "20260630T153000",
            "topics": [],
            "ticker_sentiment": [
                {
                    "ticker": "TSLA",
                    "relevance_score": "0.50",
                    "ticker_sentiment_score": "-0.31",
                    "ticker_sentiment_label": "Somewhat-Bearish",
                }
            ],
        },
    ],
}


async def test_alpha_vantage_mapping(monkeypatch):
    class _Response:
        status_code = 200

        def json(self):
            return AV_PAYLOAD

    async def _fake_get(self, url, params=None, **kwargs):
        assert params["function"] == "NEWS_SENTIMENT"
        assert params["tickers"] == "TSLA"
        assert params["apikey"] == "test-key"
        return _Response()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    provider = AlphaVantageNewsProvider("test-key")
    items = await provider.for_ticker(None, "TSLA", 10)

    assert len(items) == 2
    first = items[0]
    assert first["news_id"] == "https://example.com/news/1"
    assert first["ts"] == "2026-07-01T06:20:06+00:00"
    assert first["title"] == "Tesla Jumps On Record Deliveries"
    assert first["topics"] == ["Technology", "Financial Markets"]
    assert first["sentiments"] == [
        {
            "ticker": "TSLA",
            "relevance_score": 0.85,
            "sentiment_score": 0.42,
            "label": "Bullish",
        },
        {
            "ticker": "AAPL",
            "relevance_score": 0.10,
            "sentiment_score": -0.05,
            "label": "Neutral",
        },
    ]
    assert items[1]["ts"] == "2026-06-30T15:30:00+00:00"
    assert items[1]["sentiments"][0]["label"] == "Somewhat-Bearish"


async def test_alpha_vantage_empty_key_unavailable():
    provider = AlphaVantageNewsProvider("")
    with pytest.raises(DependencyUnavailable):
        await provider.for_ticker(None, "TSLA", 10)


async def test_alpha_vantage_http_error_unavailable(monkeypatch):
    class _Response:
        status_code = 500

        def json(self):
            return {}

    async def _fake_get(self, url, params=None, **kwargs):
        return _Response()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    provider = AlphaVantageNewsProvider("test-key")
    with pytest.raises(DependencyUnavailable):
        await provider.for_ticker(None, "TSLA", 10)


@pytest.fixture
def av_settings(tmp_path):
    return Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        EVENT_BUS="memory",
        SESSION_STORE="memory",
        RUN_WORKERS=False,
        DEV_AUTH=True,
        DATA_DIR=str(tmp_path / "no-such-data-dir"),
        NEWS_PROVIDER="alphavantage",
        ALPHAVANTAGE_API_KEY="",
    )


@pytest.fixture
async def av_client(av_settings):
    app = create_app(av_settings)
    async with LifespanManager(app) as manager:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://t"
        ) as http_client:
            yield http_client


async def test_live_news_unconfigured_returns_503(av_client):
    headers = await login(av_client, "trader@demo.nomura")
    response = await av_client.get("/api/v1/instruments/TSLA/news", headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
