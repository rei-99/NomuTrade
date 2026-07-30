# Changelog — STP Trading Platform

Progress log for the Nomura Tech Graduate Program 2026 final presentation.
Entries are reverse-chronological, one per milestone, each with its driver
(brief / SRS / stakeholder input), what changed, and how it was verified.
Requirement IDs refer to SRS-STP-2026-001; decisions D-xx to DESIGN.md.

---

## 2026-07-30 — Real-time WebSocket push channel (design 22): tick broadcast + per-user hints

**Driver:** owner-directed feature program — implementing the authenticated
`/ws` channel reserved in nginx.conf / former DESIGN.md §9, delivering
NFR-PER-004 (dashboard refresh within 5 s of a tick).

- **Design first**: `docs/design/22-websocket-push.md` pins the route
  `WS /api/v1/ws` (parity mapping for the reserved `/ws`), `?token=` auth via
  the server-side session store (close 4401 on a bad token, 4403 when
  disabled), the push-as-hint model (**REST stays the source of truth**), one
  fan-out worker per stream, and the `WS_PUSH_ENABLED` kill-switch; indexed
  in `docs/design/README.md` and DESIGN.md §5.
- **Backend `push` module**: `WS /api/v1/ws` endpoint + ConnectionManager
  singleton (per-user registry, drop-on-send-failure) + three fan-out workers
  — `market.ticks` → broadcast `{"type":"tick"}`, `notify` → per-user
  `notification`, `trading.executions` → `execution` to the portfolio owner
  (resolved via `Portfolio.owner_id`, shielded DB lookup).
- **Frontend**: `src/api/ws.ts` singleton (auto-reconnect, backoff capped at
  ~15 s, subscribe-by-type, connection state; lifecycle bound to auth) +
  `useWsMessage`/`useWsState` hooks. Ticks are applied in place (TickerTape
  hero/O-H-L + sparklines, PriceChart last candle + last-price tag, SIM
  clock); execution/notification hints trigger immediate REST refetches
  (positions + valuation, notification list); polls relaxed to 30 s
  structural fallback; WS indicator in the top bar; vite dev proxy for
  `/api/v1/ws` (ws upgrade).
- Verified: backend **68/68** (6 new tests in `tests/test_realtime.py` —
  live-socket uvicorn + `websockets` client: 4401-reject path, tick
  broadcast, per-user notification filtering, execution-to-owner delivery);
  `npm run build` zero type errors; real-socket E2E against the live stack
  (bad token rejected 403, live replay ticks received direct :8000 and via
  the vite WS proxy :5174); headless-Chrome screenshots show the WS indicator
  and the workspace advancing on push ($247.05 → $248.48, sim clock
  07-02 13:44 → 07-04 10:41, news 18 → 51 articles in 30 s).

---

## 2026-07-30 — Trading workspace polish: live chart, sim clock, portfolios page, position close, table sorting

**Driver:** owner-directed frontend improvement program (stage 2 of 5).

- **Live price chart**: `PriceChart` polls its series every 5 s (previously
  only on symbol/timeframe/indicator change — the chart went stale while the
  tape ticked); the user's dataZoom window is tracked and re-applied across
  polls, and background refreshes no longer flash the skeleton loader.
- **Simulation clock in the top bar**: dataset time (e.g. `SIM 07-01 11:20`)
  next to the SIM LIVE/IDLE dot, derived from the latest candle ts of the
  workspace symbol (URL `?symbol=`, AAPL fallback) — pure frontend, an honest
  reflection of the D-10 sim clock without a new endpoint.
- **Portfolios index** (`/portfolios`, PORTFOLIO_VIEW): name / type badge /
  cash / total value in one list request; row → PortfolioDetail, which is now
  reachable from the nav for the first time.
- **Position Close action** (ORDER_SUBMIT-gated): per-row ghost button opens
  `OrderTicket` prefilled SELL / MARKET / full quantity via the Assistant
  prefill mechanism; row-click navigation preserved.
- **DataTable sorting**: opt-in `sortable` + `sortValue` column props,
  tri-state asc→desc→none cycle, numeric-aware stable sort, subtle arrow
  indicators; enabled on Trades and Orders; tables that don't opt in render
  byte-identical DOM.
- **Test hygiene (test-only)**: `_insert_news` in test_experience.py anchored
  to noon UTC — the relative-to-now fixture made
  `test_news_and_sentiment_endpoints` fail between 00:00–02:00 UTC (the two
  items straddled the day boundary, `assert 2 == 1` on the daily series); the
  suite is now deterministic across the UTC day boundary.
- Verified: `npm run build` zero type errors; backend **62/62** (31.6 s,
  incl. the fixed news test at 01:2x UTC — inside the former failure window);
  headless-Chrome screenshots: chart and tape advancing across three captures
  ($209.99 → $228.19 → $249.77) with the sim clock ticking, portfolios index,
  Close button on the TSLA position row, sort affordances on the blotter
  headers. (Active sort clicks and the Close-ticket submit flow are
  build-verified but not exercised headlessly.)

## 2026-07-29 — Frontend feature completeness: alerts UI, notification center, restricted admin, trade blotter

**Driver:** owner-directed frontend improvement program (stage 1 of 5) — four
shipped backend feature sets had no UI consumer at all.

- **Price alerts (`/alerts`)**: list/create/disable alert rules
  (ABOVE/BELOW/CROSSES_*) over `GET/POST/DELETE /analytics/alerts`; status
  badges incl. TRIGGERED; 10 s polling; open to every authenticated user
  (endpoints are login-gated only). Triggered alerts arrive as notifications —
  verified live: a TSLA rule fired off the replay tick stream within seconds.
- **Notification center (`/notifications`)**: full cursor-paged inbox with
  per-item mark-read; preferences panel (IN_APP/EMAIL channel toggles +
  per-category toggles; BREAK_GLASS/GRANT/PAM locked on as non-suppressible,
  FR-NTF-003 E1); bell dropdown gains a "View all" link.
- **Restricted instruments (Admin → Restricted tab, ROLE_MANAGE)**: SecAdmin
  add/remove UI over `GET/POST/DELETE /restricted-instruments`; Admin tabs are
  now deep-linkable via `?tab=`.
- **Trade blotter (`/trades`, TRADE_VIEW)**: dense execution table from
  `GET /trades` — side badges, bond-aware notional via `tradeValue`, portfolio
  filter, cursor paging, light poll of the newest page.
- **Types**: `AlertRule`, `NotificationItem`, `RestrictedInstrument` added;
  `NotificationPreferences` corrected to the real `{channels, categories}`
  shape (ASSUMPTION comment resolved against the backend).
- **Git workflow**: `develop` is now the integration branch (cut from `main`);
  feature branches merge into `develop` with the owner's standing pre-approval;
  `develop → main` stays approval-only (AGENTS.md updated).
- Verified: `npm run build` zero type errors; backend **62/62** tests on the
  new Windows dev machine (36.9 s); headless-browser screenshots (Chrome —
  Edge headless silently fails on Windows) of all four UIs against the live
  SQLite stack showing real data (TSLA fill $10,458.50, triggered alert +
  notification, IBM restriction).

## 2026-07-28 — AGENTS.md: working conventions & verification playbook

- Captured the owner's working preferences (design-first for big changes,
  changelog-per-milestone, SRS-ID traceability, sub-agent delegation with
  parent verification), the verification playbook (pytest, frontend build,
  E2E curl walkthrough, headless-browser UI screenshots) and the known
  pitfalls (aiosqlite cancellation, naive-vs-aware datetimes, dev.sh
  invocation, create_all limits, once-only seeding) — so any fresh session,
  on any machine, starts with full context.

## 2026-07-28 — Product-owner feedback round: two-click confirm, bonds, stops, order restrictions

**Driver:** stakeholder interview with the Head of Product Development
(`docs/interview_outcome.md`; analysis & design in docs/design/21).

- **Two-click order flow (A1):** BUY/SELL now opens an in-panel confirmation
  card (instrument, side, type, qty, est. cost, cash before/after) — Confirm
  submits, Cancel/Esc dismisses; idempotency key minted at Confirm.
- **Bonds (A2, resolves TBD-17):** 4 bonds added (UST10Y, UST2Y, AAPL29,
  MSFT31; USD, % of par, lot 1000) with generated mean-reverting price
  series in dataset time (data.zip is equities-only — documented). Proper
  bond cash math (`qty × price / 100`) in validation, STP and valuation;
  allocation KPI now splits EQUITY vs BOND.
- **STOP & STOP_LIMIT (A3, resolves TBD-18):** `orders.stop_price` column
  (+ additive auto-migration for existing DBs); trigger engine (BUY ≥ stop,
  SELL ≤ stop; STOP fills as MARKET, STOP_LIMIT converts to LIMIT with
  `STOP_TRIGGERED` audit + notification); amendable; 4-type selector in UI.
- **Order restrictions (A4):** per-order max notional (`ORDER_MAX_NOTIONAL`,
  422 `MAX_NOTIONAL_EXCEEDED`) + SecAdmin-managed restricted list
  (`GET/POST/DELETE /restricted-instruments`, 422 `RESTRICTED_INSTRUMENT`,
  audited add/remove).
- **Day-change KPIs (A5):** positions carry `prev_day_open`, `day_change`,
  `day_change_pct`; new Day chg column in the positions table.
- **News provider seam (A6):** `NewsProvider` interface — dataset provider
  (default) + Alpha Vantage live provider (`NEWS_PROVIDER`/`ALPHAVANTAGE_API_KEY`,
  off by default; the phase-2 integration point the business asked to see).
- Verified: **62/62 backend tests** (9 trading + 10 governance new), frontend
  build clean, live smoke on PostgreSQL (migration applied, bond fill with
  correct % of par cash, stop trigger, restriction 422, notional 422) and a
  headless screenshot of the 11-instrument workspace.

## 2026-07-28 — TradingView-calibrated terminal UI + instrument hygiene

**Driver:** owner request — research modern trading front-ends and make ours
fancier/more sophisticated (design: docs/design/20-trading-workspace-ui.md).

- **Research → design:** synthesized TradingView / IBKR / Kite / Binance
  conventions (dense dark workspace, restrained palette, hairline panel
  separation, tabular numerals, chart & order-entry conventions) into a
  design-language doc before implementing.
- **Terminal UI refresh (frontend-only, zero new deps):** TradingView
  palette (`#131722`/`#1e222d`/`#2962ff`, hairlines over shadows);
  watchlist chips with inline-SVG **sparklines**; hero price + day O/H/L;
  chart crosshair with axis tags, hover-following **OHLC legend**, and a
  **last-price axis tag**; MARKET/LIMIT segmented order entry with qty
  stepper, est. cost-vs-cash line and fill-feedback chip; P&L pill chips +
  allocation bars + pinned totals in positions; **conic-gradient donut**
  risk gauges; news sentiment meter strip; **global symbol search** +
  market-status dot in the top bar; skeleton loaders; focus-visible rings.
  All pages inherit via CSS variables; the 5 trader requirements unchanged.
- **Instrument hygiene:** the loader now retires off-dataset instruments
  (`tradable=false`), so legacy JPY symbols from pre-dataset dev DBs
  disappear from watchlists and can't be traded; all pickers (tape, symbol
  search, order ticket) filter to tradable.
- Verified: 43/43 backend tests; frontend build zero type errors;
  **headless-browser screenshots** of the live workspace confirming the new
  look, the 7-symbol tape and the news summary (mean +0.20, 76 articles).

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
