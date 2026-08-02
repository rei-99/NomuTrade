"""LLM provider seam (design 27, D-27.1/D-27.2): OpenAI-compatible HTTP.

One provider kind — `POST {url}/chat/completions` and `POST {url}/embeddings`
— covers OpenAI, Azure OpenAI (deployment URL), DeepSeek, Qwen, Ollama and
vLLM. `LLMClient` holds only configuration plus an optional injected
`httpx.AsyncClient` (tests); call sites that do not inject one get a fresh
short-lived client per call, so there is no process-global connection state.

`validate_llm(settings)` is the startup self-check: it probes the chat and
embedding endpoints with 5 s timeouts and returns the `llm_status` dict that
lands on `app.state.llm_status` (and drives the admin health tile). It never
raises — any failure means "mock mode for that capability", never a failed
boot.
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# Startup self-check timeout (D-27.2); per-request calls use LLM_TIMEOUT_SECONDS.
VALIDATE_TIMEOUT_SECONDS = 5.0

MOCK_STATUS = {
    "provider": "mock",
    "chat": "skipped",
    "embeddings": "skipped",
    "detail": "mock: not configured",
}


def resolve_embed_connection(settings: Settings) -> tuple[str, str]:
    """Embedding endpoint connection, falling back to the chat one (D-27.1)."""
    url = (settings.EMBEDDING_API_URL or settings.LLM_API_URL).rstrip("/")
    key = settings.EMBEDDING_API_KEY or settings.LLM_API_KEY
    return url, key


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


def _reason(exc: Exception) -> str:
    """Short, log-safe failure reason for the status detail string."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    text = str(exc).strip() or type(exc).__name__
    return text[:100]


class LLMClient:
    """OpenAI-compatible chat + embeddings client (config holder, not a pool)."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self.chat_url = settings.LLM_API_URL.rstrip("/")
        self.chat_key = settings.LLM_API_KEY
        self.chat_model = settings.LLM_CHAT_MODEL
        self.embed_model = settings.LLM_EMBED_MODEL
        self.embed_url, self.embed_key = resolve_embed_connection(settings)
        self.timeout = settings.LLM_TIMEOUT_SECONDS
        self._client = client

    async def _post(self, url: str, key: str, payload: dict) -> httpx.Response:
        if self._client is not None:
            response = await self._client.post(
                url, json=payload, headers=_headers(key)
            )
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url, json=payload, headers=_headers(key)
                )
        response.raise_for_status()
        return response

    async def chat(self, messages: list[dict], *, max_tokens: int = 400) -> str:
        """One chat completion; returns the assistant message text (stripped)."""
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        response = await self._post(
            f"{self.chat_url}/chat/completions", self.chat_key, payload
        )
        data = response.json()
        return (data["choices"][0]["message"]["content"] or "").strip()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embedding vectors for `texts`, aligned with the input order."""
        payload = {"model": self.embed_model, "input": texts}
        response = await self._post(
            f"{self.embed_url}/embeddings", self.embed_key, payload
        )
        data = response.json()
        return [item["embedding"] for item in data["data"]]


async def validate_llm(
    settings: Settings, *, client: httpx.AsyncClient | None = None
) -> dict:
    """Startup self-check (D-27.2). Never raises — see module docstring.

    Status shape:
      mock:    {"provider": "mock", "chat": "skipped",
                "embeddings": "skipped", "detail": "mock: not configured"}
      openai:  {"provider": "openai", "chat": "ok"|"down",
                "embeddings": "ok"|"down"|"skipped",
                "detail": "live: <model>" | "down: <reason> — using mock"}
    """
    if (
        settings.LLM_PROVIDER != "openai"
        or not settings.LLM_API_URL
        or not settings.LLM_API_KEY
    ):
        return dict(MOCK_STATUS)

    chat_url = settings.LLM_API_URL.rstrip("/")
    embed_url, embed_key = resolve_embed_connection(settings)
    status = {
        "provider": "openai",
        "chat": "down",
        "embeddings": "skipped",
        "detail": "",
    }
    chat_reason = "unreachable"

    async def _check(http: httpx.AsyncClient) -> None:
        nonlocal chat_reason
        try:
            response = await http.get(
                f"{chat_url}/models", headers=_headers(settings.LLM_API_KEY)
            )
            response.raise_for_status()
            status["chat"] = "ok"
        except Exception as exc:  # noqa: BLE001 — fallback by design
            chat_reason = _reason(exc)
            logger.warning("LLM chat endpoint self-check failed: %s", chat_reason)
        if embed_url and embed_key:
            try:
                response = await http.post(
                    f"{embed_url}/embeddings",
                    headers=_headers(embed_key),
                    json={"model": settings.LLM_EMBED_MODEL, "input": ["probe"]},
                )
                response.raise_for_status()
                status["embeddings"] = "ok"
            except Exception as exc:  # noqa: BLE001 — fallback by design
                status["embeddings"] = "down"
                logger.warning("LLM embedding endpoint self-check failed: %s", _reason(exc))

    if client is not None:
        await _check(client)
    else:
        async with httpx.AsyncClient(timeout=VALIDATE_TIMEOUT_SECONDS) as http:
            await _check(http)

    if status["chat"] == "ok":
        status["detail"] = f"live: {settings.LLM_CHAT_MODEL}"
    else:
        status["detail"] = f"down: {chat_reason} — using mock"
    return status
