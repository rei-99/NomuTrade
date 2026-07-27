# 05 — Technical Analytics

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.4, analytics half (the reporting half is [04 — Reporting & Charting](04-reporting-charting.md)), updated for the dataset news/sentiment features (D-13…D-15).

## Purpose

Compute technical indicators (SMA/EMA/RSI/MACD/Bollinger) on demand from the price series and render them as chart overlays, alongside the candlestick charts served by the series API — and serve the dataset's market **news and per-ticker sentiment** to the UI and the assistant (D-14/D-15).

## SRS requirements covered

- **FR-ANA-002** — indicator overlays on charts.
- **FR-RPT-002** — underlying OHLC series (owned by [04](04-reporting-charting.md)); E1: gaps marked.
- **D-03** (design decision) — charts via Apache ECharts; indicators are pure, cacheable functions.
- **D-13** (design decision) — wide timeframes use server-side daily-aggregated closes; API shapes unchanged.
- **D-14 / D-15** (design decisions) — news as sim-clock-capped reference data; analytics news/sentiment endpoints.

## Components

- **Indicator service** — computes SMA/EMA/RSI/MACD/Bollinger on demand from `PriceTick` closes (pure functions, cacheable); overlays rendered client-side by ECharts. On wide timeframes it runs over **daily-aggregated closes** (D-13); 1D uses the intraday minute bars. All windows are relative to the latest tick at or before the **simulation clock** (D-10) — never `utcnow()` — and data beyond the sim clock is withheld while a replay runs.
- **News & sentiment endpoints** (D-14/D-15) — read-only queries over the news reference tables (`NewsItem`/`NewsSentiment`, see [16](16-data-design.md)): per-instrument headlines, latest market-wide headlines, and a daily sentiment series for charting. News visibility is capped at the simulation clock.
- **Price alerts** — alert rules evaluated by the alert-evaluator worker against the `market.ticks` stream (pre-existing module scope; SRS Could item).

## Flows

No dedicated flow diagram exists in the source design. Price path: `PriceTick` → series API / indicator service → client-side ECharts overlay. News path: dataset news pack → loader (see [01](01-market-data.md)) → `NewsItem`/`NewsSentiment` → the endpoints below → frontend **Charts → News tab** and **sentiment panel**, plus the **Dashboard market-news widget** (D-15); the assistant consumes the same tables through its news intent (see [07](07-genai-assistant.md)).

## Data entities used

- `PriceTick` — indicator input; no additional persistence.
- `NewsItem`, `NewsSentiment` — news reference data (D-14; loaded once by the dataset loader, never replayed; see 16).

## API endpoints used

- `GET /api/v1/instruments/{symbol}/indicators?timeframe=…&indicators=SMA,EMA,RSI,MACD,BB` — on-demand indicator series (`{ts, value}` points; MACD as `{ts, macd, signal, histogram}`, Bollinger as `{ts, upper, middle, lower}`). Wide timeframes use daily-aggregated closes (D-13); windows reference the sim clock (D-10). Insufficient data yields an empty series for that indicator, not an error.
- `GET /api/v1/instruments/{symbol}/news?limit=` — latest headlines mentioning the instrument, newest first: `{news_id, ts, title, topics[], sentiments[{ticker, relevance_score, sentiment_score, label}]}` (D-14/D-15).
- `GET /api/v1/news/latest?limit=` — latest headlines mentioning any platform instrument (Dashboard market-news widget).
- `GET /api/v1/instruments/{symbol}/sentiment?timeframe=…` — daily sentiment series for charting: `[{date, mean_score, article_count, label_counts}]`. The window reference is the latest news timestamp for the ticker — news is reference data with its own clock, like prices (D-10/D-14) — and is sim-clock capped.
- Price series come from `GET /api/v1/instruments/{symbol}/prices` (see [01 — Market-Data Service](01-market-data.md); reporting half in [04](04-reporting-charting.md)).
- Standard conventions (former DESIGN.md §9): base `/api/v1`, JSON, error envelope with `traceId`.

## Error / edge cases

- **Gap-marked input series** (FR-RPT-002 E1) are passed through to the client; no interpolation is performed (dataset gaps are rendered as-is).
- **Insufficient data** — an indicator with too few points returns an empty series, not an error; a sentiment window with no news returns an empty series.
- **News beyond the sim clock** — withheld while a replay runs (D-10/D-14): the platform must not know the dataset's future.
- **Off-platform tickers** — news mentions tickers beyond the platform's 7; those sentiments are stored but not queryable (see 16).
- **Determinism** — indicators are pure functions over the series, so results are cacheable and unit-testable (indicator math is a named unit-test scope, see 19).

## Acceptance criteria mapping

- No dedicated AC ID is cited for FR-ANA in the source design. Indicator math is covered by unit tests (see [19 — Testing Strategy](19-testing-strategy.md), Unit row); per the 23-AC statement there, each SRS acceptance criterion is implemented as at least one automated test or scripted demo step.
- Charts and overlays ship in week 3 of the delivery plan (DESIGN.md §8); the news/sentiment endpoints are exercised by the backend experience tests.
