"""GenAI-agent tests (design 27): provider self-check + mock fallback, LLM news
summary, RAG help intent, advisory review intent, and the currency fix.

No real network: validate_llm is exercised against httpx.MockTransport, and
the assistant's live seams are wired by calling the module's configure() with
a fabricated llm_status plus an injected fake client. configure() mutates
module-global state, so every live-mode test restores the mock wiring via the
`restore_assistant` fixture.
"""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.core.db import create_all, get_engine, get_sessionmaker
from app.core.models import (
    DocEmbedding,
    Instrument,
    NewsItem,
    NewsSentiment,
    Order,
    PriceTick,
)
from app.core.timeutil import utcnow
from app.modules import assistant as assistant_module
from app.modules.assistant.llm import validate_llm
from app.modules.assistant.rag import build_rag_index, chunk_markdown, retrieve
from conftest import login

TRADER = "trader@demo.nomura"
CLIENT = "client@demo.nomura"
OPS = "ops@demo.nomura"


def _settings(**overrides) -> Settings:
    base = {
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "EVENT_BUS": "memory",
        "SESSION_STORE": "memory",
        "RUN_WORKERS": False,
        "DEV_AUTH": True,
        "DATA_DIR": "/nonexistent",
        "LLM_PROVIDER": "mock",
        # Pin the model name: Settings() also reads a local .env, and tests
        # asserting "live: gpt-4o-mini" must be hermetic against it.
        "LLM_CHAT_MODEL": "gpt-4o-mini",
    }
    base.update(overrides)
    return Settings(**base)


class FakeLLMClient:
    """Stand-in for LLMClient with scripted chat/embed behavior."""

    def __init__(self, reply: str = "LLM-drafted prose.", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.chat_calls = 0

    async def chat(self, messages, *, max_tokens: int = 400) -> str:
        self.chat_calls += 1
        if self.error:
            raise self.error
        return self.reply

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.error:
            raise self.error
        return [[1.0, 0.0, 0.5] for _ in texts]


@pytest.fixture
def restore_assistant():
    """configure() mutates module-global wiring; reset it to mock afterwards."""
    yield
    assistant_module.configure(_settings(), dict(assistant_module.MOCK_STATUS))


async def _user_id(client, headers) -> str:
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    return response.json()["user"]["user_id"]


async def _insert_tick(app, symbol, close):
    async with app.state.sessionmaker() as session:
        instrument_id = (
            await session.execute(
                select(Instrument.instrument_id).where(Instrument.symbol == symbol)
            )
        ).scalar_one()
        price = Decimal(str(close))
        session.add(
            PriceTick(
                instrument_id=instrument_id,
                ts=utcnow(),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("10000"),
            )
        )
        await session.commit()


async def _insert_news(app, ticker="TSLA"):
    # Anchored at noon UTC so both items share one UTC day (see test_experience).
    base = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    async with app.state.sessionmaker() as session:
        session.add_all(
            [
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
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Provider selection + startup self-check (D-27.1/D-27.2)
# ---------------------------------------------------------------------------


async def test_validate_llm_mock_when_unconfigured():
    status = await validate_llm(_settings())
    assert status == {
        "provider": "mock",
        "chat": "skipped",
        "embeddings": "skipped",
        "detail": "mock: not configured",
    }
    # openai provider without url/key is still mock — never probed.
    status = await validate_llm(_settings(LLM_PROVIDER="openai"))
    assert status["provider"] == "mock"
    assert status["detail"] == "mock: not configured"


async def test_validate_llm_openai_live():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            assert request.headers["Authorization"] == "Bearer test-key"
            return httpx.Response(200, json={"data": []})
        if request.url.path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        status = await validate_llm(
            _settings(
                LLM_PROVIDER="openai",
                LLM_API_URL="https://fake.local/v1",
                LLM_API_KEY="test-key",
            ),
            client=client,
        )
    assert status["provider"] == "openai"
    assert status["chat"] == "ok"
    assert status["embeddings"] == "ok"
    assert status["detail"] == "live: gpt-4o-mini"


async def test_validate_llm_openai_down_falls_back_to_mock():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        # Never raises — the failure becomes the down-with-reason status.
        status = await validate_llm(
            _settings(
                LLM_PROVIDER="openai",
                LLM_API_URL="https://fake.local/v1",
                LLM_API_KEY="test-key",
            ),
            client=client,
        )
    assert status["provider"] == "openai"
    assert status["chat"] == "down"
    assert status["embeddings"] == "down"
    assert status["detail"].startswith("down: ")
    assert status["detail"].endswith("— using mock")


# ---------------------------------------------------------------------------
# News summary via LLM with rules fallback (D-27.3)
# ---------------------------------------------------------------------------


_LIVE_CHAT_STATUS = {
    "provider": "openai",
    "chat": "ok",
    "embeddings": "skipped",
    "detail": "live: fake-chat",
}


async def test_news_summary_live_llm_rewords_prose(client, app, restore_assistant):
    headers = await login(client, CLIENT)
    await _insert_news(app)
    fake = FakeLLMClient(
        reply="TSLA coverage leans mildly bullish this week on strong guidance."
    )
    assistant_module.configure(
        app.state.settings, _LIVE_CHAT_STATUS, llm_client=fake
    )

    response = await client.get(
        "/api/v1/assistant/news-summary", params={"symbol": "TSLA"}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mock"] is False
    assert body["model"] == app.state.settings.LLM_CHAT_MODEL
    assert body["summary"] == fake.reply
    assert fake.chat_calls == 1
    # Structured grounding is always computed by the rules, LLM only rewords.
    assert body["article_count_7d"] == 2
    assert body["sentiment_mean_7d"] == pytest.approx(0.16, abs=1e-4)
    assert body["label_mix"] == {"Bullish": 1, "Neutral": 1}


async def test_news_summary_llm_error_keeps_rules(client, app, restore_assistant):
    headers = await login(client, CLIENT)
    await _insert_news(app)
    fake = FakeLLMClient(error=RuntimeError("provider exploded"))
    assistant_module.configure(
        app.state.settings, _LIVE_CHAT_STATUS, llm_client=fake
    )

    response = await client.get(
        "/api/v1/assistant/news-summary", params={"symbol": "TSLA"}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mock"] is True and body["model"] == "rules-v1"
    assert "coverage this week" in body["summary"]
    assert body["article_count_7d"] == 2


# ---------------------------------------------------------------------------
# RAG: chunking, indexing, keyword retrieval, help intent (D-27.4)
# ---------------------------------------------------------------------------


def _write_doc_set(root):
    (root / "README.md").write_text(
        "# STP Platform\n\n" + "Trading platform overview with orders. " * 60 + "\n"
    )
    design = root / "docs" / "design"
    design.mkdir(parents=True)
    (design / "06-access-approvals.md").write_text(
        "# Access requests and approvals\n\n"
        "A user submits an access request for a role. The approver opens the "
        "Approvals tab, reviews the request and can approve or reject it. An "
        "approved access request becomes an active grant immediately.\n"
    )
    (design / "15-admin.md").write_text(
        "# Admin dashboard\n\nGovernance tiles and CSV exports.\n"
    )


async def test_rag_chunking_and_keyword_retrieval(tmp_path):
    _write_doc_set(tmp_path)

    # Chunking sanity: long docs split into multiple bounded, non-empty chunks.
    long_text = "# Title\n\n" + "\n\n".join(
        f"Paragraph {i} " + "lorem ipsum " * 40 for i in range(12)
    )
    chunks = chunk_markdown(long_text, max_chars=700)
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)

    # Keyword retrieval ranks the access-approvals doc first (db unused here).
    hits = await retrieve(
        None,
        _settings(),
        "how do I approve an access request?",
        False,
        root=tmp_path,
    )
    assert hits
    assert hits[0]["source"] == "docs/design/06-access-approvals.md"
    assert hits[0]["score"] > 0
    assert len(hits) <= _settings().RAG_TOP_K


async def test_build_rag_index_embeds_new_and_reuses_unchanged(tmp_path):
    _write_doc_set(tmp_path)
    settings = _settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/rag.db",
        LLM_PROVIDER="openai",
        LLM_API_URL="https://fake.local/v1",
        LLM_API_KEY="test-key",
    )
    engine = get_engine(settings)
    sessionmaker = get_sessionmaker(settings, engine)
    await create_all(engine)
    status = {
        "provider": "openai",
        "chat": "ok",
        "embeddings": "ok",
        "detail": "live: fake-chat",
    }
    fake = FakeLLMClient()

    counts = await build_rag_index(
        sessionmaker, settings, status, llm_client=fake, root=tmp_path
    )
    assert counts["skipped"] is False
    assert counts["embedded"] >= 1
    assert counts["reused"] == 0
    async with sessionmaker() as session:
        rows = (await session.execute(select(DocEmbedding))).scalars().all()
    assert len(rows) == counts["embedded"]
    assert rows[0].embedding and len(rows[0].content_hash) == 64

    # Second pass: unchanged content hashes are reused, never re-embedded.
    counts = await build_rag_index(
        sessionmaker, settings, status, llm_client=fake, root=tmp_path
    )
    assert counts["embedded"] == 0
    assert counts["reused"] == len(rows)

    # Embeddings down -> the build is skipped entirely (keyword-only mode).
    skipped = await build_rag_index(
        sessionmaker, settings, {"embeddings": "down"}, llm_client=fake, root=tmp_path
    )
    assert skipped["skipped"] is True
    assert skipped["embedded"] == 0
    await engine.dispose()


async def test_help_intent_mock_answers_with_doc_citations(client, app):
    headers = await login(client, TRADER)
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "how do I approve an access request?"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    doc_cites = [c for c in body["citations"] if c["kind"] == "doc"]
    assert doc_cites, body
    assert all(c["ref"].endswith(".md") for c in doc_cites)
    assert len(doc_cites) <= app.state.settings.RAG_TOP_K
    assert "documentation" in body["answer"].lower()
    # Mock mode names its sources in the answer, not raw chunk dumps.
    assert doc_cites[0]["ref"] in body["answer"]
    assert body["suggested_ticket"] is None


# ---------------------------------------------------------------------------
# Advisory review intent (D-27.5) + currency fix
# ---------------------------------------------------------------------------


async def test_review_intent_mock_disclaimer_kpis_no_orders(client, app):
    headers = await login(client, TRADER)
    user_id = await _user_id(client, headers)
    await _insert_tick(app, "TSLA", "2600")

    async def order_count() -> int:
        async with app.state.sessionmaker() as session:
            return await session.scalar(
                select(func.count(Order.order_id)).where(Order.created_by == user_id)
            )

    before = await order_count()
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "should I trim my TSLA?"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Advisory review: KPI grounding + fixed disclaimer, never a ticket.
    assert assistant_module.DISCLAIMER_TEXT in body["answer"]
    assert "concentration" in body["answer"]
    assert "total value" in body["answer"]
    assert "TSLA" in body["answer"]
    assert "$2,600.00" in body["answer"]
    assert body["suggested_ticket"] is None
    assert any(c["kind"] == "valuation" for c in body["citations"])
    # Guardrail (FR-AI-003): the assistant created NO orders.
    assert await order_count() == before


async def test_trade_answer_uses_instrument_currency(client, app):
    headers = await login(client, CLIENT)
    await _insert_tick(app, "TSLA", "2600")
    response = await client.post(
        "/api/v1/assistant/query",
        json={"question": "buy 100 Tesla"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    answer = response.json()["answer"]
    assert "$2,600.00" in answer
    assert "¥" not in answer
    assert assistant_module.DISCLAIMER_TEXT in answer


# ---------------------------------------------------------------------------
# Admin health: llm integration tile (D-27.2)
# ---------------------------------------------------------------------------


async def test_admin_health_shows_llm_mock_tile(client, app):
    ops = await login(client, OPS)
    response = await client.get("/api/v1/admin/health", headers=ops)
    assert response.status_code == 200, response.text
    tiles = {i["name"]: i for i in response.json()["integrations"]}
    assert tiles["llm"]["status"] == "UP"
    assert tiles["llm"]["detail"] == "mock: not configured"
