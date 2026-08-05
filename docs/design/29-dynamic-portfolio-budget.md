# 29 — Dynamic Portfolio Budget (research + design; NOT implemented)

| | |
|---|---|
| **Status** | Research/design only — roadmap candidate (owner asked for design before any code) |
| **Date** | 2026-08-04 |
| **Driver** | Owner review: portfolio budget is currently a fixed number typed at creation (`POST /portfolios initial_cash`, default $1M); in the real world it should come out of a model |
| **Related** | [03 — Portfolio Management](03-portfolio-management.md) (valuation seams reused), design 21 §A4 (order restrictions/limits) |

## 1. Purpose

Define how the platform could move from *fixed, hand-typed starting cash* to a
**computed budget**: an initial allocation and ongoing buying-power limit
derived from a defensible capital model, suitable for a training platform to
explain and for an admin to override. This document is the research summary
and the chosen design; **no implementation yet**.

## 2. How the real world sets a trader's budget

A real desk never gets "cash"; it gets **risk capital**. Buying power is
derived from the risk the book is allowed to run. The industry models, in
order of increasing sophistication:

| Model | Idea | Used by | Fit for us |
|---|---|---|---|
| **Tiered desk limits** | Junior 100k, senior 1M, partner 10M — flat by mandate/seniority | Every bank's starting point | Already have it (our fixed default) — it is the floor, not the ceiling |
| **Instrument haircuts / margin** | Buying power reduced per instrument risk class (SPAN futures margin, Reg-T stock margin) | Brokers, CCPs | Good — simple, explainable, per-instrument |
| **VaR/ES-based capital** | Capital charge = k × ES_99 (FRTB multiplier ~1.5–3); budget sized so expected tail loss ≤ capital | FRTB / internal economic-capital | **Best** — we already compute ES-95 from real price history |
| **Kelly criterion** | Optimal size f* = μ/σ²; half-Kelly in practice ties stake to edge & vol | Quant PMs | Too aggressive/unstable for training; cite, don't use |
| **Mean-variance (Markowitz)** | Weights from expected returns + covariance matrix | PM allocation teams | Needs return forecasts we don't have; overkill |

Consensus pattern to copy: **tier floor × instrument-risk adjustment,
capped by a desk ceiling, reviewed periodically.** Everything below follows it.

## 3. Chosen model — Risk-Adjusted Capital Allocation (RACA)

For a requested book (universe of instruments + trader tier):

```
suggested_budget = clamp(
    tier_base × (target_vol / universe_vol),
    tier_floor,
    tier_ceiling,
)
```

- `tier_base` — the tier's reference capital (config: e.g. junior 100k,
  standard 500k, senior 2M).
- `universe_vol` — annualized volatility of an equal-weight reference basket
  of the book's intended instruments, computed from stored daily closes
  (same statistics stack as `valuation.py` — `stdev(returns) × √252`).
- `target_vol` — the vol the tier is *allowed* to run (config, e.g. 15%);
  a riskier universe shrinks the budget, a calmer one grows it.
- `tier_floor` / `tier_ceiling` — bounds so the model can never grant 0 or
  unlimited cash; the ceiling is the desk-level risk limit.

Why this one: three explainable inputs, all computable from data we already
hold (`PriceTick` daily closes), every number shown to the user with its
derivation ("vol 28% vs target 15% → budget ×0.54"), and it degrades
gracefully to today's fixed tier when vol history is missing.

## 4. Phased plan

**Phase 1 — quote only (advisory).** `GET /portfolios/budget-quote?universe=AAPL,TSLA&tier=standard`
→ `{suggested, universe_vol, target_vol, factor, floor, ceiling, derivation}`
plus a "Suggested: $X (why)" line in the create-portfolio dialog. Admin may
accept or override. Zero enforcement — ships the explanation first.

**Phase 2 — model default + admin override.** `POST /portfolios` computes the
model's suggestion when `initial_cash` is omitted (replacing the flat $1M
default); an explicit `initial_cash` within `[floor, ceiling]` is honored as
an override; outside it → 422 with the range. BudgetDecision audit event
(model inputs + override) on every creation.

**Phase 3 — living risk limit.** A periodic (sim-weekly) review worker
recomputes each book's realized ES-95 vs its allocated capital:
- breach (ES > capital × breach_factor) → buying power freeze on new BUYs +
  notify owner + ops queue entry (reuses design 21 §A4-style enforcement);
- persistent under-utilization → gentle suggestion to raise the limit.
This turns "budget" from a creation-time number into a governed limit —
the real-world shape.

## 5. Data entities / API sketch (phase 1-2 only)

- `BudgetTierConfig` (new table or config entries): tier, tier_base,
  target_vol, floor, ceiling — admin-editable later; seeded defaults.
  (New table = `create_all` picks it up; no migration concern.)
- `GET /portfolios/budget-quote` (REPORT_VIEW or ORDER_SUBMIT — pick with the
  owner when implemented; see design 09 conventions).
- No change to order validation in phase 1-2; phase 3 adds the BUY freeze
  beside the existing restricted/notional checks in `orders/validation.py`.

## 6. Edge cases & honesty notes

- **Fresh instruments with no history** (e.g. generated bonds early in a
  dev DB): fall back to the asset-class default vol (config) — never divide
  by zero or grant infinity.
- **Vol regime change**: the lookback (config, e.g. 60 sim days) makes the
  factor drift over time; phase-1 quotes are point-in-time and must say so.
- **Gaming**: a trader could pick a calm-looking universe to inflate the
  budget then trade hot — that's why phase 3's realized-ES review exists;
  phase 1-2 is advisory-only and should be presented as such.
- **Not Kelly, not Markowitz**: both are name-checked in the research table;
  explain in the presentation that we chose explainability and data we
  actually have over theoretical optimality — a deliberate engineering call,
  not ignorance.
