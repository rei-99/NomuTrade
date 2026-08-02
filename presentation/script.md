# Final Team Presentation — Script & Flow

**Project:** Next-Generation Trading Platform with Straight-Through Processing (STP)
**Program:** Nomura Tech Graduate Program 2026
**Total time:** 10 min presentation/demo + 5 min Q&A (15-minute slot)
**Audience:** program facilitators, business stakeholders, fellow graduates (mixed business/technical)
**Presenters (4):** P1 corporate lead · P2 technology · P3 corporate (operations) · P4 technology
*(replace P1–P4 with names; split tech/corporate as marked)*
**Demo:** live on the simulation dataset, recorded-video fallback (see §10)

> Grounding note: every claim below is traceable to the repo — README, DESIGN.md,
> docs/design/01–26, docs/interview_outcome.md, CHANGELOG.md, git history
> (42 commits, 14 merges, 12 feature branches), `.gitlab-ci.yml`, Dockerfiles,
> `infra/terraform/`. Where functionality is partial or mocked, the script says
> so out loud — honesty is a feature of this talk, not a weakness.

---

## 1. Minute-by-minute flow

| # | Section | Slides | Presenter | Time | Purpose |
|---|---|---|---|---|---|
| 1 | Hook: four stakeholder voices | 1–3 | P1 (corp) | 0:00–0:45 | Frame the whole talk as answering four people |
| 2 | What we built + demo | 4–8 | P2 (tech) | 0:45–4:15 | Platform + GenAI + 90-sec live demo (35%) |
| 3 | Operational processes | 9 | P3 (corp) | 4:15–5:45 | Settlement / risk / system access, end-to-end STP |
| 4 | How we worked | 10–12 | P4 (tech) | 5:45–7:45 | Agile rhythm, engineering practices, cross-functional |
| 5 | What worked / what didn't | 13–14 | P1 (corp) | 7:45–9:00 | One war story + honest retrospective with receipts |
| 6 | Learnings, roadmap & close | 15–17 | P3 (corp) | 9:00–10:00 | Compressed learnings; answer the four stakeholders again |
| 7 | Q&A | 18 | all | 10:00–15:00 | Appendix slides A1–A3 on standby |

---

## 2. Section 1 — Hook (P1, 0:00–0:45, slides 1–3)

**[Slide 1: Title]** Good morning. We're [team name], and in three weeks we built a
working trading platform with straight-through processing — an order goes from
ticket to settlement with zero manual steps. But we didn't start with
technology. We started with four people.

**[Slide 2: Agenda]** Quick roadmap of the next ten minutes: what we built —
including a live 90-second demo — how it supports real operational workflows,
how we worked as a cross-functional team, what held up and what didn't, and
where this goes next.

**[Slide 3: The ask — four voices]**
- **Rohan**, Head of Product Development: "I want single-click trading."
- **Tom and Patricia**, our clients: "The current way is fine — don't make me
  learn another system. And stop sending me Excel."
- **Nora**, the developer who maintains today's batch system: "Why rebuild what
  we already have? Real-time is doable, but challenging."
- **Roy**, our CTO: "Security, operations, SRE — DevSecOps from day one, or
  don't build it at all."

Everything you're about to see is our answer to those four sentences. Hold us
to it at the end.

*Handover: "To show you what that answer looks like — [P2]."*

---

## 3. Section 2 — What we built (P2, 0:45–4:15, slides 4–8)

*Pace: slides 4–7 in ~2:00, then the 90-second demo.*

**[Slide 4: Goals & success criteria]** The brief asked for five core modules —
Order Execution, Portfolio Management, Reporting & Charting, Technical
Analytics, Paper Trading — on a DevSecOps foundation, using the program's
simulation dataset. Our success bar: an order settles with no manual step, and
every action is authorized and auditable.

**[Slide 5: Platform overview]** All five modules are live, plus the governance
spine around them — RBAC, just-in-time access, break-glass, and a hash-chained
audit trail. Eleven instruments: the seven dataset equities plus four bonds the
business explicitly asked for. Five order types plus time-in-force and trailing
stops. Price alerts, scheduled PDF/CSV reports, paper trading on the same
pipeline as real trading, and a GenAI assistant. The UI speaks English and
Japanese, reshapes itself into four role-faithful personas, and sign-in is
real password login with per-email lockout.

**[Slide 6: Architecture]** One diagram. A React single-page app talks REST and
WebSocket to a FastAPI modular monolith — sixteen modules, one deployable.
Inside, an order flows through a transactional outbox to the execution engine,
the STP worker, and the settlement sweeper — asynchronous, event-driven, and
idempotent. Market data is the program's dataset — about 190,000 price bars and
9,300 news items — replayed on a *simulation clock*, so the platform runs in
market time, roughly a day every 78 seconds. Nothing in the system knows the
data is simulated.

**[Slide 7: GenAI — what it concretely does]** Two honest things. First, the
assistant answers questions *grounded in your actual data* — positions, KPIs,
news — with citations, and it refuses to invent figures it doesn't have.
Second, the news panel summarizes real coverage for the selected instrument
with a sentiment score and headline citations. And the guardrail: the assistant
is **advisory only** — it can suggest a trade, but the suggestion always lands
in the standard order ticket, with the same validation and the same two-click
confirmation as any other order. Today it's a rule-based engine with a clean
LLM seam — the interface is ready for a real model, and the responses are
honestly marked `mock: true`.

**[Slide 8: Demo framing]** Ninety seconds, live. Watch three things:
**(1)** I buy 50 TSLA from the workspace — one click, a confirmation card with
full cost impact, second click. **(2)** The position marks live over the
WebSocket push — no refresh — and the risk panel reacts. **(3)** With no manual
step, the settlement instruction transitions to *settled* — that is
straight-through processing. The settlement lifecycle is visible in the UI:
the settlements list shows every state, and ops can retry exceptions.
*(See §10 for the full checklist and fallback.)*

*Handover: "A fill is where the technology story ends — but it's where the
operations story begins. [P3]."*

---

## 4. Section 3 — Operational processes (P3, 4:15–5:45, slide 9)

**[Slide 9: Operational processes]** The corporate side of the team mapped
three workflows, and the platform implements all three.

**Trade settlement.** What you just watched: execution creates a settlement
instruction that moves EXECUTED → AFFIRMED → SETTLED automatically — every
state visible in the settlements list. Exceptions — and in trading, exceptions
are the job — surface on the operations dashboard, with audit on every
transition, and ops can retry an exception from the same dashboard. The same
pipeline serves client, house and paper books — paper trading isn't a toy mode,
it's the same code path with a portfolio type flag.

**Risk management.** Pre-trade, every order passes validation: cash, lot size,
a configurable max notional, and a restricted-instrument list managed by our
Security Administrator — restricted orders reject with an audited reason.
Post-trade, the risk panel computes concentration, volatility and top holdings
live from positions.

**System access.** Nobody gets anything by default. Roles grant permissions;
just-in-time grants expire automatically; privileged actions go through a
CyberArk-style checkout (mocked adapter — the interface is real); break-glass
is time-boxed to four hours with a 24-hour review SLA. Every denial, grant and
activation lands in a hash-chained, append-only audit trail — tamper-evident by
construction, searchable and exportable by the auditor role.

That's the STP story end to end: the business workflow and the technical
workflow are the *same* workflow.

*Handover: "Building that in three weeks took a way of working, not just code —
[P4]."*

---

## 5. Section 4 — How we worked (P4, 5:45–7:45, slides 10–12)

**[Slide 10: 3-week Agile timeline]** Three one-week sprints. Week 1: walking
skeleton plus the governance spine — auth, RBAC, audit, the access-request
lifecycle, the dataset loader. Week 2: the trading core — order ticket,
execution engine, STP settlement, portfolios, charts, notifications. Week 3:
breadth and hardening — paper trading, reports, analytics, admin dashboards,
the GenAI assistant. And feedback changed course: the product-owner review
after the trading demo produced four concrete changes you saw — the two-click
confirmation, bonds, stop orders, order restrictions. That feedback is in the
repo as interview notes and a design document.

**[Slide 11: Engineering practices]** Design before code: twenty-six numbered
design documents, one per module or major feature, each traced to SRS
requirement IDs. Git flow: feature branches off an integration branch —
thirteen merges and twelve feature branches in the history — with a dated
changelog entry per milestone. Every milestone was verified the same way: the
full test suite, the strict TypeScript build, an end-to-end walk of the real
stack, and headless-browser screenshots for UI changes — checked by a second
pair of eyes, never trusted on the author's word. CI/CD is a defined six-stage
GitLab pipeline — lint, test, security scan, build, deploy-dev, deploy-demo —
with Docker images and Terraform for a single-VM cloud deployment. Honest
caveat: written and statically reviewed, but not executed — no cloud in the
program environment. That stays on the roadmap.

**[Slide 12: Cross-functional collaboration]** Corporate analysts owned SRS
traceability and the stakeholder interviews — eighteen open questions, each
assigned to a stakeholder — and technology owned the build. The clearest joint
artifact: the business said "equities only is not credible," and two days later
the platform priced bonds properly, quoted as percent of par with correct cash
math. One real misalignment: "single-click trading" — technology heard *one*
click, the business meant *one informed decision*. The resolution was the
two-click flow: one click to arm, a confirmation card with the full cost
impact, one click to submit. Faster than a ticket, safer than a blind click.

*Handover: "That's how we worked. What actually held up — and what didn't —
[P1]."*

---

## 6. Section 5 — What worked / what didn't (P1, 7:45–9:00, slides 13–14)

**[Slide 13: Challenges & solutions]** One war story, told in forty seconds —
the other two stay on the slide as Q&A ammunition (see §9).
- **The dataset that silently didn't load.** After switching to the real
  dataset, every stock showed "No price data." Root cause: legacy rows from an
  old dev database made the loader's global "is the table empty?" check skip
  the entire tick load. Found by inspecting the database, not by guessing;
  fixed by loading per symbol, with a regression test.
- *(Q&A ammo)* **The test that only failed after midnight.** A news fixture
  used relative-to-now timestamps; between midnight and 2 a.m. UTC the two
  items straddled a day boundary and the suite went red. It only ever passed
  because of the hour it ran. Now anchored deterministically — and it made us
  re-audit every time-sensitive test.
- *(Q&A ammo)* **Real-time wasn't a library import.** Cancellations
  mid-database-call could wedge the event loop, and naive datetimes compared
  *silently* wrong against timezone-aware ones. Both are now documented
  pitfalls with fixes and tests behind them.

**[Slide 14: What worked / what didn't]** Worked: design-first — 26 design docs
meant feedback rounds changed documents before they changed code. The event
pipeline — the STP flow never needed a manual hack. Verification discipline —
~100 backend tests, a strict build, and screenshot-verified UI rounds. What didn't: we
have **no frontend tests** — UI quality rests on build strictness and manual
verification. The deployment stack is unexecuted — no cloud in the program
environment. And the GenAI reality check: a rule-based assistant is robust and
honest, but it is not a language model — the summary quality ceiling is real,
and that's a phase-2 decision, not a bug.

*Handover: "So what did three weeks teach us, and where does it go next —
[P3]."*

---

## 7. Section 6 — Learnings, roadmap & close (P3, 9:00–10:00, slides 15–17)

**[Slide 15: Key learnings]** One line per category.
- **Technical:** event-driven design pays for itself the first time a
  requirement changes mid-project — and GenAI in production is a guardrail
  problem before it's a model problem.
- **Domain:** settlement is a workflow, not a status field — and a bond is not
  a stock with a different symbol.
- **Professional:** every feature tracing to a person made prioritization
  arguments short — and honest status beats impressive status.

**[Slide 16: Metrics — honest numbers]** 42 commits on the integration branch,
14 merges, 12 feature branches. ~100 backend tests, all green. 17 backend modules,
26 design documents, a dated changelog for every milestone. 11 instruments,
~190k price bars and 9.3k news items replayed on the simulation clock. Zero
pipeline runs — the CI/CD is defined and reviewed, not yet executed. That zero
is on the roadmap.

**[Slide 17: Future roadmap — answering the four voices]**
- **Roy** — scalability & DevSecOps: run the pipeline for real; Alembic
  migrations; multi-instance deployment with the Redis session and event-bus
  path that already exists; external audit anchoring.
- **Tom & Patricia** — adoption: keep the dashboard the single screen they
  asked for; live news via the provider seam the business already reviewed;
  change management is a feature, not an afterthought.
- **Nora** — maintainability: the assistant's LLM seam lets a real model in
  without touching the guardrails; the batch system's knowledge is preserved in
  26 design docs and a runbook — we built *with* her skepticism, not against it.
- **Rohan** — cost/benefit for the CFO: iceberg orders and per-desk limits are
  designed but deferred; the roadmap is sequenced by business value, and the
  demo you saw runs on a laptop.

**[Slide 18: Thank you / Q&A]** We set out to answer four people: one-click
trading that's still an informed decision; a dashboard that replaces Excel
without teaching a new system; a rebuild that respects what the batch system
knows; and DevSecOps that was day-one, not day-twenty. Thank you — questions?

---

## 8. Q&A (all, 10:00–15:00, slide 18)

Five minutes — likely 2–3 questions. Better to answer two questions well than
rush five. Whoever owns the topic takes it (technology → P2/P4, operations and
process → P1/P3); the 10 prepared answers in §9 are backup material, and the
two war stories trimmed from §6 are ammunition for any "what was hard / what
went wrong?" question. Appendix slides A1–A3 stay on standby.

---

## 9. Q&A prep — 10 likely questions

*Backup material for the 5-minute Q&A (§8): expect 2–3 questions live; prepare
all ten so any presenter can back up the topic owner.*

1. **"What's the ROI of rebuilding?"** The batch system costs manual touches
   per trade and spreadsheet reconciliation; the demo showed zero manual steps
   from ticket to settlement. Phase-2 ROI case: exception volume, settlement
   lag, and audit-prep time are all measurable on the platform itself.
2. **(Nora, hostile) "Why not keep the existing batch system?"** Batch can't
   offer live marks, intraday risk, or instant settlement evidence — but the
   rebuild keeps its operational knowledge: the same settlement states, the
   same exception workflow, documented with her in mind.
3. **"A shared demo password — how is that security?"** The demo password is
   training data, not the security model. The real path is password login —
   PBKDF2-HMAC-SHA256 (120k iterations) with a per-email lockout after five
   consecutive failures — and the passwordless dev-login exists only behind
   the `DEV_AUTH` flag for tests and tooling; it would be off in any real
   deployment. Around authentication: server-side sessions, deny-by-default
   RBAC re-checked per request, hash-chained audit — and SSO via OIDC is the
   designed integration point, not an afterthought.
4. **"What happens when the GenAI is wrong?"** It can't trade. It's
   advisory-only with citations to your actual data, it declines questions it
   has no data for rather than fabricating, and any suggestion enters the
   standard validated ticket. A wrong summary costs a read, not a trade.
5. **"Why does the SRS forbid live market data — didn't that limit you?"** It
   made the platform *more* credible: the simulation clock gives deterministic,
   repeatable demos and tests. External integration readiness is proven by the
   news-provider seam, which is exactly how a real feed would slot in.
6. **"It runs on one process — how does it scale?"** Deliberately: one
   deployable for a 3-week training MVP. The scaling path is designed, not
   retrofitted: stateless API, Redis sessions and streams already implemented
   behind config flags, module boundaries drawn for a mechanical split.
7. **"Partial fills?"** Out of MVP scope by design — orders fill whole or rest
   working. The execution model (executions as separate records) admits
   partials without a schema change.
8. **"You claim DevOps — what actually runs in CI?"** Today: lint, tests, and
   security scans are defined to block merges; the pipeline file is real. What
   we did not do in the program environment is execute it in GitLab — no cloud
   access — so we replicated the gates locally on every merge.
9. **"If the simulation clock loops, doesn't time go backwards?"** Yes — by
   design, and it's the kind of thing that breaks naive code. Time-sensitive
   logic (order expiry, trailing stops, schedules) was built and tested
   against the loop explicitly.
10. **"What would you do with one more week?"** Run the deployment stack for
    real, add the load test we specified (200 concurrent push clients), and
    frontend tests — in that order.

---

## 10. Demo checklist + fallback plan

**Path (90 s):** workspace → select TSLA → size 50 → BUY → confirmation card →
Confirm → fill toast → position marks live → risk panel reacts → show order
status FILLED → settlement instruction → SETTLED in the settlements list.

**Checklist (T-30 min):**
- [ ] `./dev.sh sqlite` running; API health 200; UI loads
- [ ] Fresh dev DB seeded (`backend/stp.db` present; re-seed if stale)
- [ ] Log in via the form with `trader@demo.nomura` / `demo1234`; workspace shows tape + chart
- [ ] Sim clock advancing (top bar `SIM …` time moves)
- [ ] Desk Book 1 has sufficient cash; no restricted flag on TSLA
- [ ] One rehearsal pass end-to-end, then reset DB if fills accumulated
- [ ] Zoom/second screen: browser at 125–150% for the back row
- [ ] Network-independent: everything is localhost — no venue Wi-Fi dependency

**Fallback:** recorded video of the exact same path (record during rehearsal
with the checklist above). If live fails: switch to video, narrate the same
three beats, and say plainly that the fallback is running — then offer to retry
live during Q&A.

---

## 11. Module honesty map (for internal prep — do not present as a slide)

| Brief module | Status | Say honestly |
|---|---|---|
| Order Execution & STP | Full | MARKET/LIMIT/STOP/STOP_LIMIT + TIF + TRAILING_STOP; whole fills only; settles automatically — lifecycle visible in the UI, ops can retry exceptions |
| Portfolio Management | Full | Positions, valuation, KPIs, live push; day-change marks |
| Reporting & Charting | Full | ECharts candles + indicators; PDF/CSV on-demand + scheduled reports |
| Technical Analytics | Full | SMA/EMA/RSI/MACD/Bollinger; price alerts with evaluator |
| Paper Trading | Full | Same pipeline, `PAPER` portfolio type |
| GenAI assistant | Partial (by design) | Rule-based, `mock: true`, LLM seam ready; advisory-only guardrail enforced |
| CyberArk / LDAP / SMTP | Mocked adapters | Interfaces real; training stand-ins behind them |
| SSO/OIDC | Not built | Password login (PBKDF2 + lockout) + dev-login flag today; D-04 seam documented |
| CI/CD, Docker, Terraform | Written, unexecuted | Statically reviewed; zero runs (no cloud in program env) |
