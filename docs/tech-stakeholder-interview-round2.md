# Tech Stakeholder Interview — Round 2 (Post-MVP System Review)

| | |
|---|---|
| **Document ID** | INT-STP-2026-002 |
| **Version** | 1.0 — Draft |
| **Date** | 30 July 2026 |
| **Source basis** | INT-STP-2026-001 (round 1, §3 Nora); DESIGN.md §7 open items; the running system as of `develop` (87 tests, 16 backend modules, designs 01–24) |
| **Interviewee** | Nora Smith — Tech Developer, incumbent system (or equivalent senior engineer) |

Purpose: round 1 asked about the *intended* system; the system now **exists**
and runs. This interview walks her through what was actually built and
harvests her critique where it matters most: production-readiness gaps,
architecture choices she would not have made, and what breaks first at scale.
Questions are open-ended on purpose; every one is annotated with what it
resolves or informs.

**How to run (suggested, 60 min):**

1. 10 min — show the running system (§0 briefing below as the tour route).
2. 35 min — the question themes A–F; skip anything already answered.
3. 10 min — play back the proposals in §7 ("what's wrong with this?").
4. 5 min — agree decisions, owners, follow-ups; log outcomes below.

---

## 0. Current-state briefing (the tour route)

What exists today, in the order worth showing her. Let her interrupt — her
spontaneous reactions are data.

- **STP core**: order → execution → STP settlement with zero manual steps
  (FR-ORD-005); MARKET/LIMIT/STOP/STOP_LIMIT + TIF (DAY/GTC/IOC) +
  TRAILING_STOP (design 24); 7 equities + 4 bonds with % of par math.
- **Event pipeline**: transactional outbox → in-process or Redis Streams bus
  → execution engine, STP worker, settlement sweeper, valuation projector,
  alert evaluator, notification worker, report scheduler (designs 02, 23).
- **Real-time**: dataset replay on a simulation clock (5 bars/s, loops);
  authenticated WebSocket push (`WS /api/v1/ws`, design 22) driving tape,
  chart, positions, notifications; polling as 30 s fallback.
- **Governance**: deny-by-default RBAC, JIT grants, CyberArk-mock checkout,
  break-glass (4 h), hash-chained append-only audit, SoD rules.
- **Breadth**: portfolios/KPIs, PDF/CSV reports + schedules, price alerts,
  paper trading, rule-based GenAI assistant, notifications with preferences.
- **Quality gates**: 87 backend tests green; frontend `tsc` strict build;
  zero frontend tests; design docs 01–24; CI/Terraform/compose written but
  **never executed** (no Docker on dev machines).
- **Known simplifications**: in-memory session store + notification prefs +
  alert cache; DEV_AUTH passwordless login on by default; reports on local
  disk; `create_all` + additive-column DDL instead of migrations; single
  uvicorn process.

---

## A. Architecture & event pipeline (review what we chose)

1. The pipeline is one FastAPI process with in-process workers behind a
   transactional outbox, not separate services. You've maintained a batch
   system for years — where does this design hurt first, and at what scale?
   *(D-01/D-02; her round-1 concern was "real-time is doable but
   challenging" — did we land on the right side?)*
2. Orders fill whole, at the latest tick price, with no order book and no
   slippage — including stops and trailing stops. Which of those
   simplifications would a trader's desk refuse to accept, and which are
   fine for a training platform? *(TBD-14 paper-trading realism — still
   open; decides whether a slippage/liquidity model joins the roadmap.)*
3. The simulation clock loops the dataset (a market day ≈ 78 s); orders,
   DAY-expiry, alerts, schedules and news all follow it. What edge cases do
   you see at the **loop restart** — and would you rather have run
   wall-clock time with a sped-up feed? *(D-10/D-11; we already fixed one
   loop-related bug class — naive vs aware datetimes.)*
4. Trailing stops persist their water-mark per order and DAY orders expire
   on the sim clock. Is that how you'd expect them to behave, or did we get
   the semantics wrong anywhere? *(design 24 — cheap to fix now, expensive
   after the demo.)*
5. Consumers are at-least-once and idempotent; notifications can
   theoretically duplicate on redelivery. Acceptable, or should the notify
   path get a dedup key? *(notifications worker; FR-NTF.)*

## B. Production-readiness gaps (the honest list)

6. Schema management is `create_all` + an additive-column table + a
   Postgres-only varchar widen — no Alembic. The code carries a TODO saying
   "replace before production". Is that the right call for the program, and
   what would you require before *your* team ran this? *(core/db.py TODO;
   NFR-MNT.)*
7. Sessions, notification preferences and the alert-rule cache are
   in-memory: they reset on restart. Which of those would you persist, and
   which are acceptable training-environment trade-offs? *(Redis impls exist
   for sessions; prefs/alert cache have none.)*
8. Reports land on local disk (`backend/var/reports`); the design says
   S3-compatible object storage. Scheduled reports multiply files daily
   (sim days). Is "local disk for training" defensible, and what retention
   would you impose? *(DESIGN 04, 18; design 23 capped catch-up at one run
   per sweep — is that enough?)*
9. `PriceTick` grows by ~120k rows per dataset load and the replay never
   deletes; the design mentions partitioning but it isn't implemented. At
   what point does this bite, and is retention/partitioning worth doing
   now? *(design 16 physical notes; NFR-PER.)*
10. Dev runs SQLite (tests) and optionally PostgreSQL 16; compose runs
    PG 15 + Redis. The additive DDL is only exercised on SQLite. Where do
    you see the biggest parity risk? *(AGENTS.md "equivalent by design,
    not identical infrastructure" — is that good enough?)*
11. Docker compose, both Dockerfiles, Terraform and the GitLab pipeline are
    statically reviewed but have **never been run**. Which do you smoke-test
    first, and what's your bet on what fails? *(Turns "unverified" into a
    prioritized checklist; .gitlab-ci.yml, infra/terraform README caveats.)*

## C. Performance & scale (TBD-07 — adopted but never load-tested)

12. Our targets were adopted as test thresholds but never measured: 500 ms
    validation, 2 s market-order end-to-end, 200 concurrent users. Given
    the actual architecture (in-process workers, WS broadcast fan-out,
    single process), which number do you expect us to miss first? *(TBD-07;
    suggests the minimal load test worth running before the demo.)*
13. The WebSocket fan-out broadcasts every tick (5 bars/s × 7 symbols) to
    every connected client, single-process. At what client count would you
    worry, and would you throttle, batch, or shard by symbol? *(design 22;
    NFR-SCL-001 — the ConnectionManager is deliberately single-instance.)*
14. Permission resolution is cached 60 s in-process, so revoking a grant can
    take up to a minute to bite. The SRS wanted ~60 s revocation — is that
    genuinely acceptable to security, or should grant changes push
    invalidation? *(FR-IAM-005/FR-JIT-002; `invalidate_permissions` exists
    but is best-effort across instances.)*

## D. Security & compliance open items

15. `DEV_AUTH=true` ships passwordless login as any user and it's the
    default. The SSO/OIDC integration (TBD-05) was never built. Where's the
    honest line between "training convenience" and "teaches bad security" —
    and what would you want before anyone outside the program touches it?
    *(TBD-05; D-04/D-05.)*
16. The WebSocket authenticates via `?token=` in the URL — browsers can't
    set headers on WS handshakes. Tokens can end up in proxy/access logs.
    Acceptable with short-lived sessions, or should it move to a
    first-message auth frame? *(design 22; nginx/access-log hygiene.)*
17. The audit trail is hash-chained and append-only, but anchored nowhere —
    the chain head lives in the same DB it protects. Is that credible
    tamper-evidence for you, or does it need external anchoring / WORM
    export? *(TBD-08 — still open; FR-AUD-001.)*
18. Break-glass is 4 h with a 24 h review SLA, JIT caps are 8 h/90 d, SoD
    matrix is two guessed pairs. These defaults were never confirmed by
    security — which would you push back on? *(TBD-02/03/15 — carry to the
    security stakeholder if Nora can't sign off.)*

## E. Testing, operability, handover

19. 87 backend tests, zero frontend tests, no load test, one
    wall-clock-flaky test already found and fixed (a fixture that broke
    00:00–02:00 UTC). What's the smallest test investment that would make
    you trust this codebase? *(NFR-MNT; test_experience.py news fixture.)*
20. The dev loop now works on Windows and macOS, but the Makefile and
    `dev.sh postgre` are macOS-only, and PostgreSQL-on-Windows dev was never
    set up. Does that matter for handover, or is "one blessed dev platform"
    acceptable? *(dev.sh Windows fix; Makefile `.venv/bin` paths.)*
21. Round 1 you said you'd demand tests, docs, deployment runbooks and code
    structure from us. Skim the repo now (DESIGN.md, docs/design/01–24,
    AGENTS.md, README demo script) — what's missing that you'd still block
    on? *(Her own acceptance bar, revisited — the handover slide.)*
22. What monitoring would you want if this ran beyond the demo — is the
    `/api/v1/health` probe + audit trail + structured logs enough, or do
    you need metrics/tracing before you'd take it? *(admin health tiles
    exist; no metrics pipeline.)*

## F. Phase-2 technical direction

23. There's a `NewsProvider` seam with an Alpha Vantage live provider built
    but disabled (no key, fetch-on-demand, 503 unconfigured). The SRS
    forbids live *market data* — does live *news* violate the spirit of the
    training environment, or is it the right first external integration?
    *(design 21 §A6; the business asked to see this.)*
24. The GenAI assistant is rule-based with a provider seam; a real LLM would
    draft prose only, with deterministic intents, tools and the trade
    guardrail unchanged. As the resident skeptic: is that guardrail strong
    enough, and what would you test before letting an LLM near it?
    *(C-08/FR-AI-003; her round-1 concern revisited with a concrete seam.)*
25. If this went multi-instance (2+ API processes behind a LB), the known
    single-process assumptions are: WS ConnectionManager, in-memory session
    store, alert cache, permission cache, InProcessBus. Which do you fix
    first, and is the Redis path (sessions + Streams) we reserved enough?
    *(NFR-SCL-001; RedisBus/RedisSessionStore exist but are untested.)*
26. What are we still underestimating? *(Open floor — the round-1 closer,
    worth re-asking now that the system exists.)*

---

## 7. Playback — our proposals for her to attack

Read these out in the last 10 minutes; "what's wrong with this?" is the
question:

1. Keep `create_all` + additive DDL for the program; Alembic as the first
   phase-2 task. *(B6.)*
2. Leave notification prefs + alert cache in-memory; persist sessions to
   Redis only when deploying multi-instance. *(B7, F25.)*
3. No slippage/liquidity model in MVP; document it as a stated
   simplification; add only if a trader stakeholder complains. *(A2/TBD-14.)*
4. Smoke-test compose first (one `docker compose up` on any machine), fix
   what breaks, then trust CI to keep it green — Terraform stays unverified
   until a cloud decision lands (TBD-11). *(B11.)*
5. Run exactly one load test before the final demo: 200 concurrent WS
   clients + 1 order/5 s, record p95s against the TBD-07 targets. *(C12/13.)*
6. Move WS auth from query param to first-message auth only if security
   objects; otherwise document the query-param decision. *(D16.)*

---

## 8. Outcome log (fill after the interview)

| # | Decision / answer | Owner | Flips (TBD/D/FR) |
|---|---|---|---|
| A2 | | | TBD-14 |
| B6 | | | core/db.py TODO |
| B11 | | | deployment verification plan |
| C12 | | | TBD-07 |
| D15 | | | TBD-05 / D-04 |
| D17 | | | TBD-08 |
| D18 | | | TBD-02/03/15 |
| F23 | | | NEWS_PROVIDER default |
| F25 | | | NFR-SCL-001 roadmap |

**After the interview:** update DESIGN.md §7 for any TBDs resolved here,
note outcome highlights in `docs/interview_outcome.md`, and add a dated
CHANGELOG.md entry for any decision that changes scope.

---

*Owner: technology-analyst team. Changes via merge request.*
