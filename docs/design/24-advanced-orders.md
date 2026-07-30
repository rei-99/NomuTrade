# 24 — Advanced Orders: Time-in-Force, Trailing Stop, Bond Analytics

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: the deferred list of [21 — Product-owner feedback](21-product-owner-feedback.md) §Deferred ("Iceberg/TIF/trailing stops; bond yield quoting & duration analytics"). Extends the order model of [02 — Order Execution & STP](02-order-execution-stp.md). Iceberg stays roadmap.

## Purpose

Add the remaining order behaviors the product owner deferred in design 21
without touching existing MARKET/LIMIT/STOP/STOP_LIMIT semantics:

- **Time-in-force (TIF)** on every order: GTC (today's behavior), DAY
  (expires at the end of the simulation day), IOC (fill immediately or
  cancel, never rests).
- **TRAILING_STOP** order type: a stop whose trigger trails the extreme
  price seen since acceptance by an absolute amount or a percentage.
- **Bond analytics**: structured coupon/maturity fields on bond instruments
  and a yield/duration endpoint, so bonds are quotable and analyzable as
  fixed-income instruments rather than just "% of par" price series.

## SRS requirements covered

- **TBD-18** order types — extends the design-21 resolution (MARKET, LIMIT,
  STOP, STOP_LIMIT) with time-in-force and TRAILING_STOP. Iceberg stays
  roadmap per the product-owner interview.
- **FR-ORD-001/002/003** — the new fields flow through the same ticket,
  validation rule chain and idempotent submission; no new pipeline.
- **FR-ORD-004 / AC-003** — engine behaviors (IOC cancel, DAY expiry,
  trailing trigger) are audited and notified through the same choke points
  (`write_audit`, `write_outbox`) as STOP_TRIGGERED (design 21 §A3).
- **D-10** — DAY expiry and years-to-maturity are computed against the
  simulation clock, never wall-clock time.

## Decisions

### D-24.1 — Time-in-force (`orders.time_in_force`, `orders.expire_after`)

- `orders.time_in_force` String(10), additive column, **default GTC** —
  existing rows and omitted-field submissions behave exactly as today
  (additive DDL backfills `'GTC'`).
- **GTC**: rest until filled/cancelled (current behavior, unchanged).
- **DAY**: at accept time the API sets additive nullable
  `orders.expire_after` = end (23:59:59.999999 UTC) of the **simulation**
  day (`get_sim_now()`; wall clock when no replay has started). The engine
  cancels the order — status CANCELLED, `ORDER_EXPIRED` audit + notify —
  when a tick with `ts > expire_after` arrives. Sim-time per D-10: in
  dataset mode a DAY order lives one replayed market day.
- **IOC**: never rests. In `_fill_order`, any branch that would return
  "working" instead cancels the order when TIF is IOC: status CANCELLED,
  `reject_reason = IOC_UNFILLED`, `ORDER_CANCELLED` audit with
  `reason: IOC_UNFILLED` + notify. Marketable IOC orders fill normally.
  IOC applies uniformly: LIMIT must be marketable, STOP/STOP_LIMIT must be
  triggered, TRAILING_STOP must be triggered, MARKET always fills.
- TIF is fixed at submission (not amendable — amending urgency is a cancel
  + re-ticket in every real OMS).
- TIF enumeration is schema-enforced (`Literal["DAY","GTC","IOC"]`, default
  `"GTC"`): garbage values get 400 VALIDATION_ERROR; cross-field business
  rules stay 422 via the rule chain.

### D-24.2 — TRAILING_STOP (new OrderType)

- New `OrderType.TRAILING_STOP`; `orders.order_type` widened to String(20)
  ("TRAILING_STOP" is 13 chars — Postgres widen via a startup ALTER; SQLite
  does not enforce varchar length).
- Additive nullable columns: `trail_amount`, `trail_pct` Numeric(24,8),
  `trail_reference` Numeric(24,8) (the water-mark).
- Validation (422 via the rule chain): exactly one of `trail_amount` /
  `trail_pct` required, > 0 (`TRAIL_PARAM_REQUIRED` when neither/non-positive,
  `TRAIL_PARAM_CONFLICT` when both); `stop_price`/`limit_price` are forbidden
  (`PRICE_FIELD_FORBIDDEN`); trail params on any other order type are
  forbidden (`TRAIL_PARAM_FORBIDDEN`). `trail_pct` is percentage points
  (5 = 5 %).
- Engine: TRAILING_STOP rests in the working book (`_RESTING_TYPES`). Per
  tick: **update the reference first, then check the trigger** — SELL
  reference = highest price seen (trigger when price ≤ reference − amount,
  or ≤ reference × (1 − pct/100)); BUY mirror (lowest, + amount /
  × (1 + pct/100)). The reference is initialized from the first tick seen
  after acceptance and **persisted on every move**, so a book rebuild after
  restart keeps the trailing state.
- On trigger: fill as MARKET at the tick price (order_type stays
  TRAILING_STOP), `STOP_TRIGGERED` audit whose payload carries
  `trail_amount`/`trail_pct`/`trail_reference`/`tick_price`, plus a notify —
  mirroring `_convert_stop_limit`'s pattern (design 21 §A3).
- Amendable: PATCH accepts `trail_amount`/`trail_pct`; providing one
  replaces the trail and clears the other (exactly-one invariant). The
  water-mark `trail_reference` is deliberately kept across amendments —
  amending the trail does not reset the extreme.

### D-24.3 — Bond analytics (`instruments.coupon_rate`, `instruments.maturity_date`)

- Additive nullable columns: `coupon_rate` Numeric(24,8) (annual coupon,
  % of par — 4.25 means 4.25 %), `maturity_date` DateTime(tz) (midnight UTC).
- Populated from the bond names' coupon/maturity ("US Treasury 4.25% 2035")
  as structured data in the loader's `BOND_INSTRUMENTS` and — kept in sync
  per the seed comment — the seed's `INSTRUMENTS`. The loader's boot upsert
  backfills the two fields on existing rows (`None` only — never overwrites),
  which the once-only seed cannot reach. Representative day-precision
  maturity dates (names carry only the year): UST10Y 2035-08-15,
  UST2Y 2027-06-15, AAPL29 2029-03-15, MSFT31 2031-09-15.
- New endpoint `GET /instruments/{symbol}/bond-analytics?yield=<optional>`
  (analytics module, `get_current_user` like indicators):
  - `coupon_rate`, `maturity_date`, `years_to_maturity` (sim clock,
    ACT/365.25), `latest_price`;
  - `ytm` (percent): annual coupons, % of par, solved by bisection on
    r ∈ [−99 %, +100 %] (price is monotone decreasing in r);
  - `modified_duration` = Macaulay / (1 + r);
  - `implied_price` — present only when `yield` (percent) is supplied.
  - 404 for non-bonds (and for bonds lacking structured fields).
- **Cashflow convention (deliberate simplification)**: clean price, no
  accrued interest, `n = max(1, round(years_to_maturity))` annual payments
  at t = 1…n years from today, final payment coupon + 100. With integer
  periods a par-priced bond's YTM is *exactly* its coupon, and
  implied price at yield = coupon is exactly 100 — teachable,
  hand-checkable numbers for a training platform.

## Components

- **orders/api.py** — ticket schema gains `time_in_force`, `trail_amount`,
  `trail_pct`; DAY sets `expire_after` from the sim clock at acceptance;
  amend gains trail params; `order_json` exposes the new fields.
- **orders/validation.py** — rule chain gains the D-24.2 checks (codes
  above); unchanged for the four existing types.
- **orders/workers.py** — execution engine: DAY-expiry check on ticks,
  IOC cancel-or-rest, TRAILING_STOP reference/trigger/fill,
  `_RESTING_TYPES` + TRAILING_STOP. STP/settlement workers untouched.
- **analytics/bonds.py** — pure pricing math (schedule, price from yield,
  YTM bisection, Macaulay/modified duration); route registered on the
  analytics router.
- **marketdata/loader.py + seed.py** — structured bond coupon/maturity,
  upsert backfill on boot.
- **Frontend** — TIF selector + trail inputs in OrderPanel/OrderTicket
  (client-side validation mirrors the server), TIF/trail columns in the
  Orders blotter, compact bond-analytics card in the trading workspace rail.

## Flows

- **IOC LIMIT beyond market**: submit → `orders.accepted` → engine: not
  marketable + IOC → CANCELLED (`ORDER_CANCELLED` audit, reason
  IOC_UNFILLED) → never parked in the book.
- **DAY order**: submit (TIF=DAY, `expire_after` = sim end-of-day) → rests
  OPEN → first tick of the next sim day → CANCELLED (`ORDER_EXPIRED` audit
  + notify).
- **Trailing SELL**: accept → tick 100 sets reference 100 → ticks 101, 103
  raise the reference (persisted) → tick 102 with trail_amount 1.5:
  102 > 103 − 1.5 → no trigger → tick 101.5 ≤ 101.5 → `STOP_TRIGGERED`
  audit + notify → FILLED as MARKET at 101.5.
- **Bond analytics**: `GET /instruments/UST10Y/bond-analytics?yield=4.0` →
  coupon/maturity/YTM/duration + implied price at 4 %.

## Data entities used

- `Order` — new columns `time_in_force`, `expire_after`, `trail_amount`,
  `trail_pct`, `trail_reference` (additive; existing rows read GTC/NULL).
- `Instrument` — new columns `coupon_rate`, `maturity_date` (additive,
  nullable; bonds only).
- `AuditEvent` — new event types `ORDER_EXPIRED` (system actor, INFO) and
  `STOP_TRIGGERED` payload variant for trailing; `ORDER_CANCELLED` gains an
  `IOC_UNFILLED` reason variant.
- Additive auto-migration: `_ADDITIVE_COLUMNS` extends to per-dialect DDL
  (SQLite `DATETIME` vs Postgres `TIMESTAMP WITH TIME ZONE`) and gains a
  Postgres-only varchar widen for `orders.order_type`.

## API endpoints used

- `POST /orders` — ticket gains `time_in_force` (default GTC),
  `trail_amount`, `trail_pct`; response/`GET /orders*` expose them plus
  `expire_after`, `trail_reference`.
- `PATCH /orders/{id}` — gains `trail_amount`/`trail_pct`.
- `GET /instruments/{symbol}/bond-analytics?yield=<optional>` — new.

## Error / edge cases

- **Feed stale (no snapshot)**: orders stay working regardless of TIF; an
  IOC order in this edge rests until the first tick, which then fills or
  cancels it (documented degradation — validation already refuses BUYs with
  no market data).
- **Restart**: DAY orders whose `expire_after` passed during downtime are
  cancelled by the first tick after rebuild; trailing state survives via
  the persisted `trail_reference`.
- **Price exactly at trigger**: triggers (`<=`/`>=`), consistent with the
  STOP matrix (design 21 §A3).
- **Extreme prices in YTM solve**: bisection clamps at −99 % / +100 %.
- **No latest price for a bond**: `latest_price`/`ytm`/
  `modified_duration` return null; `implied_price` still computes from the
  supplied yield.
- **Existing semantics**: GTC default + no-column-change on the four
  existing types keep MARKET/LIMIT/STOP/STOP_LIMIT behavior byte-identical;
  the full suite must stay green.

## Acceptance criteria mapping

- Covered by `backend/tests/test_advanced_orders.py` (integration style of
  test_trading.py + deterministic engine-drive tests + pure-math units):
  IOC cancel/fill, DAY expiry with `ORDER_EXPIRED` audit, GTC unchanged,
  trailing reference tracking/trigger/validation 422s, bond YTM ≈ coupon at
  par, hand-computed duration, yield round-trip, 404 on non-bonds.
- Per [19 — Testing Strategy](19-testing-strategy.md), FR-ORD behaviors are
  verified as automated tests.
