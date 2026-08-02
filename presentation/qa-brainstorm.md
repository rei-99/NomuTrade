# Q&A brainstorm — likely audience questions (business + tech)

Prepared for the 5-minute Q&A after the 10-minute presentation. Each question
has a short suggested answer; items flagged **[honesty]** are places where the
strongest answer is a candid one (the honesty map in `script.md` §11 backs
these). The 10 deep-dive answers already in `script.md` §9 still apply — this
doc is the wider net, grouped by audience.

---

## Business side

**B1. What problem does this actually solve?**
Operations today run on manual handoffs and end-of-day batches; every manual
touch is a delay and an error source. We show an order going from ticket to
settled with zero manual steps, with the audit trail to prove it.

**B2. Who uses it, and what does each role get?**
Four role-faithful views: traders get a one-screen execution workspace;
operations get the settlement lane, exceptions and integration health; risk
gets book oversight and the audit trail; admins get access governance. Nobody
sees tabs that aren't their job.

**B3. What's the ROI story?**
In production terms: fewer operations touches per trade, fewer settlement
fails, faster exception resolution (ops retry in one click), and a compliance
trail that answers "who did what" in seconds instead of days.

**B4. Is that real market data?** **[honesty]**
No — and deliberately so. It's a provided simulation dataset replayed on a
simulation clock; nothing in the platform knows it's simulated, which is what
makes the demo honest as a system exercise. Live connectivity is out of scope
by requirement (SRS C-04).

**B5. Is the AI assistant real AI?** **[honesty]**
Today it's a rule-based engine grounded in real platform data, badged as a
mock in the UI. The LLM provider seam and the tool whitelist exist; the point
was proving the guardrails (advisory-only, never trades) before connecting a
model.

**B6. Can it handle our real instrument universe — multi-currency, more asset
classes?**
The instrument model carries asset class, currency, lot size, tick size; bonds
already trade with different cash math (% of par). Widening the universe is
data, not redesign.

**B7. What about partial fills, algos, iceberg orders?**
Out of MVP scope — orders fill whole or rest unfilled. Five order types plus
time-in-force and trailing stops are in; iceberg is on the roadmap the product
owner already asked for.

**B8. What happens when something breaks mid-flow?**
The STP worker raises a high-severity exception, notifies the owner, and the
ops dashboard shows it; operations re-queues it with one click (the
`STP_EXCEPTION_HANDLE` permission). Nothing fails silently.

**B9. How does compliance know who did what?**
Every security-relevant action lands in an append-only, hash-chained audit
log — tampering with history breaks the chain. Authorization is
deny-by-default; every denial is audited too.

**B10. How would our clients use this?**
The client view is read-only: their portfolios, valuation, reports (PDF/CSV,
schedulable daily/weekly), and the assistant. No order permissions — that's
deliberate segregation.

**B11. What would rollout look like?**
Three honest phase-2 items first: SSO against the corporate IdP (the seam is
designed), real email delivery, and database migrations tooling. Then
environment hardening: the Terraform/CI exists but we'd run it before
trusting it.

**B12. Would InfoSec sign off?** **[honesty]**
The architecture is the DevSecOps ask: deny-by-default RBAC, tamper-evident
audit, secrets behind a provider abstraction, blocking secret/vulnerability
scans in the pipeline. What we'd disclose: the training build uses a shared
demo password and mocked CyberArk/directory adapters — the seams, not the
controls, are simplified.

**B13. Does it replace Excel for the monthly client statement?**
Partially — scheduled PDF/CSV reports are in. The plain-English
system-written monthly summary the clients asked for is a roadmap item the
assistant seam is built to ground.

**B14. Training effort for users?**
Low by design: each role sees only their tabs, the UI is bilingual EN/JA, and
the trading layout follows terminal conventions traders already know.

**B15. Top three risks you haven't solved?** **[honesty]**
(1) The deployment pipeline is defined but never executed end-to-end. (2)
Several in-memory state stores pin us to one app instance until the Redis
seams are exercised. (3) Performance targets are adopted but not yet measured
under load.

---

## Tech side

**T1. Why a modular monolith instead of microservices?**
14 SRS modules in 3 weeks can't carry the operational cost of a dozen
services. One deployable with strict module boundaries; the event pipeline
keeps STP asynchronous. Boundaries are drawn so a later split is mechanical
(D-01).

**T2. Is the event pipeline exactly-once?**
No — transactional outbox gives at-least-once; every consumer is idempotent
(the STP worker skips executions that already have a settlement instruction).
That combination gives effectively-once without a heavier broker (D-02).

**T3. Race conditions — cancel vs fill, overselling?** **[honesty]**
Known and documented: validation checks balances at submit, resting orders
don't reserve, so two full-size resting sells can both fill. The fix is
fill-time balance guards, then reservation accounting. We can demo the
validation layer, not claim perfection.

**T4. The audit hash chain — what about concurrent writers?** **[honesty]**
The chain head is read-then-insert; two simultaneous writers could fork it.
Serializing chain writes is on the hardening list. The detection property
holds either way — a fork is itself visible.

**T5. Can it run multi-instance?**
Not yet honestly. The inventory: session store and event bus have Redis
implementations (untested); the WS registry, permission cache, login lockout
and price registry are process-local. Multi-instance is a designed-for phase
2, not a claim.

**T6. Performance numbers?** **[honesty]**
Targets adopted from the SRS (TBD-07), never measured — the load test (200
concurrent WS clients + order throughput) is written up but not run. Known
hot spots: chart endpoints aggregate minute bars in Python, valuation
snapshots grow unpruned.

**T7. Database migrations?**
`create_all` + an additive-column table for existing dev DBs. Alembic is the
named first phase-2 task — the only TODO in the codebase.

**T8. Testing story?**
103 backend tests across foundation/trading/governance/experience, mapped to
acceptance criteria in design 19; worker-path tests boot real workers with
fast timing. Honest gaps: no frontend tests, no load tests.

**T9. Did the CI/CD pipeline actually run?** **[honesty]**
No — the pipeline, Dockerfiles and Terraform are statically validated only;
the repo lives on GitHub while the pipeline is GitLab CI. We present them as
designed-and-reviewed, not executed.

**T10. How are passwords and sessions handled?**
PBKDF2-HMAC-SHA256 (120k iterations) with per-email 5-strikes lockout;
uniform 401 against enumeration. Sessions are opaque server-side tokens
(30 min idle / 12 h absolute) so revocation actually works. Dev-login exists
only behind a `DEV_AUTH` flag for tests.

**T11. Rate limiting?** **[honesty]**
Only the login lockout today; the `RateLimited` error type exists but isn't
wired into middleware yet.

**T12. WebSocket auth — token in the URL?** **[honesty]**
Yes, `?token=` — acknowledged trade-off (lands in access logs). The socket
validates once at connect; a 4401 close now routes the client to re-login
instead of reconnect-storming. Moving auth into a first-message handshake is
the cleanup.

**T13. Is CyberArk real?**
Behind an adapter. The mock speaks the checkout/check-in contract and fails
closed when "unavailable"; the real PVWA client swaps in at the seam. Same
pattern for the secret provider, directory sync and SMTP.

**T14. How would you wire a real LLM safely?**
Provider-agnostic client behind the existing seam; the engine keeps a strict
read-only tool whitelist, advisory-only answers, and no path from text to
order execution (FR-AI-003).

**T15. Why a simulation clock?**
Determinism and honesty: charts, staleness, news visibility and scheduled
reports all measure against dataset time, so the demo never "knows the
future", and the pace is a dial (now 1 bar/s, wall-second aligned — you saw
clean 13:23→13:24 steps). It also makes the whole system testable without
waiting for real market hours (D-10/D-11).

**T16. Data growth / retention?**
Designed, not implemented: `PriceTick`/`AuditEvent` monthly partitioning and
retention are in design 16; valuation snapshots currently grow ~2,880
rows/day/portfolio unpruned. **[honesty]**

**T17. Duplicate order submissions?**
Client-minted idempotency keys with a unique constraint + a race-safe replay
path. **[honesty]** The key is globally unique rather than per-user — a
cross-user collision quirk on the fix list.

**T18. How do bonds work in the same pipeline?**
Quoted % of par with lot 1,000; one bond-aware `trade_value()` helper is the
single place that knows the ÷100 — orders, STP, valuation, reports and the UI
all share it.

**T19. i18n without a library?**
Hand-rolled provider with a compile-enforced key parity between EN and JA —
dictionary drift is a TypeScript error, not a runtime surprise.

**T20. With 3 more weeks, what would you build first?**
Alembic, the Redis-backed stores exercised + multi-instance smoke, the load
test, fill-time balance guards, audit-chain serialization — then per-desk
limits and the real LLM behind its guardrails.

---

*Usage: skim before the Q&A; answer from the flagged honesty position first —
the room respects "here's exactly what's real" more than a perfect story.*
