# 01 — Market-Data Service

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.1. Decisions, IDs and requirement text unchanged.

## Purpose

Replace any live market feed with a one-time load of `data.zip` that is replayed internally as a tick stream (INT-04, C-04). The platform is self-contained: nothing else in the system knows the data is simulated. The service also provides O(1) latest-price reads for validation and valuation, and detects feed staleness.

## SRS requirements covered

- **INT-04** — simulation dataset loaded once and replayed as ticks.
- **C-04** — simulation data only; no live-market connectivity (SRS 1.2).
- **FR-ORD-003 E1** — no tick within the threshold → order submission for the instrument is suspended.
- **NFR-AVL-002** — staleness surfaced in the UI (banners).
- **TBD-06** — dataset schema confirmed week 1 (open item).
- **TBD-17** — instrument scope assumed equities-only for MVP (open item, [P] default).

## Components

- **Loader** — parses `data.zip` (schema confirmed week 1) into `Instrument` reference rows and `PriceTick` history in PostgreSQL; validates lot/tick sizes and tradable flags.
- **Replayer** — publishes ticks to `market.ticks` at a configurable rate (real-time replay, accelerated, or clock-jump for demos). Tracks per-instrument "latest price" in Redis (`px:latest:{symbol}`) for O(1) reads by validation and valuation.
- **Staleness guard** — if no tick for an instrument within the threshold (FR-ORD-003 E1), publishes a `feed.stale` event → order submission for that instrument is suspended (503), UI shows staleness banners (NFR-AVL-002).

## Flows

No dedicated flow diagram exists in the source design. The service's position in the architecture is shown in the top-level module map (DESIGN.md §4.1: market-data service → Redis → execution engine), and `market.ticks` consumers are listed in the event-pipeline table (DESIGN.md §4.2): execution engine, valuation projector, alert evaluator, UI push. A tick reaching the execution engine appears in the order→STP sequence diagram in [02 — Order Execution & STP](02-order-execution-stp.md).

## Data entities used

- `Instrument` — reference data: symbol, asset class, currency, lot/tick sizes, tradable flag.
- `PriceTick` — tick history; monthly partitioned with a BRIN index on `ts` (see [16 — Data Design](16-data-design.md)).
- Redis keys — `px:latest:{symbol}` (namespace conventions in 16).

## API endpoints used

None exposed in the source design — this is an internal service. Other modules read latest prices from Redis; the UI receives staleness state via the dashboard aggregation endpoint and WebSocket push (see [04](04-reporting-charting.md), [03](03-portfolio-management.md)). Should read endpoints be added, they follow the standard conventions (base `/api/v1`, JSON, error envelope with `traceId`) via the module's OpenAPI fragment (former DESIGN.md §9 conventions).

## Error / edge cases

- **Feed stale** — no tick within the threshold → `feed.stale` event; order submission for the instrument returns 503 (FR-ORD-003 E1; enforced by the pre-trade validator, see 02); staleness banners in the UI (NFR-AVL-002).
- **Unknown dataset schema (TBD-06)** — the loader is built after week-1 inspection of `data.zip`; lot/tick sizes and tradable flags are validated at load time.
- **Instrument scope** — equities-only assumed for MVP (TBD-17, [P] default).

## Acceptance criteria mapping

- Supports **AC-001 / AC-002** — integration tests run order→STP with replayed ticks (see [19 — Testing Strategy](19-testing-strategy.md), Integration row).
- No dedicated AC ID is cited for INT-04 in the source design; per the testing strategy, each of the 23 SRS acceptance criteria is implemented as at least one automated test or scripted demo step.
