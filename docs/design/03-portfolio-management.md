# 03 — Portfolio Management & Valuation

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.3, plus the WebSocket channel from §9. Decisions, IDs and requirement text unchanged.

## Purpose

Keep per-portfolio valuations continuously up to date from the tick and execution streams, push deltas to dashboards within 5 s of a tick (NFR-PER-004), and serve positions, KPIs and paged transaction history.

## SRS requirements covered

- **FR-PFM-001** — positions/valuation with stale prices flagged (E1).
- **FR-PFM-002** — valuation refresh driven by ticks.
- **FR-PFM-003** — KPIs: allocation by asset class, top-N, concentration, daily volatility.
- **FR-PFM-004** — paged transaction history (cursor-based).
- **NFR-PER-004** — dashboard refresh within 5 s of a tick (push channel).

## Components

- **Valuation projector** — consumes ticks and executions; maintains per-portfolio aggregates (market value, cash, realized/unrealized P&L, day change) in Redis with PostgreSQL snapshots; pushes deltas over WebSocket so dashboards refresh within 5 s (NFR-PER-004, FR-PFM-002).
- **KPI calculator** — allocation by asset class, top-N, concentration, daily volatility over valuation history (FR-PFM-003); N/A below minimum history.
- **Read APIs** — positions, valuation, paged transaction history (cursor-based, FR-PFM-004). Stale prices flagged per FR-PFM-001 E1.
- **WebSocket channel** — `/ws` (authenticated), topics `portfolio.{id}`, `watchlist`, `notifications` (former DESIGN.md §9); carries the ≤5 s refresh requirement. The `notifications` topic is delivered by [14 — Notifications](14-notifications.md).

## Flows

No dedicated flow diagram exists in the source design. The valuation projector consumes `market.ticks` and `trading.executions` (consumer table, DESIGN.md §4.2) and pushes to the UI over the WebSocket shown in the top-level module map (DESIGN.md §4.1). The dashboard aggregation endpoint that composes valuation into the initial page load is in [04 — Reporting & Charting](04-reporting-charting.md).

## Data entities used

- `Portfolio` (cash_balance, type), `Position` (quantity, avg_cost), `Order`/`Execution` (transaction history), `PriceTick` (latest/history via `px:latest:{symbol}`).
- Redis: `val:{portfolio}` aggregates; PostgreSQL snapshots for durability.

## API endpoints used

- Positions, valuation, paged transaction history (cursor-based, FR-PFM-004) — concrete paths per the module's OpenAPI fragment under the standard conventions (base `/api/v1`, JSON, cursor pagination, error envelope with `traceId`; former DESIGN.md §9).
- WebSocket `/ws` (authenticated): topic `portfolio.{id}` for valuation deltas (NFR-PER-004).

## Error / edge cases

- **Stale prices** — flagged per FR-PFM-001 E1 (staleness detection in [01](01-market-data.md)).
- **Insufficient history** — KPIs return N/A below minimum history (FR-PFM-003).
- **Statelessness** — no in-process session state; aggregates externalized to Redis (NFR-SCL-001, DESIGN.md §2).

## Acceptance criteria mapping

- **AC-006** — UI-driven end-to-end covers login and dashboard (see 19, End-to-end row).
- Performance: dashboard initial load ≤ 3 s via the aggregation endpoint (NFR-PER-003, see 04) and refresh ≤ 5 s (NFR-PER-004) — thresholds tested per 19, Performance row.
