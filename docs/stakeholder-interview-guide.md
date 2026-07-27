# Stakeholder Interview Guide — Next-Generation Trading Platform (STP)

| | |
|---|---|
| **Document ID** | INT-STP-2026-001 |
| **Version** | 1.0 — Draft |
| **Date** | 26 July 2026 |
| **Source basis** | Project Brief (stakeholder voices); SRS-STP-2026-001 §9 TBD register; DESIGN.md §7 open items |

Purpose: prepare the team to interview the platform's stakeholders during week 1.
Each interview is scoped to resolve specific open questions from the SRS TBD
register (column "Resolves") and to validate the assumptions the design has
provisionally adopted (marked `[P]` in DESIGN.md). Questions are open-ended on
purpose — the goal is to hear the stakeholder's workflow and constraints in
their own words before confirming our proposals.

**How to run each interview (suggested, 45 min):**

1. 5 min — introduce the project, confirm what the MVP is and is not (SRS §1.2).
2. 25 min — the questions below, in order; skip any the stakeholder already answered.
3. 10 min — play back what you heard; read out our proposed defaults and ask "what's wrong with this?"
4. 5 min — agree on decisions, owners and any follow-up; log outcomes into the TBD register.

---

## 1. Rohan Singh — Head of Product Development

*Context: wants single-click trading, fast processing, KPIs, reports, charts,
"speak the language of our users". Primary owner of product-scope TBDs.*

**Theme: MVP scope and definition of success**

1. When you picture the week-3 demo, what is the one moment that makes you say "this was worth building"?
2. Of the five core modules (order execution, portfolio management, reporting & charting, technical analytics, paper trading), which two must be flawless, and which can be rough? *(Validates our MoSCoW sequencing in DESIGN.md §8 delivery plan.)*
3. "Single click" — literally one click from where? Watchlist, chart, or order ticket with one confirmation? What confirmation, if any, is acceptable before an order is live? *(Drives the order-ticket UX, FR-ORD-001.)*
Yes, confirmation, details


**Theme: products and orders** — *resolves TBD-17, TBD-18*

4. Which asset classes do your clients actually trade day to day? Is an equities-only MVP credible to you? *(TBD-17 — design currently assumes equities only.)*
basics, plus, user interface
5. Beyond MARKET and LIMIT, which order types do traders refuse to live without — stop, stop-limit, iceberg? *(TBD-18.)*
yes
6. Are there order restrictions we must enforce up front (restricted lists, per-desk limits, max notional per order)? *(Extends FR-ORD-002 pre-trade validation rules.)*
yes


**Theme: reports, KPIs and the GenAI roadmap**

7. Which three KPIs do you personally check first each morning? What would you want on the dashboard that Excel can't give you today? *(FR-PFM-003, FR-RPT-001.)*

8. Are scheduled/delivered reports (daily/weekly email) genuinely valuable, or is on-demand enough for now? *(TBD-13 — is FR-RPT-004 in MVP scope?)*
9. Where do you see GenAI adding real value in year one — client Q&A, report writing, trade ideas? And where would you explicitly *not* want it? *(Validates the advisory-only guardrail, FR-AI-003 / C-08.)*
10. After the program, what would "phase 2" look like in your ideal world — live market connectivity, more products, more analytics? *(Shapes the future-roadmap slide in the final presentation, deliverable 3.)*
yes, link to outer system now, potential for future improvement
---

## 2. Tom Atkins / Patricia Bose — Clients (customer persona)

*Context: "current way is quite good… I do not want to learn another system…
viewing data through Excel is quite a task… a dashboard tracking what I am
interested in." Interview as a pair if possible; keep it non-technical.*

**Theme: today's workflow (understand before replacing)**

1. Walk us through the last trade you made — from deciding to getting confirmation. Who did what, in which tools? *(Reveals what "the commands I need to execute a trade" actually are — the client's mental model we must not break, SRS §2.3.)*
2. What do you ask your secretary to do for you, and what do you insist on doing yourself?
3. Show us the Excel you currently get. Which columns do you actually read? Which tab would you keep if you could keep only one? *(Directly specifies the dashboard widgets and holdings report, FR-RPT-001/003.)*

**Theme: the dashboard that replaces Excel**

4. If the platform showed you one screen, what five numbers must be on it? *(Validates: total value, day change, allocation, watchlist, recent transactions.)*
5. How often do you want it to refresh — live, every few seconds, end of day? Is a 5-second delay acceptable? *(Validates NFR-PER-004.)*
6. What would make you distrust the numbers on screen? What would you cross-check? *(Important for adoption; may surface stale-price indicator requirements, FR-PFM-001 E1.)*
7. Do you ever want to *do* anything from the dashboard — place an order, set an alert — or is it strictly "show me, don't make me learn a tool"? *(Confirms the Client role stays read-only per the §2.3 matrix.)*

**Theme: reporting and language**

8. What currency and timezone should your statements use by default? *(TBD-16.)*
9. Would you read a plain-English monthly summary written by the system if the figures came from your actual data? Any wording that would worry you? *(FR-AI-002, advisory-only positioning.)*
10. What would make you recommend *against* rolling this out to other clients? *(Inverted question — surfaces objections early.)*

---

## 3. Nora Smith — Tech Developer (incumbent system)

*Context: skeptical — "real-time is doable but challenging… current batch
system is a handful to maintain… why rebuild what we have?" She knows where
the bodies are buried. Goal: harvest operational knowledge and address her
concerns, not sell the project.*

**Theme: the incumbent system and its data**

1. How does the current batch system actually work — where do trades come in, what breaks most often, and what do you dread touching? *(Operational evidence for deliverable 2 workflows; also warns us what not to replicate.)*
2. Where does instrument/reference data live today, and how bad is it? What would we find if we loaded it naively? *(Feeds the market-data loader design, docs/design/01.)*
3. What's actually in the simulation dataset (data.zip) — instruments, history depth, granularity, gaps, corporate actions? Any known quirks? *(TBD-06 — the single biggest technical unknown of week 1.)*

**Theme: feasibility and performance realism**

4. You said immediate execution is "doable but challenging" — what specifically is the challenging part for us in three weeks? *(Let her critique; compare with our in-process worker design, DESIGN.md §4.)*
5. Are our proposed performance targets sane — 500 ms validation, 2 s market-order end-to-end, 3 s dashboard, 200 concurrent users? Which would you tighten or drop, and why? *(TBD-07.)*
6. You mentioned sentiment-driven pricing needs "a huge amount of data". If GenAI in this MVP is advisory-only summaries and Q&A over the user's own data — never autonomous trading — does that address your concern, or do you still see risks? *(C-08 / FR-AI-003 — an honest skeptic is the best guardrail reviewer.)*
7. If this platform had to be maintained by your team after the program, what would you demand of us now — tests, docs, deployment runbooks, code structure? *(NFR-MNT; feeds the handover section of the final presentation.)*
8. What is the one thing you think we're underestimating? *(Open floor — always ask the skeptic this.)*

---

## 4. Roy — CTO

*Context: champion of the project; wants DevSecOps built in — "security,
operations, SRE… robust, user friendly and resilient"; needs CFO buy-in.
Owner of platform/security TBDs.*

**Theme: cloud and environments**

1. AWS or Azure — which one, which region, and do we have a subscription with quota for the program? Any landing-zone policies we must follow (tagging, allowed services, network ranges)? *(TBD-11; unblocks Terraform work immediately.)*
2. What environments do you expect (dev, demo, prod-like?) and who owns the demo environment at the end? *(NFR-MNT-002.)*
3. Are there resilience expectations we should design for even in training — restart behavior, backup, RTO/RPO figures you'd quote to the CFO? *(NFR-AVL-003.)*

**Theme: security, identity and privileged access**

4. What IdP do we federate with — OIDC or SAML — and is MFA available in the training tenant? What should happen to logins when the IdP is down: fail closed? *(TBD-05, TBD-12; design proposes OIDC + fail-closed.)*
5. CyberArk: which environment do we get — PVWA URL, app authentication method, test safes, is PSM recording available? Who provisions our test accounts? *(TBD-04.)*
6. What are the house rules for privileged access duration and break-glass? Our proposals: 8 h privileged / 90-day standard grants, 4 h break-glass with 24 h review SLA — do security sign these off, or what would they change? *(TBD-02, TBD-03.)*
7. Is there an existing segregation-of-duties policy we should encode — which role combinations are actually forbidden at Nomura? *(TBD-15; we seeded Trader↔Ops and Trader↔SecAdmin as a guess.)*
Risk to see, traders want see 
8. How long must audit and trade records be retained, and is hash-chaining an acceptable tamper-evidence mechanism, or is there a standard (WORM store, immutable object storage) we should use? *(TBD-08, NFR-CMP-001/002.)*
9. What does "CFO buy-in" need to see — cost estimate, risk controls, a security story, a demo? Can you help us get 30 minutes with the CFO? *(Opens interview #5.)*

---

## 5. CFO — (via Roy's introduction; buy-in stakeholder)

*Context: not in the brief directly, but Roy explicitly needs their buy-in.
Keep it short, business-framed, no jargon.*

1. What does a successful graduate-program project look like to you — what would make this worth its cost?
2. What are your top concerns about a platform that executes trades, even simulated ones — operational risk, compliance exposure, data leakage? Which one keeps you up at night? *(Answers become risk slides in the final presentation and may add compliance requirements, NFR-CMP.)*
3. Is there a cloud-spend ceiling for the program we should design to? *(Sizes the Terraform reference deployment — t3.medium single VM is our current assumption.)*
4. What reporting or audit evidence would you personally want to see before endorsing a real version — who traded what, who had access, who approved it? *(Validates FR-AUD, FR-ADM-003 recertification export.)*
5. If phase 2 went to production with real money, what controls would be non-negotiable for you? *(Feeds the roadmap; shows we're thinking beyond the MVP.)*

---

## 6. Corporate analysts + facilitators / Security & Ops SMEs (working session)

*Context: the corporate analysts own the operational workflow deliverable
(trade settlement, risk, system access); facilitators can resolve environment
TBDs. Run as a joint working session in week 1.*

1. Approval chains: who should approve what, per role? Our default: line manager → resource owner, + security officer for privileged roles. Draw the real chain for Trader, Operations Analyst, System Administrator. *(TBD-01.)*
2. Walk the trade settlement lifecycle as it should work here — what does "affirm" and "settle" mean in our simulated world, and what evidence would prove STP end-to-end? *(NFR-CMP-004, deliverable 2.)*
3. What are realistic settlement delays and retry behaviors we should simulate? *(FR-ORD-005 simulated delay TBD — design uses a 5 s demo default.)*
4. Notification policy: which events deserve email vs in-app only, what retry/lead times, and who must never be able to unsubscribe? *(TBD-09.)*
5. Environment check: can you confirm access to — the simulation dataset, the IdP/SSO sandbox, the CyberArk training instance, an SMTP relay, cloud subscriptions? Who unblocks each? *(A-01…A-07 assumptions — convert to confirmed or escalate.)*
6. Presentation logistics: what milestone feedback sessions exist, what format does the final presentation take, and who assesses it? *(Deliverable 3 planning.)*

---

## Consolidated TBD coverage map

Every SRS open question is assigned to exactly one interview above:

| TBD | Topic | Interview |
|---|---|---|
| TBD-01 | Approval chains | §6 Corporate/SME session |
| TBD-02 | JIT duration policy | §4 Roy |
| TBD-03 | Break-glass policy | §4 Roy |
| TBD-04 | CyberArk environment | §4 Roy |
| TBD-05 | Directory and SSO | §4 Roy |
| TBD-06 | Simulation dataset | §3 Nora |
| TBD-07 | Performance targets | §3 Nora |
| TBD-08 | Audit integrity & retention | §4 Roy |
| TBD-09 | Notification policy | §6 Corporate/SME session |
| TBD-10 | Database engine | §4 Roy (confirm PostgreSQL with cloud choice) |
| TBD-11 | Cloud provider | §4 Roy |
| TBD-12 | MFA / step-up auth | §4 Roy |
| TBD-13 | Report scheduling scope | §1 Rohan |
| TBD-14 | Paper-trading realism | §3 Nora (validate slippage model) |
| TBD-15 | SoD conflict matrix | §4 Roy |
| TBD-16 | Display defaults | §2 Clients |
| TBD-17 | Instrument scope | §1 Rohan |
| TBD-18 | Order types | §1 Rohan |

**After the interviews:** update SRS §9 with the decisions, flip the `[P]`
proposals in DESIGN.md §7 to confirmed or changed, and re-check any design
assumption that an interview invalidated (most likely candidates: approval
chains, SoD matrix, dataset quirks, cloud provider).

---

*Owner: business-analysis pairing (technology + corporate analysts). Changes via merge request.*
