# 21 — Product-owner feedback round: analysis & design

Source: `docs/interview_outcome.md` (Rohan Singh, Head of Product Development).
Analysis of each answer → what we build → what stays roadmap. Resolves
TBD-17 (instrument scope) and TBD-18 (order types) with new answers.

## A1 — "Single click" is actually two: click → confirm panel → confirm

Build (frontend only): BUY/SELL in the OrderPanel opens a **confirmation
card** with the full order detail — instrument, side, quantity, order type
(+ limit/stop prices), estimated cost, cash before/after — then **Confirm**
submits (per-click idempotency key minted at confirm, not at first click) or
**Cancel** dismisses. Fill/reject feedback unchanged. Keyboard: Enter
confirms, Esc cancels.

## A2 — Bonds are a must (resolves TBD-17: NO, equities-only is not enough)

The dataset is equities-only; we add a small **bond universe with generated
prices in dataset time**, so replay, charts, orders, positions, valuation and
allocation all work unchanged:

- Instruments (asset_class `BOND`, USD, tick 0.01, quoted as **% of par**):
  `UST10Y` US Treasury 4.25% 2035 · `UST2Y` US Treasury 3.75% 2027 ·
  `AAPL29` Apple Corp 3.40% 2029 · `MSFT31` Microsoft Corp 3.10% 2031.
- Loader stage "bonds": if a bond has no ticks, generate daily + minute
  series over the dataset window (mean-reverting around par 100, ~0.3 %
  daily vol — visibly calmer than equities) — clearly documented as
  generated (no bond data in `data.zip`).
- **Bond cash math** (quoted % of par): `trade_value = qty × price / 100`
  in validation (buying power), STP cash update, and position valuation —
  three backend call sites + the frontend est-cost line.
- Watchlist/tape pick them up automatically; chip shows a `BOND` badge.
- Allocation KPI (FR-PFM-003) becomes real: EQUITY vs BOND split.

## A3 — Order types (resolves TBD-18): MARKET, LIMIT + STOP, STOP_LIMIT

- `Order.stop_price` (new nullable column; additive auto-migration at
  startup — see below). Validation: STOP/STOP_LIMIT require stop_price > 0;
  STOP_LIMIT also limit_price > 0. Same buying-power checks.
- Execution engine: resting stop orders live in the working book; on each
  tick, BUY stop triggers when price ≥ stop, SELL when price ≤ stop; STOP
  then fills as MARKET at the tick, STOP_LIMIT converts to a resting LIMIT
  at limit_price. Order JSON gains `stop_price`; audit/notify carry the
  trigger. Iceberg / TIF / trailing — roadmap only.
- Frontend: order-type pills MARKET/LIMIT/STOP/STOP-LIMIT with conditional
  price inputs (panel + ticket); Orders page shows type/stop.

## A4 — Order restrictions ("yes")

- **Max notional per order**: `ORDER_MAX_NOTIONAL` setting (default
  $1,000,000); validation rejects with `MAX_NOTIONAL_EXCEEDED` (422).
- **Restricted list**: `RestrictedInstrument` table (symbol, reason,
  created_by, created_at) + SecAdmin endpoints (`GET/POST/DELETE
  /restricted-instruments`, gated on existing `ROLE_MANAGE` so no seed
  change is needed on live DBs). Validation rejects with
  `RESTRICTED_INSTRUMENT` (422). Every add/remove is audited.

## A5 — Trader pain research (KPIs)

From the terminal-UI research: the daily-want is **per-position day
change**. Positions endpoint gains `prev_day_open` (sim-day open from the
registry), `day_change` and `day_change_pct` per item; positions table gets
a Day chg column (chips). Portfolio day_change already exists.

## A6 — External news: provider abstraction (roadmap illustration)

`NewsProvider` interface in analytics: `DatasetNewsProvider` (default,
current behavior) and `AlphaVantageNewsProvider` (`NEWS_PROVIDER` +
`ALPHAVANTAGE_API_KEY` env, fetch-on-demand for `/instruments/{s}/news`,
no persistence). The dataset is Alpha-Vantage-shaped, so the seam is
honest; the live path ships disabled-by-default and untested-without-a-key
(candidly marked), demonstrating the phase-2 integration point Rohan asked
to see.

## Deferred (documented, not built)

Iceberg/TIF/trailing stops; bond yield quoting & duration analytics; live
news by default (needs key + review); per-desk limits beyond per-order
notional; second Click-to-Confirm preference toggle (confirm is mandatory
per A1).

## Cross-cutting

- **Additive auto-migration**: `create_all` never alters existing tables,
  so a tiny startup helper adds missing columns (`orders.stop_price`) on
  SQLite/Postgres; new tables (RestrictedInstrument) come via create_all.
- Tests: stop trigger matrix (buy/sell × stop/stop-limit × trigger/no),
  bond math (validation cost, STP cash, valuation), restricted list
  (block + admin CRUD + audit), max notional 422, day-change field,
  confirm-step covered by frontend build + live screenshot.
