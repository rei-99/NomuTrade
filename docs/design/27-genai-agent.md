# 27 — GenAI agent: real-LLM seam with mock fallback (news, RAG help, advisory)

Driver: owner instruction set. (1) News summary by a real LLM when configured.
(2) Assistant tab: (a) RAG over the project's own docs — "how do I use this
system"; (b) trading *advice* — help generate a ticket and review ideas,
**never decide**. (3) Research further agent functions (§6). (4) Make it a
presentation plus-point (§7). Config: a file where the owner sets api_url /
api_key for **both** a chat model and an embedding model; on startup the
platform checks connectivity and **falls back to the current mock workflow**
when unset or unreachable.

## D-27.1 — Provider configuration (OpenAI-compatible, env-driven)

One provider kind: **OpenAI-compatible HTTP** (`POST {url}/chat/completions`,
`POST {url}/embeddings`) — covers OpenAI, Azure OpenAI (deployment URL),
DeepSeek, Qwen, Ollama, vLLM. New settings (env / `.env`, no prefix):

| Var | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock` \| `openai` |
| `LLM_API_URL` | `""` | e.g. `https://api.openai.com/v1` (no trailing slash) |
| `LLM_API_KEY` | `""` | sent as `Authorization: Bearer …` |
| `LLM_CHAT_MODEL` | `gpt-4o-mini` | chat completion model name |
| `LLM_EMBED_MODEL` | `text-embedding-3-small` | embedding model name |
| `EMBEDDING_API_URL` | `""` → falls back to `LLM_API_URL` | most providers serve both |
| `EMBEDDING_API_KEY` | `""` → falls back to `LLM_API_KEY` | |
| `LLM_TIMEOUT_SECONDS` | `15` | per-call timeout |
| `RAG_TOP_K` | `4` | retrieved chunks per help answer |

The owner edits `backend/.env` (gitignored) — `.env.example` gains a commented
AI section as the documented "place to specify". **No keys in the repo.**

## D-27.2 — Startup self-check + graceful fallback

Lifespan runs `validate_llm(settings)` when `LLM_PROVIDER=openai`:
`GET {api_url}/models` (chat endpoint) and a 1-token probe of the embedding
endpoint, each with a 5 s timeout. Result → `app.state.llm_status`:

```json
{"provider": "openai", "chat": "ok|down", "embeddings": "ok|down|skipped",
 "detail": "live: gpt-4o-mini" | "down: <reason> — using mock"}
```

- Any failure → **mock mode for that capability**, WARN log; the app never
  fails to boot because a model is unreachable.
- The admin health endpoint gains an `llm` integration tile so the demo can
  *show* the check: `UP + "live: <model>"` / `UP + "mock: not configured"` /
  `DOWN + "down: <reason> — using mock"`.
- Per-call resilience: an LLM error mid-request falls back to the rules
  answer for that request (never a 500 on the user).

## D-27.3 — News summary via LLM (mock by default)

`GET /assistant/news-summary` keeps the current structured grounding (mean,
label mix, top topics, 3 headlines — all real NewsItem/NewsSentiment rows).
When the chat model is live, a new prompt turns that grounding into a short
prose brief (≤60 words, figures verbatim, "do not invent numbers or events",
advisory tone). Response fields: `mock: false, model: <chat model>` when live
(UI badge already honest); `mock: true, model: rules-v1` otherwise or on any
LLM error. Structured fields are always computed — the LLM only rewords.

## D-27.4 — RAG help intent ("how do I use this system")

- **Corpus**: `README.md`, `DESIGN.md`, `docs/design/*.md` — chunked
  heading-aware (~700 chars, overlap 1 line).
- **Store**: new `DocEmbedding` table (source, chunk_ix, content_hash,
  embedding JSON) — created by `create_all`, no migration. Embedded **once at
  startup** when the embedding endpoint is live; unchanged chunks (content
  hash) are not re-embedded (cost control). Retrieval: in-memory cosine over
  the table, top `RAG_TOP_K`.
- **No embeddings → keyword retrieval** (token-overlap scoring) over the same
  chunks — the intent still answers, honestly degraded.
- **New `help` intent** in `AssistantEngine`: how-do-I / what-is questions
  about the platform (tabs, roles, order flow, personas, reports…). Live LLM:
  answer grounded in the retrieved chunks with doc citations; mock: return
  the best chunks with their source names (useful today). Citations get
  `kind: "doc", ref: <doc path>`.

## D-27.5 — Advisory trading (advise / ticket / review — never decides)

Existing guardrails unchanged (FR-AI-003): the assistant has **no order-API
path**; the only trade artifact is `suggested_ticket`, which the UI renders
as a *prefill the user must confirm*.

- **Advise**: trade intent now hands the LLM the grounding (instrument,
  latest price, user position if any, news sentiment) and drafts the advice;
  rules text on fallback. Every advice answer ends with the fixed disclaimer
  line. The engine stays the data source — the LLM rewords, never invents.
- **Ticket**: `suggested_ticket` keeps being rule-built (side/qty parsed,
  validation applied) — the LLM may *explain* the ticket, not change it.
- **Review** ("should I sell MSFT?", "review my book"): new review intent —
  LLM gets positions + risk KPIs (concentration, VaR/ES, drawdown, bond
  duration) + news sentiment and returns an advisory review with the same
  disclaimer. Mock: the rule-based KPI summary (already close to this).
- Currency bug fix along the way: assistant answers hard-code `¥`/`JPY` —
  read `Instrument.currency` (USD) instead.

## D-27.6 — Research: other functions for this agent

Assessed against the SRS and stakeholder asks:

| Idea | Verdict |
|---|---|
| **Plain-English report summarizer** (client's monthly statement ask, FR-AI-002) | **Top roadmap candidate** — the reports seam + this LLM client make it cheap; not in this round |
| Morning brief (positions + fresh news + today's alerts, on login) | Roadmap — one prompt over existing grounding |
| Alert-rule suggestions ("watch TSLA for me") | Roadmap — advisory text → existing alert API stays user-driven |
| Answer language matching (EN/JA) | **In this round, free**: prompt follows the request's language |
| Compliance phrasing guard (refuse promises/price targets) | **In this round**: system-prompt rule + the disclaimer |
| Onboarding tutor (role-aware walkthrough) | Covered by the RAG help intent |
| Order-flow anomaly notes / ops copilot | Later — needs the exception stream as grounding |

## D-27.7 — Presentation angle

- **The honest arc**: "the seams are real, the default is mock" — provider
  config, startup self-check tile, structured grounding. Then the kicker: if
  a key is configured before the presentation, the same panels go live with
  **zero code change** — that's the demo-able plus point.
- Demo beat (optional, if keyed): Trading panel news summary flipping from
  "Rule-based summary (mock LLM)" to the live model badge; one RAG question
  ("how do I approve an access request?") answered with doc citations; one
  advisory review ("should I trim MSFT?") showing advice + disclaimer + a
  prefill ticket the *user* confirms.
- Script/deck: one line in the platform section + Q&A ammo (guardrails,
  fallback, cost control). demo-guide gains an "AI agent" section with the
  .env how-to.

## Testing

- Provider selection: mock when unset; openai only when provider+url+key.
- Startup check: stubbed HTTP (httpx MockTransport) — ok / down → status and
  fallback; boot never fails.
- News summary: LLM path (mock=false) and error → rules fallback.
- RAG: chunking sanity, keyword retrieval ranks the right doc first, help
  intent answers with doc citations (mock mode).
- Advisory: review answer carries the disclaimer and no order rows are
  created; suggested_ticket unchanged in mock mode.
- Existing 106 tests stay green (mock default everywhere).

## Verification

`pytest`; `npm run build`; live boot twice — once without keys (health tile
"mock: not configured", mock badges) and once with a stub key (tile shows the
down-with-reason fallback); headless screenshots of the panel + assistant.
