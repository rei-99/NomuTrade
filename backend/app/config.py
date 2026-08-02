"""Application settings (pydantic-settings).

Env vars bind case-insensitively with no prefix, e.g. `DATABASE_URL=... uvicorn ...`.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    DATABASE_URL: str = "sqlite+aiosqlite:///./stp.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    EVENT_BUS: Literal["memory", "redis"] = "memory"
    SESSION_STORE: Literal["memory", "redis"] = "memory"
    DEV_AUTH: bool = True
    RUN_WORKERS: bool = True
    # Real-time WebSocket push (design 22): WS /api/v1/ws + fan-out workers.
    # Kill-switch: false disables the workers and closes the endpoint (4403).
    WS_PUSH_ENABLED: bool = True
    SETTLEMENT_DELAY_SECONDS: float = 5.0
    TICK_INTERVAL_MS: int = 500
    CORS_ORIGINS: str = "http://localhost:5173"
    SECRET_PROVIDER: str = "env"
    ACCESS_TOKEN_TTL_IDLE_SECONDS: int = 1800
    ACCESS_TOKEN_TTL_ABSOLUTE_SECONDS: int = 43200
    # Simulation dataset (INT-04, D-10..D-12). DATA_DIR is resolved against the
    # cwd, its parent, and the repo root; missing dir -> generated fallback feed.
    DATA_DIR: str = "data"
    REPLAY_BARS_PER_SECOND: float = 1.0
    REPLAY_MODE: Literal["loop", "hold"] = "loop"
    # Dataset-time start of each replay pass (ISO date/datetime, e.g.
    # "2026-08-24"); empty = from the first bar. Loops restart here too.
    REPLAY_START: str = ""
    # Order restrictions (A4, design 21): per-order notional cap in the
    # instrument's currency; 0 disables the check.
    ORDER_MAX_NOTIONAL: float = 1_000_000.0
    # News provider (A6, design 21): "dataset" (default) or "alphavantage"
    # (live fetch-on-demand; requires ALPHAVANTAGE_API_KEY).
    NEWS_PROVIDER: Literal["dataset", "alphavantage"] = "dataset"
    ALPHAVANTAGE_API_KEY: str = ""
    # GenAI agent (design 27, D-27.1): one OpenAI-compatible HTTP provider for
    # chat + embeddings. Default "mock" keeps every AI feature rule-based; the
    # startup self-check (D-27.2) falls back per capability when unreachable.
    # Empty EMBEDDING_API_URL/KEY fall back to the LLM_API_URL/KEY connection.
    LLM_PROVIDER: Literal["mock", "openai"] = "mock"
    LLM_API_URL: str = ""  # e.g. https://api.openai.com/v1 (no trailing slash)
    LLM_API_KEY: str = ""
    LLM_CHAT_MODEL: str = "gpt-4o-mini"
    LLM_EMBED_MODEL: str = "text-embedding-3-small"
    EMBEDDING_API_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    LLM_TIMEOUT_SECONDS: float = 15.0
    RAG_TOP_K: int = 4
