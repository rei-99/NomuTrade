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
    SETTLEMENT_DELAY_SECONDS: float = 5.0
    TICK_INTERVAL_MS: int = 500
    CORS_ORIGINS: str = "http://localhost:5173"
    SECRET_PROVIDER: str = "env"
    ACCESS_TOKEN_TTL_IDLE_SECONDS: int = 1800
    ACCESS_TOKEN_TTL_ABSOLUTE_SECONDS: int = 43200
