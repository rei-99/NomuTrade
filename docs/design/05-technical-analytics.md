# 05 — Technical Analytics

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.4, analytics half (the reporting half is [04 — Reporting & Charting](04-reporting-charting.md)). Decisions, IDs and requirement text unchanged.

## Purpose

Compute technical indicators (SMA/EMA/RSI/MACD/Bollinger) on demand from the price series and render them as chart overlays, alongside the candlestick charts served by the series API.

## SRS requirements covered

- **FR-ANA-002** — indicator overlays on charts.
- **FR-RPT-002** — underlying OHLC series (owned by [04](04-reporting-charting.md)); E1: gaps marked.
- **D-03** (design decision) — charts via Apache ECharts; indicators are pure, cacheable functions.

## Components

- **Indicator service** — computes SMA/EMA/RSI/MACD/Bollinger on demand from the price series (pure functions, cacheable); overlays rendered client-side by ECharts.

## Flows

No dedicated flow diagram exists in the source design. The data path is: `PriceTick` → series API (04) → indicator service → client-side ECharts overlay. The frontend stack (React + TypeScript, ECharts) is part of the top-level technology selection (DESIGN.md §4.3, D-03).

## Data entities used

- `PriceTick` — sole input, accessed via the series API (see 04); no additional persistence.

## API endpoints used

- Consumes the **series API** from [04 — Reporting & Charting](04-reporting-charting.md) (OHLC with timeframe resampling).
- Indicators are computed on demand from the price series; endpoints (if exposed separately) follow the standard conventions via the module's OpenAPI fragment (base `/api/v1`, JSON, error envelope with `traceId`; former DESIGN.md §9).

## Error / edge cases

- **Gap-marked input series** (FR-RPT-002 E1) are passed through to the client; no interpolation is performed (see 04).
- **Determinism** — indicators are pure functions over the series, so results are cacheable and unit-testable (indicator math is a named unit-test scope, see 19).

## Acceptance criteria mapping

- No dedicated AC ID is cited for FR-ANA in the source design. Indicator math is covered by unit tests (see [19 — Testing Strategy](19-testing-strategy.md), Unit row); per the 23-AC statement there, each SRS acceptance criterion is implemented as at least one automated test or scripted demo step.
- Charts and overlays ship in week 3 of the delivery plan (DESIGN.md §8).
