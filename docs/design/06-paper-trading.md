# 06 — Paper Trading

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.2, paper-trading parts (FR-PTR). Decisions, IDs and requirement text unchanged.

## Purpose

Provide paper trading that follows **the same execution path as real trading** (FR-PTR-002): a paper account is a portfolio type, not a separate engine. This is an architecture driver (DESIGN.md §2): one order pipeline; paper accounts are a portfolio type (`PAPER`).

## SRS requirements covered

- **FR-PTR-002** — paper trading reuses the real order pipeline.
- **AC-008** — every record from a paper portfolio is marked `PAPER` in views, exports and audit payloads.
- **TBD-14** — optional slippage/latency model [P], scoped to paper portfolios.

## Components

No new components — the module reuses unchanged:

- **Order ticket API**, **pre-trade validator**, **execution engine**, **STP worker** — see [02 — Order Execution & STP](02-order-execution-stp.md).
- **Isolation rule** — enforced because the engine and STP worker act only on the order's own `portfolio_id`.
- **Slippage/latency model** [P, TBD-14] — optional; a matching-engine parameter scoped to paper portfolios.

## Flows

Identical to the real-money path: the order state machine and the order → execution → STP settlement sequence in [02 — Order Execution & STP](02-order-execution-stp.md) apply unchanged. Paper orders traverse the same pipeline (`orders.accepted` → `trading.executions` → `stp.lifecycle`); nothing in the flow distinguishes them except the order's `portfolio_id` resolving to a `PAPER` portfolio.

## Data entities used

- `Portfolio` with `type = PAPER` (`CLIENT | HOUSE | PAPER` — see [16 — Data Design](16-data-design.md)).
- `Order`, `Execution`, `SettlementInstruction`, `Position` — via the shared pipeline; every record from a paper portfolio is marked `PAPER` in views, exports and audit payloads (AC-008).

## API endpoints used

- Identical to the real path: `POST /api/v1/orders` with `Idempotency-Key`, and the portfolio/positions read APIs (see [02](02-order-execution-stp.md), [03](03-portfolio-management.md)). `PAPER` marking is surfaced in views, exports and audit payloads (AC-008).

## Error / edge cases

- **Cross-contamination** — prevented structurally: the engine and STP worker act only on the order's own `portfolio_id`, so paper activity cannot touch real positions or cash.
- **Realism tuning** — the optional slippage/latency model [P, TBD-14] is scoped to paper portfolios only; the MVP default keeps the deterministic full-fill behavior (see 02).

## Acceptance criteria mapping

- **AC-008** — `PAPER` marking across views, exports and audit payloads.
- **AC-016** — paper trading is in the UI-driven end-to-end scope (see 19, End-to-end row).
- Pipeline behavior itself is accepted via AC-001/AC-002 (see 02).
