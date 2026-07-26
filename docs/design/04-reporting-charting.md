# 04 — Reporting & Charting

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.4, reporting half (the analytics half is [05 — Technical Analytics](05-technical-analytics.md)). Decisions, IDs and requirement text unchanged.

## Purpose

Serve the dashboard initial load within 3 s (NFR-PER-003), provide OHLC chart series with timeframe resampling, and generate client/ad-hoc reports as PDF/CSV files stored in object storage.

## SRS requirements covered

- **FR-RPT-002** — OHLC price series with timeframe resampling; E1: gaps marked.
- **FR-RPT-003** — report generation, PDF and CSV.
- **FR-RPT-004** — scheduled report delivery (Could; extension point).
- **NFR-PER-003** — dashboard initial load ≤ 3 s.

## Components

- **Dashboard aggregation endpoint** — composes valuation + positions + watchlist + recent transactions in one call to hit the 3 s initial-load target (NFR-PER-003). Valuation data comes from the projector in [03 — Portfolio Management](03-portfolio-management.md).
- **Series API** — serves OHLC from `PriceTick` with timeframe resampling (1D…MAX), gap-marked (FR-RPT-002 E1).
- **Report generator** — synchronous for short periods; longer jobs queued on the event bus with completion notification. HTML template → PDF (WeasyPrint) and CSV via stdlib (D-03); files in object storage, metadata in `Report`. Scheduled delivery (FR-RPT-004, Could) is an extension point on the scheduler.

## Flows

No dedicated flow diagram exists in the source design. Long report jobs flow over the internal event pipeline (DESIGN.md §4.2) and notify completion via [14 — Notifications](14-notifications.md); report files are written to object storage per the deployment diagram in [18 — DevOps & Deployment](18-devops-deployment.md).

## Data entities used

- `PriceTick` (OHLC source; monthly partitioned, BRIN on `ts` — see 16), `Report` (type, format, status, file_ref), `Portfolio`/`Position` (report scope and dashboard composition).
- Object storage (S3-compatible, D-03) holds generated report files.

## API endpoints used

- Dashboard aggregation endpoint — single call returning valuation + positions + watchlist + recent transactions (NFR-PER-003).
- Series API — OHLC with timeframe resampling (1D…MAX).
- Report requests — synchronous for short periods; longer jobs queued with completion notification.
- Concrete paths per the module's OpenAPI fragment under the standard conventions (base `/api/v1`, JSON, error envelope with `traceId`; former DESIGN.md §9).

## Error / edge cases

- **Gaps in price history** — marked, not interpolated (FR-RPT-002 E1); passed through to the client (see 05).
- **Long-running reports** — queued on the event bus; the requester gets a completion notification instead of a blocked request (see 14).
- **Scheduled delivery (FR-RPT-004)** — a Could item: extension point on the scheduler only, attempted after Must/Should items pass (delivery plan, DESIGN.md §8).

## Acceptance criteria mapping

- **AC-006** — UI-driven end-to-end covers the dashboard (see 19, End-to-end row).
- **NFR-PER-003** — 3 s dashboard initial load verified in the performance test pass (see 19, Performance row).
- **FR-RPT-004** — stretch; per the 23-AC statement in 19, every SRS acceptance criterion gets at least one automated test or scripted demo step before a Could item is attempted.
