"""News provider seam (A6, design 21): dataset-backed default, live fetch on demand.

`GET /instruments/{symbol}/news` resolves its data source through
`get_news_provider(settings)` instead of querying the news tables directly:

- `DatasetNewsProvider` (default, `NEWS_PROVIDER=dataset`): the historical
  behavior — headlines from the simulation news pack, capped at the
  simulation clock while a replay runs (D-14: no future knowledge).
- `AlphaVantageNewsProvider` (`NEWS_PROVIDER=alphavantage`): fetch-on-demand
  from the Alpha Vantage NEWS_SENTIMENT endpoint; nothing is persisted and
  the sim-clock cap does not apply (live news is real-time by nature).
  Requires `ALPHAVANTAGE_API_KEY`; unconfigured or failed calls surface as
  DependencyUnavailable (503 envelope).

Both providers return the same item shape (the dataset news pack is itself
Alpha-Vantage-shaped), so the frontend contract is unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.core.errors import DependencyUnavailable
from app.core.models import NewsItem, NewsSentiment
from app.core.timeutil import as_utc
from app.modules.marketdata.registry import get_sim_now

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
REQUEST_TIMEOUT_SECONDS = 10.0


def _dataset_item_json(item: NewsItem) -> dict:
    """Wire shape of one news item — identical to the pre-seam endpoint JSON."""
    return {
        "news_id": item.news_id,
        "ts": as_utc(item.ts).isoformat(),
        "title": item.title,
        "topics": list(item.topics or []),
        "sentiments": [
            {
                "ticker": s.ticker,
                "relevance_score": float(s.relevance) if s.relevance is not None else None,
                "sentiment_score": float(s.score) if s.score is not None else None,
                "label": s.label,
            }
            for s in item.sentiments
        ],
    }


class DatasetNewsProvider:
    """Headlines from the simulation news pack (current behavior, D-14/D-15)."""

    async def for_ticker(self, db: AsyncSession, symbol: str, limit: int) -> list[dict]:
        sim_now = get_sim_now()
        clock_filter = [NewsItem.ts <= sim_now] if sim_now is not None else []
        items = (
            (
                await db.execute(
                    select(NewsItem)
                    .join(NewsSentiment, NewsSentiment.news_id == NewsItem.news_id)
                    .where(NewsSentiment.ticker == symbol)
                    .where(*clock_filter)
                    .options(selectinload(NewsItem.sentiments))
                    .order_by(NewsItem.ts.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .unique()
            .all()
        )
        return [_dataset_item_json(i) for i in items]


def _float_or_none(raw) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _av_ts_to_iso(raw: str) -> str:
    """Alpha Vantage "20260701T062006" -> ISO-UTC "2026-07-01T06:20:06+00:00"."""
    return (
        datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).isoformat()
    )


class AlphaVantageNewsProvider:
    """Fetch-on-demand from Alpha Vantage NEWS_SENTIMENT; no persistence."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def for_ticker(self, db: AsyncSession, symbol: str, limit: int) -> list[dict]:
        if not self._api_key:
            raise DependencyUnavailable("live news provider not configured")
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    ALPHAVANTAGE_URL,
                    params={
                        "function": "NEWS_SENTIMENT",
                        "tickers": symbol,
                        "limit": limit,
                        "apikey": self._api_key,
                    },
                )
        except httpx.HTTPError as exc:  # timeouts, connect errors, ...
            raise DependencyUnavailable(f"live news request failed: {exc}") from exc
        if response.status_code != 200:
            raise DependencyUnavailable(
                f"live news provider returned HTTP {response.status_code}"
            )
        try:
            feed = response.json().get("feed", [])
        except ValueError as exc:
            raise DependencyUnavailable("live news provider returned invalid JSON") from exc
        return [self._map_item(entry) for entry in feed[:limit]]

    @staticmethod
    def _map_item(entry: dict) -> dict:
        return {
            "news_id": entry.get("url"),
            "ts": _av_ts_to_iso(entry.get("time_published", "")),
            "title": entry.get("title"),
            "topics": [t.get("topic") for t in entry.get("topics", [])],
            "sentiments": [
                {
                    "ticker": ts.get("ticker"),
                    "relevance_score": _float_or_none(ts.get("relevance_score")),
                    "sentiment_score": _float_or_none(ts.get("ticker_sentiment_score")),
                    "label": ts.get("ticker_sentiment_label"),
                }
                for ts in entry.get("ticker_sentiment", [])
            ],
        }


def get_news_provider(settings: Settings):
    """Resolve the configured news provider (A6)."""
    if settings.NEWS_PROVIDER == "alphavantage":
        return AlphaVantageNewsProvider(settings.ALPHAVANTAGE_API_KEY)
    return DatasetNewsProvider()
