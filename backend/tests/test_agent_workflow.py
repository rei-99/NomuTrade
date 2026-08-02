"""Agent-workflow tests (design 28): LangGraph state graph + conversation memory.

Pins the owner's reported failure as a regression test, in mock mode AND with
a fake live LLM classifier: "Help me buy 10 stocks of APPL at market price" →
clarification mentioning AAPL → "yes" → suggested_ticket BUY 10 AAPL MARKET
with a confirm-in-UI instruction, zero Order rows. Plus: fuzzy instrument
resolution unit tests, pending-cancel, pronoun resolution from history, and
conversation_id round-trip (memory is shared only within one conversation).

Live-mode tests wire the seams through configure() and restore the mock
wiring afterwards (same pattern as test_genai_agent / test_assistant_streaming).
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.core.models import (
    AssistantInteraction,
    ConversationState,
    Instrument,
    NewsItem,
    NewsSentiment,
    Order,
    PriceTick,
)
from app.core.timeutil import utcnow
from app.modules import assistant as assistant_module
from app.modules.assistant.agent import resolve_instrument_text
from conftest import login

CLIENT = "client@demo.nomura"

OWNER_Q1 = "Help me buy 10 stocks of APPL at market price"


def _settings(**overrides) -> Settings:
    base = {
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "EVENT_BUS": "memory",
        "SESSION_STORE": "memory",
        "RUN_WORKERS": False,
        "DEV_AUTH": True,
        "DATA_DIR": "/nonexistent",
        "LLM_PROVIDER": "mock",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def restore_assistant():
    """configure() mutates module-global wiring; reset it to mock afterwards."""
    yield
    assistant_module.configure(_settings(), dict(assistant_module.MOCK_STATUS))


async def _ask(client, headers, question, conversation_id=None):
    body = {"question": question}
    if conversation_id:
        body["conversation_id"] = conversation_id
    response = await client.post("/api/v1/assistant/query", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _pending(app, conversation_id):
    """The persisted ConversationState.state for one conversation (or None)."""
    async with app.state.sessionmaker() as session:
        row = await session.get(ConversationState, conversation_id)
        return row.state if row is not None else None


async def _order_count(app) -> int:
    async with app.state.sessionmaker() as session:
        return await session.scalar(select(func.count(Order.order_id)))


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
            ]
        )
        await session.commit()


# ---------------------------------------------------------------------------
# The owner's exact 3-turn flow (design 28 driver) — mock mode
# ---------------------------------------------------------------------------


async def test_owner_flow_clarify_then_confirm_mock(client, app):
    headers = await login(client, CLIENT)
    orders_before = await _order_count(app)

    # Turn 1: fuzzy "APPL" → one-time clarification mentioning AAPL, NO ticket.
    turn1 = await _ask(client, headers, OWNER_Q1)
    cid = turn1["conversation_id"]
    assert cid
    assert "AAPL" in turn1["answer"]
    assert "did you mean" in turn1["answer"].lower()
    assert turn1["suggested_ticket"] is None
    assert assistant_module.DECLINE_TEXT not in turn1["answer"]
    # The pending clarification is parked in ConversationState (D-28.2).
    pending = await _pending(app, cid)
    assert pending is not None and "pending_clarification" in pending
    slots = pending["pending_clarification"]
    assert slots["instrument"] == "AAPL"
    assert slots["side"] == "BUY"
    assert slots["quantity"] == 10
    assert slots["order_type"] == "MARKET"

    # Turn 2: "yes" on the SAME conversation → the clarified ticket, with a
    # confirm-in-UI instruction. Still advisory-only: no order is created.
    turn2 = await _ask(client, headers, "yes", cid)
    ticket = turn2["suggested_ticket"]
    assert ticket is not None
    assert ticket["side"] == "BUY"
    assert ticket["instrument"] == "AAPL"
    assert ticket["quantity"] == 10
    assert ticket["order_type"] == "MARKET"
    assert "confirm" in turn2["answer"].lower()
    assert assistant_module.DECLINE_TEXT not in turn2["answer"]
    pending = await _pending(app, cid)
    assert "pending_confirmation" in pending

    # Turn 3: a confirm-phrased follow-up stays coherent (previously DECLINE).
    turn3 = await _ask(client, headers, "I confirm that AAPL(Apple) help me purchase", cid)
    assert assistant_module.DECLINE_TEXT not in turn3["answer"]
    assert "AAPL" in turn3["answer"]
    assert assistant_module.DISCLAIMER_TEXT in turn3["answer"]

    # Guardrail (FR-AI-003): the whole flow created ZERO orders.
    assert await _order_count(app) == orders_before


async def test_clarification_cancel_clears_pending(client, app):
    headers = await login(client, CLIENT)
    turn1 = await _ask(client, headers, OWNER_Q1)
    cid = turn1["conversation_id"]
    assert "pending_clarification" in await _pending(app, cid)

    # "no" → pending cleared, polite cancel, no ticket.
    turn2 = await _ask(client, headers, "no", cid)
    assert turn2["suggested_ticket"] is None
    assert "discard" in turn2["answer"].lower()
    assert assistant_module.DECLINE_TEXT not in turn2["answer"]
    assert await _pending(app, cid) == {}

    # State really is gone: a later "yes" no longer resolves anything.
    turn3 = await _ask(client, headers, "yes", cid)
    assert turn3["suggested_ticket"] is None
    assert turn3["answer"] == assistant_module.DECLINE_TEXT


# ---------------------------------------------------------------------------
# Fuzzy instrument resolution (D-28.3): exact → prefix → name substring → difflib
# ---------------------------------------------------------------------------


async def test_fuzzy_resolution_tiers(client, app):
    await login(client, CLIENT)  # ensure the app is seeded
    async with app.state.sessionmaker() as session:
        # "APPL" → AAPL as an unconfirmed candidate (name-substring tier).
        instrument, confident = await resolve_instrument_text(session, "APPL")
        assert instrument is not None and instrument.symbol == "AAPL"
        assert confident is False
        # Exact symbol mention is confident, no clarification needed.
        instrument, confident = await resolve_instrument_text(session, "buy AAPL")
        assert instrument is not None and instrument.symbol == "AAPL"
        assert confident is True
        # Full-name mention is exact-tier too (existing behaviour).
        instrument, confident = await resolve_instrument_text(session, "buy 100 Tesla")
        assert instrument is not None and instrument.symbol == "TSLA"
        assert confident is True
        # Prefix tier: "TSL" → TSLA candidate.
        instrument, confident = await resolve_instrument_text(session, "TSL")
        assert instrument is not None and instrument.symbol == "TSLA"
        assert confident is False
        # difflib tier (cutoff 0.8): "TSLAA" → TSLA candidate.
        instrument, confident = await resolve_instrument_text(session, "TSLAA")
        assert instrument is not None and instrument.symbol == "TSLA"
        assert confident is False
        # No match at all → (None, False).
        instrument, confident = await resolve_instrument_text(session, "QZZX")
        assert instrument is None and confident is False


# ---------------------------------------------------------------------------
# Pronoun / context resolution from conversation history
# ---------------------------------------------------------------------------


async def test_pronoun_price_inherits_conversation_instrument(client, app):
    headers = await login(client, CLIENT)
    await _insert_tick(app, "TSLA", "2600")
    await _insert_news(app, "TSLA")

    turn1 = await _ask(client, headers, "what is the news on TSLA?")
    cid = turn1["conversation_id"]
    assert "TSLA" in turn1["answer"]

    # Same conversation: "its" inherits TSLA from history.
    turn2 = await _ask(client, headers, "what about its price?", cid)
    assert "TSLA" in turn2["answer"]
    price_cites = [c for c in turn2["citations"] if c["kind"] == "price"]
    assert price_cites and price_cites[0]["ref"] == "TSLA"
    assert price_cites[0]["figures"]["price"] == 2600

    # A different conversation has no such memory: the same question declines.
    other = await _ask(client, headers, "what about its price?")
    assert other["answer"] == assistant_module.DECLINE_TEXT
    assert other["suggested_ticket"] is None


# ---------------------------------------------------------------------------
# Live LLM classifier (strict JSON) — same owner flow through the graph
# ---------------------------------------------------------------------------


class FakeClassifierClient:
    """Scripted chat client: strict-JSON route for router prompts (system
    prompt names the "intent router"), plain prose for everything else."""

    ROUTE_JSON = json.dumps(
        {
            "intent": "trade",
            "instrument": "APPL",
            "side": "BUY",
            "quantity": 10,
            "order_type": "MARKET",
        }
    )

    def __init__(self):
        self.route_calls = 0
        self.prose_calls = 0

    async def chat(self, messages, *, max_tokens: int = 400) -> str:
        if "intent router" in messages[0]["content"]:
            self.route_calls += 1
            return self.ROUTE_JSON
        self.prose_calls += 1
        return "LLM-drafted reply."

    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


_LIVE_CHAT_STATUS = {
    "provider": "openai",
    "chat": "ok",
    "embeddings": "skipped",
    "detail": "live: fake-chat",
}


async def test_live_classifier_owner_flow(client, app, restore_assistant):
    headers = await login(client, CLIENT)
    orders_before = await _order_count(app)
    fake = FakeClassifierClient()
    assistant_module.configure(
        app.state.settings, _LIVE_CHAT_STATUS, llm_client=fake
    )

    # Turn 1: the LLM classifies (trade/APPL/BUY/10/MARKET); fuzzy resolution
    # still turns APPL into an AAPL candidate → clarification, no ticket.
    turn1 = await _ask(client, headers, OWNER_Q1)
    cid = turn1["conversation_id"]
    assert turn1["suggested_ticket"] is None
    assert fake.route_calls == 1
    pending = await _pending(app, cid)
    assert pending["pending_clarification"]["instrument"] == "AAPL"
    assert pending["pending_clarification"]["side"] == "BUY"
    assert pending["pending_clarification"]["quantity"] == 10
    assert pending["pending_clarification"]["order_type"] == "MARKET"

    # Turn 2: "yes" resolves the pending clarification RULE-SIDE (guardrail —
    # no LLM call), producing the same ticket as mock mode.
    turn2 = await _ask(client, headers, "yes", cid)
    ticket = turn2["suggested_ticket"]
    assert ticket is not None
    assert (ticket["side"], ticket["instrument"]) == ("BUY", "AAPL")
    assert ticket["quantity"] == 10
    assert ticket["order_type"] == "MARKET"
    assert fake.route_calls == 1  # pending resolution never calls the LLM
    assert assistant_module.DISCLAIMER_TEXT in turn2["answer"]
    assert await _order_count(app) == orders_before


async def test_live_classifier_invalid_json_falls_back_to_mock(
    client, app, restore_assistant
):
    headers = await login(client, CLIENT)

    class BrokenClassifier(FakeClassifierClient):
        async def chat(self, messages, *, max_tokens: int = 400) -> str:
            if "intent router" in messages[0]["content"]:
                self.route_calls += 1
                return "not json at all"
            # Prose seam: echo the grounding's intent line so the test can
            # see which intent the mock router fell back to.
            return messages[1]["content"].splitlines()[0]

    fake = BrokenClassifier()
    assistant_module.configure(
        app.state.settings, _LIVE_CHAT_STATUS, llm_client=fake
    )
    body = await _ask(client, headers, "what are my positions?")
    # Mock router answered the data question despite the broken classifier.
    assert "positions" in body["answer"]
    assert fake.route_calls == 1


# ---------------------------------------------------------------------------
# conversation_id round-trip: memory is shared only within one conversation
# ---------------------------------------------------------------------------


async def test_memory_requires_matching_conversation_id(client, app):
    headers = await login(client, CLIENT)
    turn1 = await _ask(client, headers, OWNER_Q1)
    cid = turn1["conversation_id"]

    # "yes" on a DIFFERENT conversation id: no pending state there → no ticket.
    other = await _ask(client, headers, "yes", "different-conversation")
    assert other["suggested_ticket"] is None
    assert other["answer"] == assistant_module.DECLINE_TEXT

    # "yes" on the ORIGINAL id: the pending clarification resolves.
    turn2 = await _ask(client, headers, "yes", cid)
    assert turn2["suggested_ticket"] is not None
    assert turn2["suggested_ticket"]["instrument"] == "AAPL"

    # Turn history is rebuilt from the persisted interaction rows (D-28.2).
    async with app.state.sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(AssistantInteraction).order_by(
                        AssistantInteraction.created_at
                    )
                )
            )
            .scalars()
            .all()
        )
    by_cid = [
        row
        for row in rows
        if (row.grounded_refs or {}).get("conversation_id") == cid
    ]
    assert [row.prompt for row in by_cid] == [OWNER_Q1, "yes"]
