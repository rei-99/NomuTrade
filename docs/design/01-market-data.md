# 01 — Market-Data Service

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.1, updated for the real simulation dataset (D-10…D-14; TBD-06 RESOLVED).

## Purpose

Load the real simulation dataset from `data/` once and replay it internally as a tick stream (INT-04, C-04). The platform is self-contained: nothing else in the system knows the data is simulated. All "current time" for market data is the **simulation clock** (D-10) — never wall-clock `utcnow()`.

## SRS requirements covered

- **INT-04** — simulation dataset loaded once and replayed as ticks.
- **TBD-06** — **RESOLVED**: dataset schema is the three-pack layout under `data/` (below; full reference in `data/README.md`).
- **TBD-16** — **RESOLVED**: USD (D-16).
- **TBD-17** — **RESOLVED**: the dataset's US-equities universe (D-12).
- **C-04** — simulation data only; no live-market connectivity (SRS 1.2).
- **FR-ORD-003 E1** — no tick within the staleness threshold → order submission for the instrument is suspended (measured against the simulation clock, D-10).
- **NFR-AVL-002** — staleness surfaced in the UI (banners).
- **FR-RPT-002 E1** — dataset gaps rendered as-is, never interpolated.
- **D-10 … D-14** (design decisions, DESIGN.md §4.3) — single store + simulation clock, replay loop, dataset instruments, overlap/aggregation rule, news as reference data.

## Dataset (TBD-06 RESOLVED)

Three packs under `data/` (schema reference: `data/README.md`):

- `simulation_historical_data/<SYM>_2026_historical.csv` — daily OHLC, **2026-01-02 → 2026-07-10**, USD. `adjusted_close`/`dividend_amount`/`split_coefficient` are ignored (corporate actions out of MVP scope, SRS 1.2).
- `simulation_price_data_July_1-Aug_30/simulated_<SYM>_live.csv` — **1-minute bars**, market hours 09:30–15:59, **2026-06-30 → 2026-08-29** (~17 k rows/symbol, some deliberate gaps) — the replay source of truth.
- `simulation_news_data_July_1-Aug_30/simulated_<Month>_news_2026.json` — ~154 items/day: `title`, `time_published`, `topics[]`, `ticker_sentiment[]` {`ticker`, `relevance_score`, `ticker_sentiment_score`, label} with Alpha-Vantage-style labels (Bullish … Bearish) — news reference data (D-14; see [16 — Data Design](16-data-design.md) and [05 — Technical Analytics](05-technical-analytics.md)).

Instrument universe (D-12): **AAPL, GOOG, IBM, MSFT, TSLA, UL, WMT** — USD, lot size 1, tick size 0.01.

## Components

- **Loader** (`backend/app/modules/marketdata/loader.py`) — resolves `DATA_DIR` (default `data`, tried against the cwd, its parent and the repo root) and loads the three packs into the platform's own tables: instruments upserted by symbol (the 7 dataset equities — D-12); dailies and minute bars into `PriceTick` (single store, D-10); news flattened into `NewsItem` + `NewsSentiment` (D-14). **Overlap rule (D-13):** dailies and minute bars overlap Jun 30 – Jul 10; dailies are loaded only before 2026-06-30 — the minute bars win from there on. **Idempotent per stage:** instruments upserted by symbol; tick and news loads are skipped when their tables are non-empty, so it is safe to run on every startup. Inserts commit in 5,000-row chunks so the SQLite write lock is released between chunks. Missing data dir → the loader reports failure and the replayer falls back to a generated random-walk feed with the same 7 symbols, so tests/CI never depend on the dataset.
- **Replayer** (`worker.py`) — walks the stored 1-minute bars in dataset-time order at `REPLAY_BARS_PER_SECOND` unique timestamps per second (default **1.0 ≈ 6.5 min per market day**, paced on a wall-clock-aligned grid), publishing all instruments' bars sharing a timestamp as `market.ticks` events that carry the *dataset* timestamp. Each pass starts at the first bar at/after `REPLAY_START` (empty = dataset start; invalid or past-the-end → dataset start with a warning). Tick payload: `{instrument_id, symbol, ts, price, open, high, low, close, volume}` — `price` is the bar close; OHLC/volume are running day aggregates from the registry snapshot. At the end of the dataset it loops to the start (`REPLAY_MODE=loop`, default) or goes idle holding last prices (`hold`). Fallback mode (no `data/`): random-walk ticks at `TICK_INTERVAL_MS` with throttled persistence — the pre-dataset behavior.
- **Simulation clock** (`registry.get_sim_now()`, D-10) — the latest tick timestamp seen across all instruments; a *dataset* timestamp while replaying, never `utcnow()`. Chart ranges, indicator windows, stale-price flags and news visibility are all measured against it. **Future-withholding rule:** while a replay runs, queries withhold data beyond the sim clock (`ts <= sim_now`) — the platform never "knows the future" of the dataset. On each loop the clock is re-based to the replay start so consumers never see past the replay position. The registry (process-local latest-price snapshots + the clock) is warmed from the latest stored ticks on cold start and **reset per app start**.
- **Staleness guard** — a price is stale after 60 s without a tick, measured against the simulation clock (D-10); stale prices are flagged on positions/valuation (FR-PFM-001 E1), and order submission for an instrument with no tick is rejected (FR-ORD-003 E1) with staleness banners in the UI (NFR-AVL-002).
- **Timeframe aggregation (D-13)** — wide chart timeframes (1W…MAX) aggregate ticks into daily candles server-side (works for stored dailies and minute bars alike); the reference day's minute bars fold into one partial candle so the last candle tracks the live feed; 1D serves the intraday minute bars. API shapes are unchanged.

## Flows

No dedicated flow diagram exists in the source design. The service's position in the architecture is shown in the top-level module map (DESIGN.md §4.1: market-data service → tick stream → execution engine), and `market.ticks` consumers are listed in the event-pipeline table (DESIGN.md §4.2): execution engine, valuation projector, alert evaluator, UI push. A tick reaching the execution engine appears in the order→STP sequence diagram in [02 — Order Execution & STP](02-order-execution-stp.md).

## Data entities used

- `Instrument` — the 7 dataset US equities (D-12): symbol, name, asset class EQUITY, currency USD, lot 1, tick 0.01, tradable.
- `PriceTick` — single store for dailies and minute bars (D-10); physical notes in [16 — Data Design](16-data-design.md).
- `NewsItem`, `NewsSentiment` — news reference data (D-14; see 16).
- Registry — process-local latest-price snapshots plus the simulation clock; it stands in for the `px:latest:{symbol}` Redis keys in the single-process dev default (Redis namespaces remain the deployment design, see 16).

## API endpoints used

- `GET /api/v1/instruments` — instrument catalog with latest prices (registry warmed lazily from the DB for instruments not yet seen).
- `GET /api/v1/instruments/{symbol}/prices?timeframe=1D…MAX` — OHLC series; reference day is the sim clock's latest tick date for the instrument (D-10), future ticks withheld while replaying; wide timeframes daily-aggregated (D-13).
- News/sentiment endpoints are served by the analytics module (D-15) — see [05 — Technical Analytics](05-technical-analytics.md).
- Standard conventions (former DESIGN.md §9): base `/api/v1`, JSON, error envelope with `traceId`.

## Error / edge cases

- **Feed stale** — no tick for an instrument within 60 s of sim-clock time → order submission rejected for that instrument (FR-ORD-003 E1); stale flags on positions/valuation and UI banners (FR-PFM-001 E1, NFR-AVL-002).
- **Dataset gaps** — some symbols have fewer minute bars (e.g. UL 16,770 vs TSLA 17,160 rows); gaps are rendered as-is, never interpolated (FR-RPT-002 E1).
- **Dataset exhausted** — `REPLAY_MODE=loop` (default) re-bases the sim clock and restarts from the first bar; `hold` goes idle keeping last prices.
- **Missing data dir** — generated random-walk fallback with the same 7 symbols (D-12); logged as a warning.
- **Cold start / restart** — registry reset per app start, warmed from the latest stored ticks, then re-based to the replay start so consumers never see the dataset's future (D-10).
- **Off-platform news tickers** — sentiments for tickers beyond the 7 (META, NVDA, CRYPTO:BTC, …) are stored but only platform tickers are queryable (see 05, 16).
- **SQLite hardening** — connect timeout 15 s; the outbox relay survives failed batches; the loader commits in chunks to release the write lock.

## Acceptance criteria mapping

- Supports **AC-001 / AC-002** — integration tests run order→STP with replayed ticks (see [19 — Testing Strategy](19-testing-strategy.md), Integration row); the dataset loader and replay are covered directly by backend tests (`test_marketdata_loader.py`, trading tests with real workers).
- Per the testing strategy, each of the 23 SRS acceptance criteria is implemented as at least one automated test or scripted demo step.
