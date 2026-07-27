# Changelog — STP Trading Platform

Progress log for the Nomura Tech Graduate Program 2026 final presentation.
Entries are reverse-chronological, one per milestone, each with its driver
(brief / SRS / stakeholder input), what changed, and how it was verified.
Requirement IDs refer to SRS-STP-2026-001; decisions D-xx to DESIGN.md.

---

## 2026-07-27 — Bug fix: dataset symbols showed "No price data"; switch dev DB to PostgreSQL

**Driver:** user report — switching to a dataset stock (AAPL, TSLA, …) showed
"No price data" on every timeframe; request to move off SQLite.

- **Root cause (found by inspecting the dev DB, not guessed):** the dev
  `stp.db` predated the dataset (10 legacy JPY instruments + 1,400 generated
  ticks). The loader's tick stage used a *global* "table empty?" check, so
  the presence of those legacy rows skipped the entire tick load — the 7
  dataset instruments were upserted but had zero price rows. **Not** a
  dataset-vs-wall-time issue: the simulation clock already decouples replay
  time from real time by design.
- **Fix:** per-symbol tick loading (skip only symbols that already have
  ticks); regression test with a non-empty `price_ticks` table. The healed
  dev DB now serves 377 intraday candles for AAPL/1D.
- **PostgreSQL 16 dev database:** selectable at launch —
  `./dev.sh` / `./dev.sh sqlite` (SQLite, zero setup) vs `./dev.sh postgre`
  (project-local cluster at `backend/.pgdata`, auto-initialized on first
  call; `make pg-start/pg-stop`, `make dev-postgre`). SQLite remains the
  zero-setup default and the test engine.
- Verified on Postgres: 120,382 ticks + 9,296 news items loaded, AAPL candles
  on all timeframes, market order FILLED, STP settlement advancing; backend
  43/43 tests green.

## 2026-07-26 — Dev convenience: one-command stack start

- `dev.sh` starts backend (:8000) and frontend (:5173) together with clean
  shutdown (Ctrl+C terminates the whole process tree, incl. uvicorn's reloader
  and vite); wired as `make dev`; README quickstart updated.

## 2026-07-26 — Trading workspace: single-screen trader UI (stakeholder interview)

**Driver:** trader interview — 5 requirements, all on one screen: single-click
orders, live P&L marks, price charts, GenAI news summary, risk exposure.

- New landing page **Trading workspace** (`/`) merging Dashboard + Charts +
  order ticket: ticker tape with watchlist, candle chart with indicators,
  order panel, risk panel, AI news panel, live positions table + account bar.
- **Single-click orders**: price-labelled BUY/SELL buttons, default sizes
  10/50/100 or custom; per-click idempotency key; inline rejection reasons.
- **Live marks**: positions polled 5 s with green/red price-flash; uP&L + %.
- **Risk exposure**: concentration / volatility gauges with amber-red
  thresholds, top-holdings bars, cash-vs-invested split (from FR-PFM-003 KPIs).
- **News summary (mock GenAI)**: new endpoint `GET /assistant/news-summary`
  — deterministic summarizer over the real news pack (sentiment mean, themes,
  headline citations), honestly marked `mock: true` pending a real LLM.
- Global visual refresh: modern terminal palette, panel chrome, flash
  micro-interactions; Dashboard/Charts pages deleted (`/charts/:symbol`
  redirects), nav reordered.
- Verified: backend 42/42 tests (incl. news-summary), frontend build zero
  type errors, live smoke of all five panels' data paths.

## 2026-07-26 — Real simulation dataset + news/sentiment features (TBD-06/16/17 resolved)

**Driver:** receipt of the program dataset `data.zip` (SRS INT-04); making
full use of it incl. the news pack (Nora's "prices move on feelings" point).

- **Dataset loader**: 7 US equities (AAPL, GOOG, IBM, MSFT, TSLA, UL, WMT,
  USD), 130 daily bars backfill + ~120k 1-minute bars (Jun-30→Aug-29),
  ~9.3k news items with per-ticker sentiment; idempotent, chunked; generated
  random-walk fallback when `data/` is absent (tests/CI unaffected).
- **Replay on a simulation clock**: minute bars walked in dataset-time order
  (default 5 bars/s ≈ 78 s/day, loops); charts, staleness and news visibility
  all follow the sim clock — the platform never sees past the replay
  position (D-10/D-11).
- **News consumers** (D-15): assistant news/sentiment intent with citations;
  `GET /instruments/{s}/news`, `GET /news/latest`, `GET /instruments/{s}/sentiment`;
  Charts News tab + sentiment panel + dashboard market-news widget.
- Seed universe switched JPY→USD (TBD-16/17); orders fill at real dataset
  prices end-to-end through STP settlement.
- Verified: 41/41 tests (4 new loader, 2 news), 15/15 E2E checks; fixed core
  bugs found by verification (replay datetime freeze, outbox-relay resilience
  under SQLite locks, sim-clock leak between app instances).

## 2026-07-26 — Initial MVP: full-stack STP platform

**Driver:** project brief + SRS-STP-2026-001 — 3-week MVP with
straight-through processing and DevSecOps from day one.

- **Design first**: SRS analyzed; `DESIGN.md` (architecture, decisions
  D-01…D-06, 12 validated Mermaid diagrams) split into 19 module design docs.
- **Backend** (FastAPI modular monolith, 15 modules): one-click order ticket
  → pre-trade validation → simulated execution → **straight-through
  settlement with zero manual steps** (FR-ORD-005); portfolios/valuation/KPIs;
  reports (PDF/CSV); technical indicators + price alerts; paper trading on
  the same pipeline; rule-based GenAI assistant with trade guardrail
  (FR-AI-003); access requests with multi-level approval, RBAC
  deny-by-default, JIT time-bound grants, CyberArk checkout (mock adapter),
  break-glass with review, hash-chained append-only audit, notifications,
  admin/governance dashboards.
- **Frontend** (React 18 + TS): 14 permission-gated screens, dark
  trading-terminal theme, ECharts candlesticks.
- **DevOps**: Dockerfiles + compose (Postgres/Redis), Terraform AWS reference
  (+ Azure parity notes), GitLab CI pipeline (lint→test→scan→build→deploy),
  README with demo script.
- Verified: 35/35 tests; 14/14 E2E checks incl. market order → fill →
  settlement, approval chain, break-glass, audit search.

## 2026-07-26 — Requirements & stakeholder preparation

- Project brief + SRS read in full; SRS is the requirements baseline.
- Stakeholder interview guide (`docs/stakeholder-interview-guide.md`):
  per-stakeholder question sets mapped to all 18 SRS TBD items (product
  owner, clients, tech developer, CTO, CFO, corporate/facilitators).

---

*Maintenance: append a dated entry for every meaningful change — driver,
what changed, verification. Keep entries quotable for the final presentation.*
