# Demo guide — seeded data, run-of-show, and the recorded walkthrough

Everything here assumes the local stack (`./dev.sh sqlite`, API :8000 / UI :5173)
and login `trader@demo.nomura` / `demo1234` (or the persona named in the step).

## 1. Seeded trade history (Desk Book 1)

**Audience accounts**: 100 demo traders (`trader_1…trader_100@demo.nomura`,
password `demo1234`, each with an empty funded 100k book) are created by an
idempotent startup patch — a fresh machine gets them on first boot. Every
new browser session's login page is **prefilled with the next unused
account** (round-robin via `GET /auth/demo-credential`), so audience members
on phones just tap Sign in.

Seeded 2026-08-02 via the ordinary order pipeline (MARKET orders → fills →
positions/cash → settlement instructions, all SETTLED):

| Symbol | Side | Qty | Note |
|---|---|---|---|
| MSFT | BUY | 100 | largest position (~73% concentration — great for the risk panel) |
| TSLA | BUY 30, SELL 10 | net 20 | the SELL creates realized P&L |
| GOOG | BUY | 25 | |
| AAPL | BUY 50, SELL 50, BUY 5 | net 5 | realized + remaining position |
| UST10Y | BUY | 2,000 face | bond — % of par math |
| AAPL29 | BUY | 1,000 face | corporate bond |
| WMT | BUY | 25 | from the recorded demo (§3) |
| UST2Y | BUY | 1,000 face | from the recorded demo (§3) |

Result (live numbers at seed time): total value ≈ $50.0M, market value
≈ $63k invested, realized P&L +$244, day change and uP&L moving with the
replay, allocation ≈ 94/6 equity/bond, concentration ≈ 74% MSFT.

**Left untouched for your live demo:** equities **IBM** and **UL**, bond
**MSFT31** — no orders, no positions. (WMT/UST2Y were spent recording the
video; use IBM + MSFT31 on stage.) Client Portfolio A is cash-only — the
Client role has no `ORDER_SUBMIT`, so it can only ever hold seeded positions.

Honesty notes for Q&A:
- `STALE` marks appear when the replay **loops** (`REPLAY_MODE=loop` jumps the
  sim clock back to the pass start); marks refresh as new ticks arrive.
- VaR / volatility / max drawdown use the live snapshot history once 10+ days
  exist; before that they **reprice the current book through stored daily
  closes** (standard approximation, cash held constant) so the KPIs show real
  numbers from day one instead of N/A. A portfolio with no positions still
  shows N/A — correctly.
- The local `backend/.env` (gitignored) sets `REPLAY_START=2026-08-24` so each
  replay pass starts in the dataset's final week — late-August sim dates on
  stage. Delete that line to replay from the beginning.

## 2. Recommended live run-of-show (~4 min, fits the deck's 90-s demo beat if trimmed)

1. **Trade an untouched equity (trader)** — Trading workspace → pick **IBM**
   on the tape → size 25 → **BUY** → two-click Confirm. Narrate: fill toast,
   positions table flashes, risk gauges react, and the settlement will
   proceed with *zero manual steps*.
2. **Trade an untouched bond** — scope toggle **Bonds** → **MSFT31** → 1,000
   face → BUY. Point at the bond analytics card (coupon, YTM, mod. duration,
   yield → implied price) and the % of par est-cost note.
   *Tip:* the **`» +1d`** button in the top bar replays one market day at
   full speed per press — every tick is processed (fills, triggers,
   snapshots), so settlement states progress and stops fire as if the day
   really passed, in seconds instead of 6.5 minutes.
3. **Portfolio management** — Portfolios → Desk Book 1: KPI cards, allocation
   donut (equity vs bond), P&L contribution per name, positions with day
   change; Transactions tab for the realized-P&L story (the TSLA sell).
4. **Settlement visibility** — Trade Blotter: the SETTLEMENT column walks
   EXECUTED → AFFIRMED → SETTLED in front of you (~5 s cadence).
5. **Ops view** — sign out, in as `ops@demo.nomura` → Governance: integration
   health (honest *mock* badges on directory/SMTP), STP exceptions (clean),
   and the Recent settlements lane showing the trades you just made.
6. **If time — governance beats** (from the README demo script): access
   request → approver inbox, break-glass as sysadmin, audit search as auditor
   (hash-chained trail).

## 3. Recorded walkthrough (already captured)

`tools/demo-recorder/recordings/demo-2026-08-02T05-17-35-726Z.webm` (40 s,
1680×1000) — the automated browser run, usable as the video fallback. What it
does, with timestamps:

| t (s) | Beat |
|---|---|
| 0–8.9 | Login as trader (real form, visible typing) → Trading workspace, tape + chart ticking live |
| 8.9–14.9 | WMT 25 MARKET BUY: chip → size → BUY → two-click Confirm → "accepted" + "filled @ 68.57" toasts |
| 14.9–21.7 | Bonds scope → UST2Y 1,000 BUY → filled @ 98.86; bond analytics card (coupon 3.75%, YTM 4.98%, mod. duration 0.95) |
| 21.7–26.9 | Portfolios → Desk Book 1: KPIs, allocation donut, P&L contribution, positions table |
| 26.9–31.0 | Trade Blotter: SETTLEMENT column (UST2Y AFFIRMED, rest SETTLED) |
| 31.0–40.1 | Sign out → ops login → Governance: health tiles with mock badges, clean STP exceptions, Recent settlements lane (both new trades SETTLED) |

Per-beat screenshots: `tools/demo-recorder/recordings/shots/*.png`; beat log:
`recordings/*.beats.txt`.

### Re-record / extend

```bash
# stack running (uvicorn :8000 + vite :5173), then:
PLAYWRIGHT_BROWSERS_PATH=$PWD/tools/demo-recorder/.pw node tools/demo-recorder/record-demo.js
```

The driver is `tools/demo-recorder/record-demo.js` (playwright-core against
the installed Edge — no browser download; the 1 MiB ffmpeg for video lives in
`tools/demo-recorder/.pw`, git-ignored). Edit the acts to add beats (e.g.
access approval, break-glass) — selectors are pinned to the current UI.

## 4. AI agent (design 27) — optional live-LLM beat

Default is mock (honestly badged): rule-based news summary, keyword-RAG help,
rules advisory review. To flip the agent live for the presentation:

1. `cp .env.example backend/.env` and set `LLM_PROVIDER=openai`,
   `LLM_API_URL`, `LLM_API_KEY` (+ model names if different). Restart the
   backend — **no code change**.
2. Prove the self-check on stage: Governance → integration health shows the
   `llm` tile `live: <model>`; with a bad key/URL it shows
   `down: <reason> — using mock` and everything keeps working.
3. Beats that show the agent well:
   - **News summary** (Trading panel): the badge flips from "Rule-based
     summary (mock LLM)" to the live model — same grounding, real prose.
   - **RAG help** (Assistant tab): "how do I approve an access request?" →
     answer grounded in the project's own docs with `[doc]` citations.
   - **Multi-turn agent flow** (design 28): "Help me buy 10 stocks of APPL at
     market price" → the agent asks "did you mean AAPL?" → say "yes" → a
     **prefill ticket the user must confirm** appears (it never decides) →
     then "what about its sentiment?" resolves *its* from the conversation.
     The replies stream token-by-token when the LLM is live.
4. If no key: present the same three beats in mock mode — the honest line is
   "the seams are real, the default is mock; one config file takes it live".

Cost control: doc embeddings persist in `doc_embeddings` (content-hash keyed)
— only changed chunks are re-embedded on boot.
