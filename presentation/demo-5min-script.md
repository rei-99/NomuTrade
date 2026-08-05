# 5-minute demo — stage script (exactly what to do and say)

Companion to `demo-5min.md` (the spine). This is the runbook: every click and
every line, beat by beat. Every UI string below was verified against the code
(`frontend/src/**`, `backend/app/**`) on 2026-08-04 — quote them confidently.

**One choreography change vs the beat sheet:** beat 4 adds a 10-second UL
micro-trade. Reason: settlement hops run on a ~5 s wall-clock sweeper
(`SETTLEMENT_DELAY_SECONDS=5.0`, sweeper every 1 s), so the IBM row from beat 2
is already SETTLED by 1:50 — without a fresh trade there is no live walk to
watch. `» +1d` compresses *market* time; it does not drive settlement — the
script below narrates the two as separate facts.

Timing discipline: rehearse to **4:30**; the room adds 20%. Lines marked
*(drop if late)* are the first cuts. Spoken word count ≈ 560 — a calm pace.

---

## Pre-flight checklist

**T-30 min**
- [ ] `./dev.sh sqlite` running; `GET /api/v1/health` → 200; UI loads on :5173.
- [ ] Dataset actually loaded: top bar shows the **`SIM LIVE`** dot and the SIM
      clock (`SIM MM-DD HH:MM`) advances. If not, the random-walk fallback is
      running and `» +1d` will return 409 — restart with `data/` present.
- [ ] `backend/.env` still has `REPLAY_START=2026-08-24` (late-August sim
      dates on stage). Restart the backend now so risk numbers are fresh.
- [ ] **Do not reset `backend/stp.db`.** The Desk Book 1 history (MSFT ≈74%
      concentration, UST10Y/AAPL29/UST2Y bonds, realized P&L from the TSLA
      sell) was placed manually on 2026-08-02 — the seed does NOT recreate it.
      Verify: Portfolios → Desk Book 1 shows those positions; Concentration
      donut ≈74%.
- [ ] **IBM, UL, MSFT31 untouched** — no positions in them (they are the live
      trades: IBM beat 2, UL beat 4, MSFT31 reserved for Q&A).
- [ ] Window 1: logged in `trader@demo.nomura`, on the Trading workspace.
      Window 2: logged in `ops@demo.nomura`, on **Governance & Health**.
      No logins happen on stage.
- [ ] LLM live? Hotspot connected, send one warm-up assistant query, and check
      window 2: the `llm` tile shows UP with detail `live: <model>`. If it
      shows `down: <reason> — using mock` or `mock: not configured`, use the
      mock-variant lines in beat 5 — the flow is identical.
- [ ] Browser zoom 125–150%; video fallback cued:
      `tools/demo-recorder/recordings/demo-2026-08-02T05-17-35-726Z.webm`.

**T-1 min**
- [ ] Window 1 focused, tape ticking. Breathe.

---

## Beat 1 — Open (0:00–0:25) · window 1, workspace

**DO** — nothing yet; the screen is the opening. Gesture: tape → chart → SIM clock.

**SAY**
> "What you're looking at is a live trading platform. The tape is ticking,
> candles are drawing, and this clock, top right, is a *simulation* clock —
> the platform replays a real market dataset at one minute-bar per second, a
> market day in about six and a half minutes. Nothing in the system knows the
> data is simulated. Every number you'll see comes out of the real pipeline."

---

## Beat 2 — Trade (0:25–1:15) · window 1, workspace

**DO**
1. Click the **`IBM`** chip on the tape (chart and order panel switch to IBM).
2. In the **Order entry** panel: click the size input and **type `25`** —
   the chips are only 10/50/100, so type it.
3. Click **`BUY`** → the `Confirm BUY` modal opens. Hold a beat on it:
   Est. cost, Cash before, Cash after.
4. Press **`Enter`** (equals clicking `Confirm BUY (Enter)`; `Esc` cancels).
5. Toast `BUY 25 IBM accepted` appears at once; a few seconds later
   `BUY 25 IBM filled @ …`. Point at the positions table — the **Mark**
   cells flash green/red as ticks arrive.

**SAY**
> "Rohan — our head of product — asked for single-click trading. What we
> shipped is one *informed* decision. I pick IBM on the tape, size
> twenty-five, one click on BUY — and this is the click that matters: a
> confirmation with the full cost impact. Estimated cost, cash before, cash
> after. Second click.
>
> *(toast)* Accepted — and filled at the market, in seconds. That fill went
> through the real pipeline: validation, an execution engine matching it
> against the live tick stream, positions and cash updated *(drop if late:
> — and a settlement instruction created, which you'll see in a moment)*.
> No manual step anywhere."

---

## Beat 3 — Risk (1:15–1:50) · window 1, workspace (same page, scroll down)

**DO** — move to the **Risk exposure** panel at the bottom of the workspace.
Point in order: the four donuts → the Bond book line → Top holdings.

**SAY**
> "Same screen, the risk panel — recomputed live from the positions. Four
> donuts: concentration, annualized volatility, one-day VaR and expected
> shortfall — and they just reacted to the IBM buy. This desk is seventy-plus
> percent Microsoft, which is why concentration is red.
>
> And because the book holds bonds, there's a bond-book line: weighted
> yield-to-maturity and modified duration. *(drop if late: A bond is not a
> stock with a different symbol — it's priced as percent of par, with proper
> cash math.)*"

---

## Beat 4 — STP payoff (1:50–2:35) · window 1, workspace → Trades

**DO**
1. Quick second trade for a live lifecycle: click the **`UL`** chip, click
   the **`10`** size chip, **`BUY`**, **`Enter`**. (~10 s, no narration
   needed beyond the line below.)
2. Click **`Trades`** in the sidebar → **Trade Blotter**.
3. Top row, **Settlement** column: watch the badge walk
   `EXECUTED` → `AFFIRMED` → `SETTLED` (~5 s per hop; if it's already
   SETTLED, use the alternate line).
4. Press **`» +1d`** in the top bar, twice. Toast: `Replaying one market day
   at full speed`. The SIM clock races through two days; the tape keeps
   ticking.

**SAY**
> "Now the payoff: straight-through processing. One more trade — UL, ten
> shares, same two clicks — and watch the settlement column in the blotter.
> The instruction is born with the fill: EXECUTED… AFFIRMED… SETTLED. Seconds,
> and nobody touched it.
>
> *(alternate if already settled: "This row settled eight seconds after the
> fill — the timestamps are right there.")*
>
> And because the clock is ours, I can compress time. One button replays a
> whole market day at full speed — every tick processed. Two market days, in
> seconds."

---

## Beat 5 — AI agent (2:35–4:00) · window 1, Assistant tab

**DO**
1. Click **`Assistant`** in the sidebar.
2. Type **exactly** `Help me buy 10 stocks of APPL at market price` → Enter.
   (This is the test-suite sentence; APPL resolves via the instrument-name
   match.) Watch the reply stream in.
3. Point at the clarification: *"Just to be sure: did you mean AAPL (Apple
   Inc.)? … Reply 'yes' to continue or 'no' to cancel."*
4. Type `yes` → Enter. The answer streams; a **Suggested ticket: BUY AAPL ×
   10** bubble appears with a **`Review ticket`** button.
5. Click **`Review ticket`** → the standard order ticket opens, prefilled.
   Show it two seconds. **`Esc`** — do NOT submit.
6. Type `what about its sentiment?` → Enter. It resolves "its" to AAPL from
   the conversation and streams the sentiment answer with the dataset
   headlines behind it.

**SAY**
> "Now the GenAI agent — and notice the typo: A-P-P-L. *(send)* The reply
> streams in live. It doesn't guess — it asks: did you mean AAPL, Apple?
> Yes. *(send)*
>
> Now the important part: it does **not** trade. It prepares a suggested
> ticket — Review ticket — and it lands in the *standard* order ticket. Same
> validation, same two-click confirm as any human order. Advisory only; the
> decision is always mine. I'll close it without submitting.
>
> And the conversation has memory: 'what about its sentiment?' — it knows
> 'its' is Apple, and it answers from our own news dataset, headlines
> included. It refuses to invent numbers it doesn't have.
>
> *(live LLM:)* It's running on a live model right now — the next window
> proves it — but one config file is the whole difference; unreachable model,
> it falls back to the rule-based mock and keeps working.
> *(mock variant:)* Right now it's the rule-based mock, honestly labeled.
> One config file points it at any OpenAI-compatible model, a startup
> self-check takes it live, and unreachable means it falls back here. That
> fallback is the resilience story, not a caveat."

*(If running late: skip step 6 and its two sentences — saves ~15 s.)*

---

## Beat 6 — Ops + honesty (4:00–4:45) · window 2 (pre-logged as ops)

**DO** — switch windows. On **Governance & Health**, point in order:
integration tiles (`directory`, `cyberark`, `smtp` with their `mock` chips,
`market_feed`, `llm` showing `live: <model>`) → **STP exceptions** lane
(`No STP exceptions — pipeline is clean.`) → **Recent settlements** lane
(IBM and UL rows, SETTLED).

**SAY**
> "The same story from the middle office — this window is logged in as our
> operations analyst. Integration health across the top: the LLM tile, live
> on the model *(mock variant: honestly badged mock)* — and directory and
> SMTP carry mock badges. Every seam we faked is *labeled* a mock. Honest
> seams, not fake green.
>
> STP exceptions: none — pipeline clean. If one ever stuck, ops retries it
> from this screen. And the recent-settlements lane: the IBM and UL trades
> you just watched happen — settled."

---

## Beat 7 — Close (4:45–5:00) · back to window 1

**DO** — switch back. Tape still ticking. Face the room.

**SAY**
> "Underneath all of it: deny-by-default permissions on every request, and a
> hash-chained, append-only audit trail — tamper-evident by construction.
> One order, from click to settled, wrapped in enterprise controls.
> Questions?"

---

## Recovery lines (never debug on stage)

- **Fill toast is slow** (market fills on the next tick; toast within ~5 s):
  keep talking — "the toast polls every few seconds; meanwhile the tape
  keeps ticking."
- **`» +1d` errors** (only if the fallback feed is running — pre-flight
  catches this): "the clock loops the dataset on its own — the point is the
  pipeline processes every tick." Move on.
- **LLM / hotspot dies mid-assistant:** the stream silently falls back and
  mock answers deterministically — "and *that* is why the mock exists: same
  grounding, same guardrails." Continue the beat in mock.
- **Anything on fire:** switch to the 40 s video, narrate beats 1–4 over it,
  say plainly the fallback is running, offer to retry live during Q&A.

## Cut-if-late ladder (in order)

1. Beat 4: drop the UL micro-trade, use the alternate "timestamps" line (−12 s).
2. Beat 5: drop the sentiment follow-up (−15 s).
3. Beat 6: tiles only, skip the two lanes (−20 s).
4. Beat 3: drop the bond-book sentence (−8 s).

## After the demo — Q&A ammo, one click away

Bond trade on MSFT31 (tape scope toggle `Bonds` — it lives *on the tape*,
not the top bar), access-request approval, break-glass, audit hash chain,
paper trading, report generation, the `/connect` phone page. See
`demo-5min.md` §"Cut on purpose" and `demo-guide.md` for those walkthroughs.


---

## Q&A — Risk-exposure metrics (what they mean, how computed, why these)

Every figure on the Risk Exposure panel is computed from the portfolio's
**daily total-value series** (market value + cash), one point per day.
Source: `ValuationSnapshot` history; when the book is too new for 10 days of
snapshots, we fall back to repricing today's book through stored daily closes
("how would this exact book have moved"), so the KPIs are meaningful from day
one instead of N/A. `backend/app/modules/portfolios/valuation.py`.

**Concentration (%)** — the single largest holding's share of market value.
- *How:* `top_holdings[0]["pct"]` — max position value ÷ total market value.
- *Why:* the one-number answer to "how diversified is this book?" Regulators
  and risk limits frame concentration first because idiosyncratic risk is the
  easiest to blow up on. Ours reads 100% on a one-stock demo book — a great
  talking point, not a bug.

**Volatility, annualized (%)** — how violently the book's value swings.
- *How:* `stdev(daily total values) × √252 ÷ mean × 100`, needs ≥ 10 daily
  points (252 = trading days/year).
- *Why:* the universal risk yardstick — every other metric (VaR, limits,
  margin) starts from vol. A trader compares it against the market's vol to
  know if they're running hot.

**VaR 95%, 1-day (%)** — "on a bad day (1 in 20), expect to lose at least
this much, as % of book value."
- *How:* historical simulation — the 5th percentile of daily returns,
  negated: `VaR = max(0, −q5) × 100`. Not parametric, no normal-distribution
  assumption — it just ranks real observed days.
- *Why:* the industry-standard headline risk number since the 1990s; it's
  what risk committees quote and what limits are written against. 95% 1-day
  is the Basel-flavored convention.

**ES / CVaR 95%, 1-day (%)** — "when the worst 5% of days *do* happen, the
average loss is this."
- *How:* mean of all daily returns at or below the 5th percentile, negated.
  Always ≥ VaR by construction.
- *Why:* VaR tells you where the cliff edge is, ES tells you what's *below*
  it — it catches fat tails that VaR hides. Post-crisis regulation (FRTB)
  moved from VaR to ES for exactly this reason; having both on the panel
  shows we know the difference.

**Sharpe ratio, annualized** — return earned per unit of risk taken.
- *How:* `mean(daily returns) ÷ stdev(daily returns) × √252`, risk-free rate
  = 0 (documented training simplification).
- *Why:* the honest "was it worth it?" metric — a book up 10% on 30% vol is
  worse than one up 6% on 5% vol. It stops traders from celebrating raw P&L.

**Max drawdown (%)** — the worst peak-to-trough fall the book has suffered.
- *How:* running peak of daily total value; `max((peak − value) ÷ peak) × 100`.
- *Why:* the metric every investor *feels* — "how much pain, worst case, did
  sitting through this book cost?" VaR is forward-looking probability;
  drawdown is the lived worst case. Fund mandates often cap it explicitly.

**Day change ($ and %)** — today's move vs the previous day's open, per
position and in total.
- *How:* mark-to-market at latest tick vs `prev_day_open` per instrument,
  summed. (On the sim clock, "day" is dataset day.)
- *Why:* the number a trader checks first each morning; everything else on
  the panel is context for it.

**If asked "why no Greeks / beta / factor exposures?"** — this is a
cash-equity-and-bond book with no derivatives and no factor model in the
dataset; delta-1 metrics plus vol/VaR/ES/DD is the honest, complete set for
it. The valuation seam (`_daily_total_values`) is exactly where a beta or
factor series would plug in.

---

## Q&A — How are Operations mapped to traders in the real world?

**Answer: many-to-many, organized as a desk-level queue — not a personal
hotline.** And that is what we built.

- **Reality (industry):** an operations team services a whole trading desk or
  business unit — dozens of traders — through a shared exception queue with
  triage and SLAs, first-available analyst picks items up. It's many-to-many:
  any trader can produce an exception, any qualified op can clear it.
  High-touch variants exist (a senior sales-trader pair may get a dedicated
  op — many-to-one), but that's the exception, not the model. Follow-the-sun
  ops (Tokyo → London → New York) makes queue-based assignment a necessity,
  not a choice — a fixed person is a single point of failure at 3 a.m.
- **Our design mirrors that:** failed orders and STP exceptions surface to a
  *role*, not a person — anyone holding `STP_EXCEPTION_HANDLE` (the
  Operations Analyst role) sees the queue and can act (design decision: the
  queue is the permission). The trader who owns the order is always notified
  too, so accountability stays with the originator while resolution stays
  with the team.
- **Why not route to "the trader's fixed op"?** Because at scale that
  assignment table is another thing to maintain, and it breaks the moment
  someone is out sick. Role-based queue = resilient by construction.
- **If asked about SoD:** the trader cannot clear their own exception —
  `ORDER_SUBMIT` (trader) and `STP_EXCEPTION_HANDLE` (ops) are separate roles
  in separate hands, which is the segregation-of-duties point compliance will
  ask about. The audit trail records who requeued what and why, so the
  queue is fast *and* accountable.

## Q&A — What is the outbox relay? ("How do you guarantee no step is lost?")

**Answer: the database keeps the promise, the relay is just the courier.**
Every state change commits *with* its event in one transaction — so a fill
can never exist without its settlement event, nor the reverse.

- **The problem it solves**: you can't write a database and publish a message
  atomically. Publish first and the commit fails → a phantom fill moves
  positions and cash. Commit first and crash before publishing → the fill
  exists but the STP worker never hears about it; settlement silently never
  happens — which FR-ORD-005 (zero manual steps) forbids.
- **The pattern**: the module writes the state row *and* an `OutboxEvent`
  row in **one DB commit** — both or neither. The relay is a background loop
  (every 0.2 s, batches of 100) that reads unpublished rows
  (`published_at IS NULL`), publishes them to the event bus, stamps them,
  commits. The DB is the source of truth for "what happened"; the relay only
  moves that fact onto the bus.
- **Why duplicates don't hurt**: a crash between publish and stamp means the
  row is republished — delivery is *at-least-once* by design, so every
  consumer is idempotent (the STP worker skips executions that already have
  an instruction; the engine skips closed orders; the projector swallows the
  unique-constraint hit). A redelivery is invisible; a lost event is
  impossible.
- **Resilience details worth quoting**: a failed batch logs and retries —
  it can't kill the relay or the other workers; and on shutdown an in-flight
  batch drains to completion (cancelling mid-DB-call can wedge aiosqlite —
  a war story we actually hit and fixed).
- **One-liner for stage**: "We don't try to make two systems atomic — we make
  the event a database row committed with the trade, and let a courier catch
  up. That's why the pipeline can claim zero manual steps."

*Code references if pressed: `backend/app/core/events.py` (`write_outbox`,
`outbox_relay`, `_relay_batch`); design doc §4.2 (D-02); the sequence diagram
in `presentation/trade-lifecycle.md` (PNG: `trade-lifecycle-sequence.png`).*
