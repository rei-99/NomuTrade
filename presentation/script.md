# Final Team Presentation — Script & Flow

**Project:** Next-Generation Trading Platform with Straight-Through Processing (STP)
**Program:** Nomura Tech Graduate Program 2026
**Total time:** 15 min presentation + 5 min Q&A
**Audience:** program facilitators, business stakeholders, fellow graduates (mixed business/technical)
**Presenters (5):** P1 corporate lead · P2 technology · P3 corporate (operations) · P4 technology · P5 corporate
*(replace P1–P5 with names; split tech/corporate as marked)*
**Demo:** live on the simulation dataset, recorded-video fallback (see §10)

> Grounding note: every claim below is traceable to the repo — README, DESIGN.md,
> docs/design/01–24, docs/interview_outcome.md, CHANGELOG.md, git history
> (28 commits, 9 merges, 7 feature branches), `.gitlab-ci.yml`, Dockerfiles,
> `infra/terraform/`. Where functionality is partial or mocked, the script says
> so out loud — honesty is a feature of this talk, not a weakness.

---

## 1. Minute-by-minute flow

| # | Section | Slides | Presenter | Time | Purpose |
|---|---|---|---|---|---|
| 1 | Hook: four stakeholder voices | 1–3 | P1 (corp) | 0:00–1:00 | Frame the whole talk as answering four people |
| 2 | What we built + demo | 4–8 | P2 (tech) | 1:00–5:00 | Platform + GenAI + 90-sec live demo (45%) |
| 3 | Operational processes | 9 | P3 (corp) | 5:00–7:00 | Settlement / risk / system access, end-to-end STP |
| 4 | How we worked | 10–12 | P4 (tech) | 7:00–10:30 | Agile rhythm, git flow, CI/CD, cross-functional |
| 5 | What worked / what didn't | 13–14 | P5 (corp) | 10:30–12:30 | Honest retrospective with receipts |
| 6 | What we learned | 15 | P1 (corp) | 12:30–14:00 | Technical / domain / professional |
| 7 | Roadmap & close | 16–17 | P3 (corp) | 14:00–15:00 | Answer the four stakeholders again |
| 8 | Q&A | 18 | all | 15:00–20:00 | Appendix slides A1–A3 on standby |

---

## 2. Section 1 — Hook (P1, 0:00–1:00, slides 1–3)

**[Slide 1: Title]** Good morning. We're [team name], and in three weeks we built a
working trading platform with straight-through processing — an order goes from
ticket to settlement with zero manual steps. But we didn't start with
technology. We started with four people.

**[Slide 2: Agenda]** Quick roadmap of the next fifteen minutes: what we built,
how it supports real operational workflows, how we worked as a cross-functional
team, what worked and what didn't, and where this goes next.

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

## 3. Section 2 — What we built (P2, 1:00–5:00, slides 4–8)

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
pipeline as real trading, and a GenAI assistant.

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
straight-through processing. *(See §10 for the full checklist and fallback.)*

*Handover: "A fill is where the technology story ends — but it's where the
operations story begins. [P3]."*

---

## 4. Section 3 — Operational processes (P3, 5:00–7:00, slide 9)

**[Slide 9: Operational processes]** The corporate side of the team mapped
three workflows, and the platform implements all three.

**Trade settlement.** What you just watched: execution creates a settlement
instruction that moves EXECUTED → AFFIRMED → SETTLED automatically. Exceptions —
and in trading, exceptions are the job — surface on the operations dashboard
for the Operations Analyst, with audit on every transition. The same pipeline
serves client books, house books and paper accounts — paper trading isn't a
toy mode, it's the same code path with a portfolio type flag.

**Risk management.** Pre-trade, every order passes validation: cash, lot size,
a configurable max notional, and a restricted-instrument list that our Security
Administrator manages — restricted orders reject with a reason, and the
rejection is audited. Post-trade, the risk panel computes concentration,
volatility and top holdings live from positions.

**System access.** Nobody gets anything by default. Roles grant permissions;
just-in-time grants expire automatically; privileged actions go through a
CyberArk-style checkout (mocked adapter — the interface is real, the vault is
the training stand-in); and break-glass access is time-boxed to four hours with
a 24-hour review SLA. Every denial, grant and activation is written to a
hash-chained, append-only audit trail — tamper-evident by construction, and the
auditor role can search and export it.

That's the STP story end to end: the business workflow and the technical
workflow are the *same* workflow.

*Handover: "Building that in three weeks took a way of working, not just code —
[P4]."*

---

## 5. Section 4 — How we worked (P4, 7:00–10:30, slides 10–12)

**[Slide 10: 3-week Agile timeline]** Three one-week sprints. Week 1: walking
skeleton plus the governance spine — auth, RBAC, audit, the access-request
lifecycle, and the dataset loader. Week 2: the trading core — order ticket,
execution engine, STP settlement, portfolios, charts, notifications. Week 3:
breadth and hardening — paper trading, reports, analytics, admin dashboards,
and the GenAI assistant. Daily facilitator touchpoints; structured feedback
after each milestone — and we changed course because of it. The product-owner
feedback round after the trading demo produced four concrete changes you saw:
the two-click confirmation, bonds, stop orders, and order restrictions. That
feedback is in the repo as interview notes and a design document.

**[Slide 11: Engineering practices]** Design before code: twenty-four numbered
design documents, one per module or major feature, each traced to SRS
requirement IDs. Git flow: feature branches off an integration branch — you can
see nine merges and seven feature branches in the history — with a dated
changelog entry per milestone. Every milestone was verified the same way: the
full test suite, the strict TypeScript build, an end-to-end walk of the real
stack, and headless-browser screenshots for UI changes — verified by a second
pair of eyes, never trusted on the author's word. CI/CD is defined as a
six-stage GitLab pipeline — lint, test, security scan, build, deploy-dev,
deploy-demo — with Docker images for both tiers and Terraform for a single-VM
cloud deployment. Honest caveat: the pipeline, containers and Terraform are
written and statically reviewed, but no cloud was available in the program
environment — they have not been executed. That stays on the roadmap slide.

**[Slide 12: Cross-functional collaboration]** Day to day this meant: corporate
analysts owned the SRS traceability and the stakeholder interviews — eighteen
open questions, each assigned to a stakeholder — and technology owned the
build. The product-owner interview is the clearest joint artifact: the business
said "equities only is not credible," and two days later the platform priced
bonds properly, quoted as percent of par with correct cash math. One real
misalignment: "single-click trading" — technology heard *one* click, the
business meant *one informed decision*. The resolution was the two-click flow:
one click to arm, a confirmation card with the full cost impact, one click to
submit. Faster than a ticket, safer than a blind click.

*Handover: "That's how we worked. What actually held up — and what didn't —
[P5]."*

---

## 6. Section 5 — What worked / what didn't (P5, 10:30–12:30, slides 13–14)

**[Slide 13: Challenges & solutions]** Three war stories, all real.
- **The dataset that silently didn't load.** After switching to the real
  dataset, every stock showed "No price data." Root cause: legacy rows from an
  old dev database made the loader's global "is the table empty?" check skip
  the entire tick load. Found by inspecting the database, not by guessing;
  fixed by loading per symbol, with a regression test.
- **The test that only failed after midnight.** A news fixture used
  relative-to-now timestamps; between midnight and 2 a.m. UTC the two items
  straddled a day boundary and the suite went red. It only ever passed because
  of the hour it ran. Now anchored deterministically — and it made us re-audit
  every time-sensitive test.
- **Real-time wasn't a library import.** Cancellations mid-database-call could
  wedge the event loop, and naive datetimes compared *silently* wrong against
  timezone-aware ones. Both are now documented pitfalls with fixes and tests
  behind them.

**[Slide 14: What worked / what didn't]** Worked: design-first — 24 design docs
meant feedback rounds changed documents before they changed code. The event
pipeline — the STP flow never needed a manual hack. Verification discipline —
87 tests, a strict build, and screenshot-verified UI rounds. What didn't: we
have **no frontend tests** — UI quality rests on build strictness and manual
verification. The deployment stack is unexecuted — no cloud in the program
environment. And the GenAI reality check: a rule-based assistant is robust and
honest, but it is not a language model — the summary quality ceiling is real,
and that's a phase-2 decision, not a bug.

*Handover: "So what did three weeks teach us — [P1]."*

---

## 7. Section 6 — What we learned (P1, 12:30–14:00, slide 15)

**[Slide 15: Key learnings]**
- **Technical:** event-driven design pays for itself the first time a
  requirement changes mid-project — trailing stops and report scheduling both
  slotted into the existing pipeline. And GenAI in production is a guardrail
  problem before it's a model problem.
- **Domain:** settlement is a workflow, not a status field — the corporate
  mapping of affirm/settle semantics is what made the STP demo credible. And
  "a bond is not a stock with a different symbol" — percent-of-par quoting,
  yield and duration all had to be first-class.
- **Professional:** the stakeholder-voices framing changed how we built — every
  feature traces to a person, and that made prioritization arguments short.
  And honest status beats impressive status: facilitators know what three
  weeks allows.

*Handover: "Which brings us back to the four people we started with — [P3]."*

---

## 8. Section 7 — Roadmap & close (P3, 14:00–15:00, slides 16–17)

**[Slide 16: Metrics — honest numbers]** 28 commits on the integration branch,
9 merges, 7 feature branches. 87 backend tests, all green. 16 backend modules,
24 design documents, a dated changelog for every milestone. 11 instruments,
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
  24 design docs and a runbook — we built *with* her skepticism, not against it.
- **Rohan** — cost/benefit for the CFO: iceberg orders and per-desk limits are
  designed but deferred; the roadmap is sequenced by business value, and the
  demo you saw runs on a laptop.

**[Slide 18: Thank you / Q&A]** We set out to answer four people: one-click
trading that's still an informed decision; a dashboard that replaces Excel
without teaching a new system; a rebuild that respects what the batch system
knows; and DevSecOps that was day-one, not day-twenty. Thank you — questions?

---

## 9. Q&A prep — 10 likely questions

1. **"What's the ROI of rebuilding?"** The batch system costs manual touches
   per trade and spreadsheet reconciliation; the demo showed zero manual steps
   from ticket to settlement. Phase-2 ROI case: exception volume, settlement
   lag, and audit-prep time are all measurable on the platform itself.
2. **(Nora, hostile) "Why not keep the existing batch system?"** Batch can't
   offer live marks, intraday risk, or instant settlement evidence — but the
   rebuild keeps its operational knowledge: the same settlement states, the
   same exception workflow, documented with her in mind.
3. **"Your login is passwordless — how is that security?"** It isn't, and we
   say so: dev-login is a training-only flag. The real model is server-side
   sessions, deny-by-default RBAC re-checked per request, hash-chained audit —
   and SSO via OIDC is the designed integration point, not an afterthought.
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
status FILLED → settlement instruction → SETTLED.

**Checklist (T-30 min):**
- [ ] `./dev.sh sqlite` running; API health 200; UI loads
- [ ] Fresh dev DB seeded (`backend/stp.db` present; re-seed if stale)
- [ ] Dev-login as `trader@demo.nomura` works; workspace shows tape + chart
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
| Order Execution & STP | Full | MARKET/LIMIT/STOP/STOP_LIMIT + TIF + TRAILING_STOP; whole fills only; settles automatically |
| Portfolio Management | Full | Positions, valuation, KPIs, live push; day-change marks |
| Reporting & Charting | Full | ECharts candles + indicators; PDF/CSV on-demand + scheduled reports |
| Technical Analytics | Full | SMA/EMA/RSI/MACD/Bollinger; price alerts with evaluator |
| Paper Trading | Full | Same pipeline, `PAPER` portfolio type |
| GenAI assistant | Partial (by design) | Rule-based, `mock: true`, LLM seam ready; advisory-only guardrail enforced |
| CyberArk / LDAP / SMTP | Mocked adapters | Interfaces real; training stand-ins behind them |
| SSO/OIDC | Not built | DEV_AUTH only; D-04 seam documented |
| CI/CD, Docker, Terraform | Written, unexecuted | Statically reviewed; zero runs (no cloud in program env) |
