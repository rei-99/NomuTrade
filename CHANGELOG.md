# Changelog — STP Trading Platform

Progress log for the Nomura Tech Graduate Program 2026 final presentation.
Entries are reverse-chronological, one per milestone, each with its driver
(brief / SRS / stakeholder input), what changed, and how it was verified.
Requirement IDs refer to SRS-STP-2026-001; decisions D-xx to DESIGN.md.

---

## 2026-08-02 — Replay fast-forward (flush): `» +1d` button

**Driver:** owner ask — a button to advance a day so trade/settlement states
change on demand; clarified to **flush** semantics: every bar is read and
processed, not skipped.

- **Backend**: `POST /api/v1/marketdata/replay/skip {days: 1..10}` — the
  replayer replays the next `days` calendar days at **full speed** (no pacing
  sleep): every bar is published to the tick stream, so the execution engine
  (fills, stop/trailing triggers), valuation projector, alerts and the WS
  broadcast all process the day as if it really passed; normal wall-aligned
  pacing resumes at the target day's first bar (pacing grid re-based after
  the burst so elapsed flush time isn't slept off). Any authenticated user
  (training-environment control), audited as `REPLAY_SKIP` with
  `mode: "flush"`; 409 `STATE_CONFLICT` when the fallback feed is running.
- **Frontend**: `» +1d` ghost button next to the SIM clock (EN/JA tooltips
  say "every tick processed"), toast on press.
- Verified: backend **119/119** (skip-target unit test incl. weekend/clamp;
  endpoint 401/409/200 + audit row); `npm run build` clean; live — a full
  market day flushed in ~2 s (sim 10:39 → 15:59 → next day 09:31, then
  pacing resumes at 1 bar/s); a marketable LIMIT order **filled at the live
  tick price mid-flush**; the one earlier "no-fill" was correct model
  behavior (engine matches on bar *closes*; the bar low dipped below the
  limit but no close did).



**Driver:** owner UX review — the summary's cited-3 headlines duplicated the
feed but weren't clickable ("why not just keep them in line?"), and the panel
showed two scrollbars.

- **Cited-3 block removed** from the summary (sentiment row, strip, prose,
  topic chips, as-of remain); the feed below carries the same headlines —
  clickable with the detail modal, as before. One list, one behavior.
- **Nested scrollbar removed**: `.news-list` had its own `max-height: 420px;
  overflow-y: auto` inside the already-scrolling `.panel-scroll` — the inner
  rule is gone (its only consumer is this panel); the panel scrolls as a
  single region.
- Verified: `npm run build` clean; headless probe — no cited block, exactly
  one scroll region (`panel-scroll`), 8 clickable feed rows, feed click opens
  the detail modal (screenshot).



**Driver:** owner UX review — a long summary could push the third cited
headline out of reach. Root cause: the CSS for the earlier "fixed summary +
separately scrolling list" design targeted `.news-panel > .news-summary` /
`> .news-list` — **dead selectors** since the summary/list are nested inside
`.panel-scroll`, so the scroll container sized to content and clipped at the
grid boundary instead of scrolling.

- The panel is now one scroll region: header pinned, everything below
  (summary → divider → feed) scrolls together — the layout the code was
  already structured for; the two dead rules and the stale design comment are
  gone (owner call, supersedes the fixed-summary design).
- Verified: `npm run build` clean; headless Playwright probe at 1680×820 —
  panel content 787 px vs 200 px visible, scrollTop 0 → 587, bottom feed
  items reachable (screenshot confirmed); at roomier sizes nothing scrolls
  unnecessarily (content fits).



**Driver:** owner instruction set — a real AI agent behind config: (1) LLM
news summary; (2) Assistant with RAG over the project's own docs plus
advisory trade help (never deciding); (3) researched extras; (4) presentation
plus-point. Keys are owner-configured later, so the default stays mock and
startup must check connectivity and fall back. Design:
`docs/design/27-genai-agent.md` (D-27.1…D-27.7, indexed).

- **Provider config (D-27.1)**: `LLM_PROVIDER` (`mock`|`openai`),
  `LLM_API_URL`/`LLM_API_KEY`, `LLM_CHAT_MODEL`/`LLM_EMBED_MODEL`,
  `EMBEDDING_API_URL`/`EMBEDDING_API_KEY` (fall back to the chat endpoint),
  `LLM_TIMEOUT_SECONDS`, `RAG_TOP_K`. `.env.example` is the documented place
  to set them; `backend/.env` stays gitignored.
- **Startup self-check (D-27.2)**: lifespan probes the chat (`GET /models`)
  and embedding endpoints (5 s) → `app.state.llm_status`; any failure keeps
  the app in mock mode (boot never fails). New `llm` tile in integration
  health shows `live: <model>` / `mock: not configured` /
  `down: <reason> — using mock`; per-call LLM errors fall back per request.
- **News summary (D-27.3)**: structured grounding unchanged; when live, the
  chat model rewords it (≤60 words, figures verbatim) → `mock: false,
  model: <model>`; rules prose otherwise.
- **RAG help intent (D-27.4)**: README/DESIGN/design docs chunked
  heading-aware (fenced diagrams stripped at ingest), embedded once per
  content hash into the new `doc_embeddings` table (cost control); cosine
  retrieval when embeddings are live, keyword token-overlap otherwise.
  Answers cite `[doc]` sources; citation cards render clean text (no raw
  JSON).
- **Advisory (D-27.5)**: trade answers gain position + news-sentiment
  grounding and a fixed disclaimer; new review intent ("should I trim MSFT?")
  returns a KPI-grounded advisory review (positions, concentration, VaR/ES,
  drawdown, bond metrics) with the same disclaimer. Guardrails unchanged: no
  order path, `suggested_ticket` stays rule-built and user-confirmed.
  Assistant currency bug fixed (¥→USD via `Instrument.currency`).
- **Presentation (D-27.7)**: demo-guide "AI agent" section (configure →
  self-check tile → three beats); script slide 7 rewritten (RAG + review +
  config-to-live arc, stale 78 s replay figure fixed); honesty-map and Q&A
  (B5/T14) updated.
- Verified: backend **117/117** (11 new — provider selection live/down/mock,
  LLM news reword + error fallback, RAG chunking/keyword retrieval/index
  reuse, help with doc citations, review disclaimer with zero Order rows,
  USD currency, health tile); `npm run build` clean; live boots — mock tile
  `mock: not configured`, stub-key boot tile **DOWN**
  "All connection attempts failed — using mock" with everything still
  working; headless screenshots of the tile and a RAG help answer
  (mermaid-free after the ingest fix).



**Driver:** owner UX report — the news summary "seemed not to work
correctly". Diagnosis: not a bug — the backend's mock summarizer embedded
"Recurring themes: …" and "Notable headlines: 'A' (L); 'B' (L); 'C' (L)"
verbatim in the prose, and the frontend *also* rendered the same topics as
chips and the same 3 headlines as citations, then 8 more in the feed — every
item appeared three times and the prose read like a data dump.

- **Backend** (`/assistant/news-summary`): the prose is now the coverage
  one-liner only ("TSLA coverage this week is mildly bullish (mean sentiment
  +0.14 across 98 articles)."); themes and notable headlines ship only via
  the structured `top_topics` / `headlines` fields they already had. The
  endpoint is the sole consumer of `get_news_summary` (chat answers build
  prose separately), so nothing else changes; no frontend edit was needed —
  the panel was already structured for this.
- Panel now reads: mock badge → sentiment badge + strip → coverage one-liner
  → topic chips → 3 cited headlines → divider → full feed.
- Verified: backend **106/106**; live API check (prose trimmed, structured
  fields intact); headless screenshot of the TSLA panel before/after.



**Driver:** owner ask — convert percentage metrics into ring gauges so the
panel reads as a proper gauge row.

- VaR and ES join Concentration and Volatility as donuts (4 across,
  space-between spread); each keeps its threshold coloring (VaR/ES red > 10,
  amber > 5) and 3-decimal display; N/A renders as a grey ring. Sharpe stays
  a stat tile (not a percentage); the stat row is now Sharpe / Max drawdown /
  Day change. Two caption keys that only the old stat tiles used removed from
  both dictionaries.
- Verified: `npm run build` clean; headless screenshot — CONCENTRATION 73 %,
  VOLATILITY 0.08 %, VAR 0.003 %, ES 0.004 % as rings, stat tiles and the
  bond-book line intact at 1680×1000.



**Driver:** owner question — the Risk Exposure panel showed only the two
donut gauges; what else belongs there. (The panel already had VaR/drawdown/
day-change tiles and mix/holdings/cash bars; this round adds the missing
risk-metric depth and the bond angle.)

- **New valuation KPIs** (`GET /portfolios/{id}/valuation`):
  `es_95_1d_pct` (expected shortfall — mean of the worst-5% daily returns;
  always ≥ VaR by construction), `sharpe_ratio` (annualized, rf=0,
  documented simplification), and `bond_wtd_ytm_pct` +
  `bond_wtd_mod_duration` — market-value-weighted over bond holdings using
  the design-24 bond math (null for equity-only books, never fabricated).
  All series KPIs now share **one** `_daily_total_values` fetch per request
  (was one scan per KPI — the 3× redundancy from the risk-viz branch, gone
  as the panel grew to five consumers).
- **Risk panel UI**: ES and Sharpe stat tiles (with tone thresholds and
  captions), a conditional "Bond book" line (YTM · mod. duration) that
  appears only when the book holds bonds, and precision bumps so small
  honest values don't render as zero (volatility/VaR/ES/drawdown at 2–3
  decimals). 6 new EN/JA key pairs (ES/シャープ比/債券ブック…); Valuation
  type extended.
- Verified: backend **106/106** (repriced-history test extended — ES ≥ VaR,
  Sharpe non-null, equity-only bond keys null; new bond-book test — UST10Y
  fill → weighted YTM/duration in sane ranges); `npm run build` clean;
  live — Desk Book 1 reports VaR 0.003 %, ES 0.004 %, Sharpe 0.09–0.16,
  bond YTM 3.74 %, mod. duration 4.60 y; headless screenshots of the panel
  at 1680×1000 and 1680×1600 confirm every tile renders with real values.



**Driver:** owner asks — fix the VaR `N/A` on the demo book, and let the
replay start mid-dataset so the sim clock shows late-August dates on stage.

- **Risk KPIs from day one**: `_daily_total_values` (shared by volatility,
  VaR-95 and max drawdown) now falls back to **repricing the current book
  through stored daily closes** when live snapshot history is < 10 days — the
  standard "how would today's book have moved" approximation: one close per
  instrument-day aggregated in SQL (minute bars never leave the DB),
  bond-aware `trade_value()`, cash held constant, sim-clock capped (D-10).
  Self-heals: snapshots take over once 10 days exist. A position-less
  portfolio still correctly shows N/A. Debugging surfaced the documented
  naive-vs-aware pitfall for real: SQLite **silently mis-compares tz-aware
  bind params** (aware → 0 rows, naive → all rows), so the ts filter binds
  per-dialect.
- **`REPLAY_START`** (empty = first bar): ISO date/datetime selecting where
  each replay pass starts — loops restart there too; invalid or past-the-end
  falls back to the dataset start with a warning. The demo machine's
  gitignored `backend/.env` sets `2026-08-24` (final dataset week).
- Docs synced: README, AGENTS.md (config table), DESIGN.md D-11, design 01,
  demo-guide (VaR honesty note rewritten; REPLAY_START documented).
- Verified: backend **105/105** (2 new — `_replay_start_index` unit test;
  repriced-history KPI test with an explicit STP wait); live — sim clock
  starts at **2026-08-24 09:30** and steps 1 min/wall-second (reached
  08-25 09:47 during the check); Desk Book 1 valuation shows
  volatility 0.075 %, VaR 0.0032 %, drawdown 0.019 % instead of N/A (small
  by construction — the book is ~99.9 % cash; the math is honest).



**Driver:** owner asks — (a) slow the replay so the sim clock ticks one
dataset row per wall second, flipping in clean one-minute steps
(13:23→13:24→13:25) in step with the wall clock; (b) a wider Q&A net for a
mixed business/tech audience.

- **Replay pacing**: `REPLAY_BARS_PER_SECOND` default 5.0 → **1.0** (~6.5 min
  per market day); the replayer now paces emissions on a wall-clock-aligned
  grid (`next whole multiple of the interval`, catch-up instead of drift)
  instead of a naive sleep — the sim clock flips exactly once per second.
  Docs synced: README, AGENTS.md, DESIGN.md D-11, data/README, design 01.
- **`presentation/qa-brainstorm.md`**: 35 likely questions (15 business /
  20 tech) with short suggested answers, honesty flags on the candid ones
  (unexecuted pipeline, no reservation accounting, audit-chain fork risk,
  unmeasured perf targets) and a pointer from script §9.
- Verified: backend **103/103**; live WS probe — tick groups arrive at mean
  interval **1.000 s** (min 0.998 / max 1.001), first tick of each group
  within 4 ms of the whole-second boundary, sim ts stepping one clean
  minute per second.



**Driver:** owner asks — (a) seed realistic trade history so risk/portfolio
pages demo well, keeping at least one equity and one bond untouched for live
trading on stage; (b) a recommended demo flow; (c) an automated, recorded
browser demo with a step script.

- **Seeded trades (Desk Book 1, via the real order pipeline, all SETTLED)**:
  MSFT 100, TSLA 30→sell 10 (realized P&L), GOOG 25, UST10Y 2,000 face,
  AAPL29 1,000 face (AAPL 50→50→5 from earlier sessions). Live KPIs: total
  value ≈ $50.0M, realized +$244, allocation ≈ 94/6 equity/bond,
  concentration ≈ 74% MSFT. **IBM, UL and MSFT31 stay untouched** — the live
  demo trade targets (WMT/UST2Y were spent on the recording). Client
  Portfolio A stays cash-only (Client has no `ORDER_SUBMIT` by design).
- **`presentation/demo-guide.md`**: the seeded-data table, a 6-beat
  run-of-show (trade IBM → trade MSFT31 → portfolio/risk → settlement column
  → ops governance → governance extras), honesty notes (STALE on replay
  loop, VaR N/A until snapshot history accumulates), and the video step
  script with timestamps.
- **Automated recorder** (`tools/demo-recorder/`, playwright-core against the
  installed Edge — no browser download; 1 MiB ffmpeg in `.pw/`, git-ignored):
  drives the real UI at human pace (visible typing, two-click confirm) and
  records the page as video. Captured
  `recordings/demo-….webm` (40 s): trader login → WMT 25 BUY filled → UST2Y
  1,000 BUY filled (bond analytics card) → Desk Book 1 KPIs/allocation/P&L →
  blotter settlement column → ops Governance settlements lane — plus 6
  per-beat screenshots and a beats log. Re-runnable; serves as the
  presentation's video fallback.
- Verified: video + screenshots inspected beat by beat (fills, toasts,
  KPIs, SETTLED badges all genuine); positions/valuation/settlements
  cross-checked via the API.



**Driver:** owner direction — (a) Pass 2 of the improvement review: make the
"S" in STP visible and give the dead `STP_EXCEPTION_HANDLE` permission a
purpose; (b) the group presents as **4 people in a 15-minute slot** (10 min
presenting+demoing, 5 min Q&A).

- **New `settlements` module (17th)**: `GET /api/v1/settlements`
  (`TRADE_VIEW`; own-book scoped unless `PORTFOLIO_VIEW_ALL` /
  `STP_EXCEPTION_HANDLE` / `INTEGRATION_MONITOR`) — settlement instructions
  joined to execution/order/instrument with bond-aware `trade_value()`,
  cursor-paged, `portfolio_id` + `lifecycle_state` filters.
  `POST /api/v1/settlements/exceptions/{execution_id}/retry`
  (`STP_EXCEPTION_HANDLE` — its first real consumer): 404 unknown execution,
  409 when an instruction already exists, otherwise re-publishes the
  `trading.executions` event through the outbox (the STP worker's idempotency
  check makes it safe), audits `STP_EXCEPTION_RETRY` (WARN, fail-closed),
  notifies the owner. STP exceptions are no longer a dead end (FR-ORD-005 E1).
- **`GET /trades` gains `settlement_state`** via one LEFT OUTER JOIN (no N+1).
- **Frontend**: Settlement column in the Trade Blotter (sortable, state
  badges) + WS `execution`-hint refetch; Governance gains a "Recent
  settlements" lane (newest 15, 10 s poll) and per-exception **Retry**
  buttons for `STP_EXCEPTION_HANDLE` holders (409 → standard conflict toast +
  refetch); the exceptions list text was silently broken (fields that don't
  exist → raw JSON) and now renders `execution_id · state · age`. 11 new
  EN/JA i18n pairs (決済/約定済/確認済/決済済…).
- **Presentation pack**: script + deck rebuilt for **4 presenters** (old P5
  merged into P1; balanced 2:00/3:30/2:30/2:00) and a **10-minute
  present+demo** flow (7 beats; two war stories demoted to Q&A ammo) + 5-min
  Q&A; metrics refreshed to reality (42 commits / 14 merges / 12 branches /
  ~100 tests / **17 modules** / 26 design docs — measured, then pinned to the
  post-merge counts); STP-flow slides carry the settlement-visibility line.
  Deck regenerated (`build_deck.py`, python-pptx in the venv only) — `verify()`
  PASS: 21 slides, notes on all, no empty frames, no off-canvas shapes.
  AGENTS.md: 17 modules incl. `settlements`, 103 tests.
- Verified: backend **103/103** (4 new in `test_settlements.py` — lifecycle +
  trades join, hand-computed bond value, own-vs-all scoping, retry
  403/404/409/200 + instruction created + audit); `npm run build` zero type
  errors; live E2E — MARKET BUY 5 AAPL filled and its instruction reached
  **SETTLED** with zero manual steps, visible in `GET /settlements` and the
  blotter; ops 200 vs trader 403 on the retry gate, 404 on bogus id;
  headless screenshots of the blotter Settlement column and the ops
  Governance settlements lane.



**Driver:** owner instruction — portfolio management belongs in the Trader's
view too, scoped to their own books.

- `portfolios` added to the Trader persona tab list (`personas.ts`), between
  Trading and Orders. Backend scoping already does the right thing:
  `GET /portfolios` filters to `owner_id == caller` without
  `PORTFOLIO_VIEW_ALL`, and `_require_portfolio_access` 403s other people's
  books — so no backend change; the permission model (`PORTFOLIO_VIEW` vs
  `PORTFOLIO_VIEW_ALL`) drives the own-vs-all split for Trader vs Risk/Ops.
  Design 26 table updated.
- Verified: `npm run build` clean; live — trader `GET /portfolios` returns
  exactly Desk Book 1 (HOUSE) + Paper — Demo Trader (PAPER), no Client
  Portfolio A; headless screenshot of the trader Portfolios page with the
  new nav.

---

## 2026-08-01 — Demo-critical fixes: 4-persona mapping, reports correctness, UX quick wins, docs reconciliation

**Driver:** owner-commissioned deep improvement review of the whole repo. The
headline finding: the design-25/26 persona model dead-ended three of the eight
seeded users (Approver couldn't reach Approvals, Auditor landed on a 403,
Client saw no portfolio pages) — breaking the README demo script. Plus a batch
of verified correctness bugs and stale docs. Frontend + backend, no
architecture changes.

- **4-persona mapping fixed** (design 26 §R1a, personas kept to
  Trader/Operation/Risk/Admin per owner instruction): `APPROVE_ACCESS → Admin`
  (Approver lands on **Approvals**); `PORTFOLIO_VIEW → Risk` fallback (Client
  lands on **Portfolios**, sees Portfolios/Reports/Assistant/Access/
  Notifications — the client stakeholder's core ask is reachable again);
  static `PERSONA_HOME` replaced by `personaHome(persona, perms)` = first
  permission-passing tab (Auditor lands on **Audit**, no more static-home
  403s); Reports tab perm-gated (`REPORT_VIEW`); Assistant added to the Risk
  list (`ASSISTANT_USE`-gated); missing Guard on `/portfolios/:id` added
  (perms-only — traders reach it from the positions table); design 26 doc
  amended with the mapping and the dynamic-home rule.
- **`GET /roles` readable by any authenticated user** (deliberate contract
  change: the access-request form needs the role catalog; role names aren't
  sensitive; ROLE_MANAGE gates writes, ROLE_VIEW keeps `/permissions`).
  Access page now loads "my requests" independently of the roles call, so a
  roles failure no longer blanks the page.
- **Reports correctness**: holdings/transactions builders use the bond-aware
  `trade_value()` (bond values were 100× overstated); new `ReportStatus`
  StrEnum + **FAILED state** — a render failure marks the row FAILED, deletes
  the partial file, audits `REPORT_FAILED`, and download returns a clear 409
  instead of the misleading "file missing" 404.
- **Paper reset cancels working orders** (CANCELLED, reason `PAPER_RESET`,
  audit + notification) — orders no longer fill after a reset.
- **Frontend quick wins**: Orders page makes one `/orders` call per poll (was
  a per-portfolio fan-out every 5 s); transactions date filter includes the
  end day (was excluded by a midnight boundary); WS client stops reconnecting
  on close 4401 and bounces to /login like the 401 path; Governance
  access-review button gated on `GOVERNANCE_VIEW`; directory/SMTP health
  tiles carry an honest "mock" badge; the news summary reads the backend's
  `mock` field and badges itself "Rule-based summary (mock LLM)".
- **i18n**: Reports schedules panel, Notifications preferences, bell
  "View all", report statuses REQUESTED/DONE/FAILED — 21 new EN/JA key pairs
  (parity compile-enforced).
- **Docs reconciliation**: README (the "No WebSocket in this build"
  limitation was false since design 22 — corrected; TRAILING_STOP + TIF added
  to order types; demo-users table gains a Persona column), AGENTS.md (16
  modules incl. `restricted`, 99 tests, 26 design docs), dev.sh
  (`$USER@localhost` PG URL — was hardcoded to one machine; login banner
  reflects password login), presentation/script.md (metrics current, Q&A #3
  rewritten for password login, demo checklist uses the login form, designs
  25/26 noted). Deck (.pptx) refresh is a pending follow-up.
- Verified: backend **99/99** (4 new: bond report value hand-computed,
  FAILED state + 409 download, paper-reset cancel, /roles open to all);
  `npm run build` zero type errors; live E2E — client `GET /roles` 200,
  approver `GET /approvals` 200, auditor governance 403 contained server-side
  as designed; headless screenshots — approver→Approval Inbox,
  client→Portfolios (Client Portfolio A), auditor→Audit Events, ops
  Governance with mock badges and no 403ing button. Note: the local dev DB
  still carries JPY-era seed balances (100M/50M from the original seed;
  re-seed to refresh) — a data artifact, not a code bug.



**Driver:** owner asks — more portfolio visualization (trader's perspective),
more risk metrics, hide Access Requests from traders.

- **Portfolio Insights section** (PortfolioDetail): allocation donut with
  Asset-class | Holdings toggle (EQUITY/BOND, or top-5 + Other + Cash slice)
  answering "how is the book spread?"; P&L contribution horizontal bars with
  Unrealized | Day toggle (sorted, pos/neg, nulls never plotted as zero)
  answering "what's making/losing me money?". Existing fetches reused.
- **Risk metrics**: valuation KPIs gain **`var_95_1d_pct`** (historical 95%
  1-day VaR from daily book returns, null under 10 observations) and
  **`max_drawdown_pct`** (largest peak-to-trough decline) — computed from
  ValuationSnapshot history like volatility. Risk panel adds VaR and
  drawdown stat cards (threshold-colored), Day P&L (money + %), and an
  EQUITY/BOND asset-mix bar; concentration/volatility donuts unchanged.
- **Trader tab set**: Access Requests removed (deep links get the friendly
  not-available page; API unchanged).
- **Cross-branch bug fixed (root-caused during verification):** the
  advanced-orders per-dialect DDL keyed maps on `"postgres"` but
  `conn.dialect.name` is `"postgresql"` on asyncpg — `KeyError: 'postgresql'`
  made the app fail to boot on PostgreSQL (SQLite CI masked it; also fixed a
  latent never-firing column-widen guard).
- Verified: backend **95/95** (2 new KPI tests); live check of KPI values;
  headless screenshots — Insights donut + P&L bars on Desk Book 1, risk
  panel with VaR/drawdown/asset-mix, trader nav without Access Requests.

## 2026-08-01 — Role-faithful views + real login (design 26)

**Driver:** owner instruction set — roles should see only the tabs their job
needs (researched from SRS §2.3 role duties), and a real username+password
login instead of persona cards.

- **Persona-faithful views**: Trader → home Trading (full execution tabs);
  **Operation** → Trades/Governance/Access/Notifications only (their
  business is the settlement flow + integration health — no trading panel);
  **Risk** → Portfolios/Trades/Audit/Governance/Reports (+ a new Portfolios
  tab for book oversight); **Admin** → Admin/Governance/Audit/Approvals.
  Post-login lands on the persona home; non-traders hitting `/` redirect
  there too; deep links outside the set keep the friendly not-available
  page. Two UX bugs fixed in verification: stray `"trading"` left in the
  Operation tab set, and the Governance page calling `/admin/health` on
  `GOVERNANCE_VIEW` instead of `INTEGRATION_MONITOR` (403 toast for Risk).
- **Real login**: `POST /auth/login` (email+password) — PBKDF2-HMAC-SHA256
  hashes (stdlib, zero deps), `users.password_hash` additive migration,
  idempotent startup patch giving seeded users the demo password
  **`demo1234`** (training env, README-documented), uniform
  anti-enumeration 401 (incl. dummy-verify timing flattening), per-email
  lockout 5 failures → 60 s with `retry_after_seconds`, full audit.
  `/auth/dev-login` stays DEV_AUTH-gated for tests/tooling.
- **Login page**: terminal-styled form (show/hide password, spinner,
  envelope error banner with traceId + live lockout countdown), demo
  credentials behind one disclosure card; EN+JA.
- Verified: backend **93/93** (6 new auth tests); live E2E — good login,
  bad-vs-unknown identical bodies, lockout with correct password rejected
  during window; headless screenshots of EN+JA forms and each persona home
  (Operation: no Trading tab; Risk: Portfolios present, no error toasts).

## 2026-08-01 — Workspace proportions: chart-dominant grid

- Owner tweak to the design-25 layout: chart now spans 8/12 columns (2:1)
  and 3:2 rows (bottom three panels share the smaller track); ≥1401px only —
  below that the ticket keeps 7:5. Below 800px viewport height (or 1100px
  width) the page drops to compact document flow (page scrolls) instead of
  clipping the order ticket. Verified: headless screenshots at 1920×1080,
  1680×900/1000 and 1366×768 — chart dominant, BUY/SELL always on screen,
  bottom panels aligned, no dead space.

## 2026-08-01 — UX round 1 (design 25): Japanese UI, 4 personas, bond/equity split, layout v2, price-follow

**Driver:** owner instruction set (5 items); design doc
`docs/design/25-ux-round-1.md`. Frontend-only; backend untouched (87/87).

- **U1 — EN/JA i18n**: hand-rolled `I18nProvider` + `t()` with `{var}`
  interpolation and type-enforced dictionary parity (`src/i18n/en|ja.ts`,
  574 keys each — drift is a compile error); EN|JA pills in the top bar,
  persisted `stp_lang`, `<html lang>`, locale-aware `Intl` formatting
  (en-US/ja-JP). Full pass over every page and shared component with
  natural financial Japanese (成行/指値/逆指値, 買い/売り, 評価損益,
  シミュレーション取引, 権限リクエスト…); backend-generated text (news
  summary, error messages, dataset labels) correctly passes through.
- **U2 — Four personas**: Trader / Risk / Operation / Admin, derived from
  permission sets (backend 8-role RBAC untouched); nav renders exactly the
  persona's tabs, deep links outside the set get a friendly "not available
  for your role" page, login shows 4 persona cards (remaining demo users
  behind a disclosure). Documented as a presentation-layer consolidation.
- **U3 — Bond/equity split**: `Equities | Bonds` scope toggle in the tape
  (chips + hero picker filtered, session-persisted); symbol search groups
  by asset class.
- **U4 — Layout v2**: chart extends vertically (7:5 chart:order); bottom
  row Positions | Risk | News as three equal, aligned, internally-scrolling
  panels; account chips absorbed into the Positions header; one-screen rule
  and ≤1100px fallback kept.
- **U5 — Price fields follow the instrument**: `useAnchoredPriceFields`
  hook — limit/stop/trail prices re-anchor to the newly selected
  instrument's last price on symbol change (dirty-tracked manual edits are
  only preserved within the same symbol; terminal convention, documented).
- Verified: `npm run build` zero type errors (twice — after layout and
  after i18n); headless screenshots of the EN layout (persona-filtered
  Trader nav, scope toggle, bottom-row panels) and the JA workspace
  (株式/債券 toggle, 成行/買い/売り order panel, JA nav) at 1680×1000.

## 2026-07-30 — Final presentation pack: script + 21-slide deck

**Driver:** `final-presentation-prompts.md` — two-prompt flow (script, then
slides) for the program's final team presentation.

- `presentation/script.md` — 15-min + 5-min Q&A script grounded in the repo:
  four-stakeholder-voices arc (45% platform / 55% journey), minute-by-minute
  flow with 5 presenters and scripted handovers, 3 real war stories (dataset
  load bug, midnight-flaky test, real-time pitfalls), 10-question Q&A prep
  incl. hostile ones, demo checklist + video fallback, and a module honesty
  map (mocked/partial called out: GenAI rule-based, CyberArk/SMTP adapters,
  unexecuted CI/CD/Terraform).
- `presentation/STP-final-presentation.pptx` — 21 slides (18 + A1–A3
  appendix), python-pptx, native editable shapes only, conservative
  financial palette + one accent, speaker notes on every slide from the
  script; `build_deck.py` is the re-runnable source (python-pptx installed
  in the project venv only, not in requirements.txt).
- Verified: deck re-opens (21 slides, 21 notes sections, no empty content
  frames, no off-canvas shapes); content spot-checked against the script.
  No Office/LibreOffice on this machine — a visual once-over in PowerPoint
  before presenting is still recommended.

## 2026-07-30 — Trading workspace: fluid at any resolution

**Driver:** owner request — the one-screen layout must fit and align at
every resolution, not just the two it was tuned for.

- **Wide screens**: removed the `.trading-page` `max-width: 1440px` cap —
  at 2560×1440 the chart/positions now absorb the full width (rail pinned
  at its clamp max) instead of leaving a dead gutter.
- **Fluid rail**: fixed ~380px → `clamp(320px, 26vw, 420px)`, so narrower
  screens give proportionally more width to the chart.
- **Single-column mode (≤1100px) fixed**: the base `.rail` grid placement
  was out-cascading the media-query reset, forcing an implicit second
  column and rendering an empty frame at e.g. 1024×700 — the reset is now
  properly scoped; collapsed mode renders everything in natural document
  flow (tape → chart 360px → order → risk → news → account → positions).
- **Order-type seg**: buttons no longer wrap mid-label; below 1400px the
  5-button seg wraps whole buttons 3+2 (STOP-LIMIT/TRAIL readable at every
  width), horizontal scroll as last resort.
- Verified: `npm run build` clean; headless-Chrome screenshots at
  2560×1440, 1920×1080, 1680×1000, 1366×768, 1280×800 and 1024×700 — no
  dead space, no clipped labels, no broken frames; one-screen rule holds
  ≥1100px, natural scroll below.

## 2026-07-30 — Trading workspace: one-screen layout, OHLC legend out of the canvas, bounded news

**Driver:** owner UX review of the Trading page — OHLC overlay collided with
chart controls; a news-heavy symbol pushed the account bar off screen;
everything must fit one screen with no dead space.

- **OHLC legend relocated**: the hover-following O/H/L/C readout moved from
  an absolutely-positioned canvas overlay (it overlapped chart controls) to
  a slim in-flow strip under the indicator chips — same follow-hover /
  fall-back-to-last-candle behavior, zero overlaying.
- **News panel bounded** (option 2 of the owner's two proposals — fixed
  panels with internal scrolling chosen over a slide-in banner: a trading
  terminal's value is spatial stability; transient banners compete with
  fill toasts and are easy to miss): summary + sentiment strip fixed at the
  top, headlines scroll internally; **headlines are clickable** (the good
  half of option 1) and open a Modal with the item's full payload (title,
  timestamp, topics, per-ticker sentiment scores + relevance) — the dataset
  carries no body/source fields, so nothing is invented.
- **True one-screen grid** (100vh, no page scrollbar at 1680×1000 and
  1366×768): full-height right rail (order ticket → bond analytics → risk →
  news) with shrink priorities — the order ticket never scrolls, risk
  shrinks first below 950px viewport height; left column = chart (flexes to
  fill, fixed 460px height removed) → account bar → positions (table
  scrolls internally). No dead gaps: rail bottom aligns with positions
  bottom; ≤1100px single-column fallback intact. Known trade-off: with a
  bond selected, the analytics card squeezes to an internally-scrolled
  strip at 1680×1000 (four panels, one rail).
- Verified: `npm run build` zero type errors; headless-Chrome screenshots
  at 1680×1000 (TSLA news-heavy and UST10Y bond) and 1366×768 — BUY/SELL
  fully visible at all sizes, account bar + positions on screen, no page
  scrollbar. (Headline-modal interaction is build-verified, not exercised
  headlessly.)

## 2026-07-30 — Tech-stakeholder interview, round 2 prep (INT-STP-2026-002)

- `docs/tech-stakeholder-interview-round2.md`: 26 questions for the tech
  stakeholder (Nora/developer persona) grounded in the running system —
  current-state tour, architecture review, production-readiness gaps
  (migrations TODO, in-memory state, untested deploy stack), performance
  validation (TBD-07 unmeasured), security open items (DEV_AUTH, WS token
  in URL, audit anchoring TBD-08), testing/operability, and phase-2
  direction (live news seam, real-LLM guardrails, multi-instance) — plus a
  playback list of proposals to attack and an outcome log mapped to open
  TBDs.

## 2026-07-30 — Advanced orders (design 24): time-in-force, TRAILING_STOP, bond analytics

**Driver:** owner-directed feature program — building the deferred items of
the product-owner feedback round (design 21 §Deferred): TIF/trailing stops
and bond yield/duration analytics; extends the TBD-18 resolution.

- **Design first**: `docs/design/24-advanced-orders.md` pins D-24.1 (TIF:
  GTC default; DAY expiry at sim end-of-day via `orders.expire_after`,
  ORDER_EXPIRED audit; IOC cancels unfilled with reason IOC_UNFILLED, never
  rests), D-24.2 (TRAILING_STOP: exactly one of trail_amount/trail_pct,
  persisted water-mark `trail_reference`, trigger rolls-then-checks, fills
  as MARKET with STOP_TRIGGERED audit, amendable trail) and D-24.3
  (`instruments.coupon_rate`/`maturity_date`, loader boot backfill;
  `GET /instruments/{symbol}/bond-analytics` — YTM bisection, modified
  duration, implied price at a supplied yield); indexed in
  `docs/design/README.md`; DESIGN.md §7 extends TBD-18.
- **Backend**: additive columns + per-dialect DDL in `_ADDITIVE_COLUMNS`
  (orders: time_in_force/expire_after/trail_*; instruments:
  coupon_rate/maturity_date) and a Postgres varchar widen for
  `orders.order_type` ("TRAILING_STOP"); execution engine handles DAY
  expiry, IOC and trailing triggers with audit/notify idioms unchanged;
  order JSON exposes the new fields; validation adds TRAIL_PARAM_REQUIRED /
  CONFLICT / FORBIDDEN and PRICE_FIELD_FORBIDDEN (422). Existing
  MARKET/LIMIT/STOP/STOP_LIMIT semantics untouched.
- **Frontend**: TIF selector (DAY/GTC/IOC) + TRAIL type with trail
  amount/% inputs (exactly-one, client-validated) in the order panel and
  ticket; TIF suffix + trail info in the Orders blotter; compact bond
  analytics card in the trading rail with debounced yield → implied price.
- Verified: 87/87 tests (16 new in `tests/test_advanced_orders.py`: TIF
  matrix, engine-driven trailing reference/trigger with exact ticks,
  hand-computed YTM/duration, loader backfill); frontend `npm run build`
  clean; live E2E on the running stack — SELL TRAILING_STOP 25 TSLA
  (trail_pct 0.05) FILLED as MARKET @ 222.35 against water-mark 222.61 and
  its settlement instruction reached SETTLED ~11 s later (full STP path,
  zero manual steps), IOC LIMIT far below market CANCELLED with
  IOC_UNFILLED, UST10Y bond analytics live (coupon 4.25, YTM 4.27 @
  99.85, mod. duration 7.35, implied price exactly 100 at yield=coupon,
  404 on equities); headless-Chrome screenshot of the workspace showing
  the TRAIL pill, TIF selector and bond analytics card.

---

## 2026-07-30 — Scheduled reports (design 23): daily/weekly per-user report schedules, TBD-13 resolved

**Driver:** owner-directed feature program — resolving SRS open item TBD-13
(report scheduling scope): scheduled reports are in MVP as in-app generation
on per-user schedules; email delivery stays out.

- **Design first**: `docs/design/23-scheduled-reports.md` pins the scope
  (per-user schedules over {portfolio, type, format, DAILY|WEEKLY}, sim-clock
  driven with wall-clock fallback, ≤10 active per user, hard delete, no
  backfill on create, catch-up capped at one run per sweep); indexed in
  `docs/design/README.md`; DESIGN.md §7 marks TBD-13 RESOLVED.
- **Backend**: new `ReportSchedule` entity; `POST/GET/DELETE
  /report-schedules` (REPORT_VIEW; same portfolio access check as
  `POST /reports`; 422 on the 11th active schedule); `report_scheduler`
  worker (10 s sweep, shielded DB units, per-schedule failure isolation)
  reusing the on-demand generation path — `create_report`'s core refactored
  into the shared `_generate_report` helper (endpoint behavior unchanged;
  scheduled runs add `schedule_id` to the audit payload and name the
  schedule in the notify body). Scheduled reports land in the ordinary
  `GET /reports` history with a REPORT notification.
- **Frontend**: "Report schedules" panel on the Reports page — create form
  (portfolio/type/format/frequency), list with next run + delete, 15 s
  polling, simulation-time hint; `ReportSchedule`/`ReportFrequency` types.
- Verified: backend **71/71** (3 new tests in `tests/test_experience.py` —
  CRUD round-trip + ownership isolation + 403 portfolio check, the
  active-schedule cap, and deterministic due-processing: DONE report with
  exact trailing period, file on disk, one-step advance, notify outbox,
  second run a no-op); `npm run build` zero type errors; live E2E on the sim
  clock — a DAILY schedule created via the API fired with no manual action
  (4 consecutive DONE holdings reports 6/30→7/4, REPORT notifications
  naming the schedule, one-step `next_run_at` advance), DELETE → 200;
  headless-Chrome screenshot of the Reports page schedules panel + history.

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
