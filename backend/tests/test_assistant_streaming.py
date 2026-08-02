"""Assistant SSE streaming tests (POST /assistant/query/stream).

Covers `LLMClient.chat_stream` SSE parsing (httpx MockTransport), the
mock-mode chunked stream matching the one-shot answer byte-for-byte, live-LLM
token streaming, and honest degradation when the LLM fails before the first
delta or mid-stream. Live-mode tests wire the seams through configure() and
restore the mock wiring afterwards (same pattern as test_genai_agent).
"""

import json

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.core.models import AssistantInteraction
from app.modules import assistant as assistant_module
from app.modules.assistant.llm import LLMClient
from conftest import login

TRADER = "trader@demo.nomura"

LIVE_CHAT_STATUS = {
    "provider": "openai",
    "chat": "ok",
    "embeddings": "skipped",
    "detail": "live: fake-chat",
}

POSITIONS_QUESTION = "what are my positions?"


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


class FakeStreamLLMClient:
    """Scripted `chat_stream` stand-in: yields `tokens`, optionally raising at
    index `error_after` (0 = fails in setup, before the first token)."""

    def __init__(self, tokens=(), error_after: int | None = None):
        self.tokens = list(tokens)
        self.error_after = error_after
        self.stream_calls = 0

    async def chat_stream(self, messages, *, max_tokens: int = 320):
        self.stream_calls += 1
        for i, token in enumerate(self.tokens):
            if self.error_after is not None and i == self.error_after:
                raise RuntimeError("provider exploded mid-stream")
            yield token
        if self.error_after == len(self.tokens):
            raise RuntimeError("provider exploded")


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, json-data) pairs."""
    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event and data:
            events.append((event, json.loads(data)))
    return events


async def _interactions(app, question: str) -> list[AssistantInteraction]:
    async with app.state.sessionmaker() as session:
        return list(
            (
                await session.execute(
                    select(AssistantInteraction).where(
                        AssistantInteraction.prompt == question
                    )
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# LLMClient.chat_stream SSE parsing
# ---------------------------------------------------------------------------


async def test_chat_stream_parses_sse_deltas():
    sse_body = (
        ": keep-alive comment\n\n"
        "\n"
        'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
        'data: {"choices":[{"delta":{}}]}\n\n'
        "data: {not json}\n\n"
        'data: {"choices":[{"delta":{"content":"!"}}]}\n\n'
        "data: [DONE]\n\n"
        'data: {"choices":[{"delta":{"content":"ignored"}}]}\n\n'
    )
    seen_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payloads.append(json.loads(request.content))
        return httpx.Response(
            200, text=sse_body, headers={"content-type": "text/event-stream"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(
            _settings(
                LLM_PROVIDER="openai",
                LLM_API_URL="https://fake.local/v1",
                LLM_API_KEY="test-key",
            ),
            client=http,
        )
        tokens = [
            token
            async for token in client.chat_stream(
                [{"role": "user", "content": "hi"}]
            )
        ]
    assert tokens == ["Hello", " world", "!"]
    assert seen_payloads[0]["stream"] is True


async def test_chat_stream_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(
            _settings(
                LLM_PROVIDER="openai",
                LLM_API_URL="https://fake.local/v1",
                LLM_API_KEY="test-key",
            ),
            client=http,
        )
        with pytest.raises(httpx.HTTPStatusError):
            async for _token in client.chat_stream([{"role": "user", "content": "hi"}]):
                pass


# ---------------------------------------------------------------------------
# POST /assistant/query/stream — mock mode
# ---------------------------------------------------------------------------


async def test_stream_mock_mode_matches_one_shot(client, app):
    headers = await login(client, TRADER)
    one_shot = await client.post(
        "/api/v1/assistant/query",
        json={"question": POSITIONS_QUESTION},
        headers=headers,
    )
    assert one_shot.status_code == 200, one_shot.text

    before = len(await _interactions(app, POSITIONS_QUESTION))
    response = await client.post(
        "/api/v1/assistant/query/stream",
        json={"question": POSITIONS_QUESTION},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert events[0][0] == "meta"
    meta = events[0][1]
    assert meta["conversation_id"]
    assert meta["intent"] == "positions"
    deltas = [data["text"] for event, data in events if event == "delta"]
    assert deltas
    finals = [data for event, data in events if event == "final"]
    assert len(finals) == 1
    final = finals[0]
    assert "".join(deltas) == final["answer"]
    assert final["answer"] == one_shot.json()["answer"]
    assert final["citations"] == one_shot.json()["citations"]
    assert final["suggested_ticket"] == one_shot.json()["suggested_ticket"]

    # Same trail as the one-shot route: interaction row with the full text.
    interactions = await _interactions(app, POSITIONS_QUESTION)
    assert len(interactions) == before + 1
    assert interactions[-1].response == final["answer"]
    assert interactions[-1].grounded_refs["conversation_id"] == meta["conversation_id"]


async def test_stream_empty_question_rejected(client):
    headers = await login(client, TRADER)
    response = await client.post(
        "/api/v1/assistant/query/stream", json={"question": "  "}, headers=headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# POST /assistant/query/stream — live LLM and degradation paths
# ---------------------------------------------------------------------------


async def test_stream_live_llm_tokens(client, app, restore_assistant):
    headers = await login(client, TRADER)
    fake = FakeStreamLLMClient(
        tokens=["You hold ", "two positions", " worth $100."]
    )
    assistant_module.configure(
        app.state.settings, LIVE_CHAT_STATUS, llm_client=fake
    )

    response = await client.post(
        "/api/v1/assistant/query/stream",
        json={"question": POSITIONS_QUESTION},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    deltas = [data["text"] for event, data in events if event == "delta"]
    assert deltas == fake.tokens
    finals = [data for event, data in events if event == "final"]
    assert finals[0]["answer"] == "".join(fake.tokens)
    assert fake.stream_calls == 1


async def test_stream_llm_setup_failure_falls_back_to_rules(client, app, restore_assistant):
    headers = await login(client, TRADER)
    rule_answer = (
        await client.post(
            "/api/v1/assistant/query",
            json={"question": POSITIONS_QUESTION},
            headers=headers,
        )
    ).json()["answer"]

    fake = FakeStreamLLMClient(error_after=0)  # raises before the first token
    assistant_module.configure(
        app.state.settings, LIVE_CHAT_STATUS, llm_client=fake
    )
    response = await client.post(
        "/api/v1/assistant/query/stream",
        json={"question": POSITIONS_QUESTION},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    # Silent per-call fallback (same as the one-shot route): no error event.
    assert [event for event, _ in events if event == "error"] == []
    deltas = [data["text"] for event, data in events if event == "delta"]
    assert deltas
    finals = [data for event, data in events if event == "final"]
    assert finals[0]["answer"] == rule_answer


async def test_stream_llm_midstream_failure_emits_error_then_rules(
    client, app, restore_assistant
):
    headers = await login(client, TRADER)
    rule_answer = (
        await client.post(
            "/api/v1/assistant/query",
            json={"question": POSITIONS_QUESTION},
            headers=headers,
        )
    ).json()["answer"]

    # Yields one token, then dies: honest degrade, never a hang.
    fake = FakeStreamLLMClient(tokens=["Partial prose"], error_after=1)
    assistant_module.configure(
        app.state.settings, LIVE_CHAT_STATUS, llm_client=fake
    )
    response = await client.post(
        "/api/v1/assistant/query/stream",
        json={"question": POSITIONS_QUESTION},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)

    error_events = [data for event, data in events if event == "error"]
    assert error_events == [{"code": "LLM_STREAM_FAILED"}]
    error_index = next(i for i, (event, _) in enumerate(events) if event == "error")
    # The partial prose went out before the error; the rules answer follows.
    pre_error = [d["text"] for e, d in events[:error_index] if e == "delta"]
    assert pre_error == ["Partial prose"]
    post_error = [d["text"] for e, d in events[error_index:] if e == "delta"]
    assert "".join(post_error) == rule_answer
    finals = [data for event, data in events if event == "final"]
    assert finals[0]["answer"] == rule_answer
    # The persisted trail holds the complete (rules) answer, not the cut-off.
    interactions = await _interactions(app, POSITIONS_QUESTION)
    assert interactions[-1].response == rule_answer
