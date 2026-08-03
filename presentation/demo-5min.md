# 5-minute demo flow — audience version (v2, reviewed)

Spine: **one order's journey, from click to settled, wrapped in enterprise
controls.** Six beats, ~5:00. Pre-log both browser windows before starting
(window 1: trader workspace; window 2: ops Governance).

| Time | Beat | Do | Say |
|---|---|---|---|
| 0:00–0:25 | Open | Window 1: workspace — tape ticking, candle chart, SIM clock | "A trading platform replaying a real dataset on a simulation clock — nothing in the system knows the data is simulated." |
| 0:25–1:15 | **Trade** | IBM 25 → BUY → two-click confirm → fill toast, positions flash | "Two-click confirm. The fill goes through the real pipeline — validation, execution, positions, cash." |
| 1:15–1:50 | Risk | 4 donuts (concentration/volatility/VaR/ES), bond-book line, top holdings | "The risk panel recomputes live — and because the book holds bonds, weighted yield and duration too." |
| 1:50–2:35 | **STP payoff** | Blotter settlement column; press `» +1d` twice; states walk EXECUTED → AFFIRMED → SETTLED | "Straight-through processing — zero manual steps. And I can compress time: two market days in seconds, every tick processed." |
| 2:35–4:00 | **AI agent** | Assistant: "Help me buy 10 stocks of APPL" → clarify → "yes" → prefill ticket → "what about its sentiment?" (streaming on) | "The agent streams, remembers the conversation, grounds every number in our data — and is advisory only: it prepares, never places. One config file takes it from mock to a live model." |
| 4:00–4:45 | Ops + honesty | Window 2 (pre-logged as ops): `llm · live` tile, mock badges on directory/SMTP, settlements lane, clean exceptions | "Middle office watches the same flow from their side. Every mock is labeled a mock — honest seams, not fake green." |
| 4:45–5:00 | Close | Back to window 1 | "Deny-by-default security and a hash-chained audit trail under everything. Questions?" |

## Cut on purpose (Q&A ammo, not demo)

Bond trade on MSFT31, access-request approval (approver inbox), break-glass,
audit hash chain, paper trading, report generation/schedules, phone access.
If asked "can it do X", these are the live follow-ups — each is one click away.

## Risk controls for stage

- **Network**: demo over the iPhone hotspot (verified), not venue Wi-Fi.
  Pre-warm the LLM with one assistant query in the minute before starting.
- **Pre-open**: window 1 trader workspace, window 2 ops Governance — no
  logins on stage.
- **State**: IBM + MSFT31 untouched until the live trade;
  `REPLAY_START=2026-08-24` in `backend/.env`; restart the backend ~10 min
  before so risk numbers are fresh.
- **Fallbacks**: LLM/network dies → "mock mode is deterministic" and move on
  (never debug on stage); recorded 40 s video
  (`tools/demo-recorder/recordings/…webm`) covers the trading beats.
- **Timing**: rehearse to 4:30 — the room always adds 20%.

## Timing judgment calls (for discussion)

- Bond trade dropped to Q&A (saves 30 s, costs the second asset class).
- AI agent gets 85 s — the wow-factor, but the least deterministic beat.
