# 07 — GenAI Assistant

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.8 (FR-AI — stretch), updated for the news/sentiment intent (D-14/D-15). Decisions, IDs and requirement text unchanged.

## Purpose

Provide an advisory-only assistant that answers portfolio/market questions grounded in the caller's own data — including market **news and sentiment** from the dataset — and can prepare — but never place — trades. This is a stretch (Could) module, attempted only if on schedule (delivery plan, DESIGN.md §8).

## SRS requirements covered

- **FR-AI-001** — answers grounded in platform data; figures cited from tool results.
- **FR-AI-003** — trade suggestions become orders only via the standard ticket and confirmation path (guardrail also listed in [17 — Security Design](17-security-design.md)).
- **D-03** (design decision) — LLM behind a provider-agnostic client (OpenAI-compatible API); tool-calling; strict tool whitelist.
- **D-14 / D-15** (design decisions) — the news/sentiment intent grounds on the dataset news tables, sim-clock capped.

## Components

- **Assistant module** — server-side; the LLM never calls platform APIs directly. It may invoke a **whitelist of read-only tool functions** (`get_positions`, `get_valuation`, `get_transactions`, `get_prices`, `get_news`) which execute with the *caller's own* permissions — answers are grounded and figures cited from tool results (FR-AI-001). (The MVP build is a rule-based engine over the same whitelist, with an LLM-prose seam; no external LLM is called.)
- **News/sentiment intent (D-15)** — questions like *"news on TSLA?"*, *"why is GOOG moving?"* or *"market sentiment today?"* are answered from the dataset news tables (`get_news`): instrument-scoped answers give the 7-day mean sentiment (relative to the latest news timestamp, never `utcnow()`) plus the latest headlines; market-wide answers give per-ticker 7-day means. Every answer carries **news citations** (headline, timestamp, per-ticker sentiment) in `grounded_refs`. With no news for the asked scope the assistant declines explicitly (FR-AI-001); headlines beyond the simulation clock are withheld while a replay runs (D-10/D-14).
- **Ticket hand-off** — trade suggestions return a **pre-filled ticket payload** rendered by the UI into the standard order ticket; confirmation goes through `POST /orders` like any other order (FR-AI-003). The assistant identity holds no trading permissions, so direct API misuse is denied by default and logged as a security event.
- **Interaction log** — every interaction (prompt, tool calls, response, data references) persisted in `AssistantInteraction`.

## Flows

No dedicated flow diagram exists in the source design. The suggestion flow is: assistant → pre-filled ticket payload → UI renders the standard order ticket → user confirms → `POST /orders` → the normal order → execution → STP flow in [02 — Order Execution & STP](02-order-execution-stp.md). The news flow is: dataset news pack → loader (see [01](01-market-data.md)) → `NewsItem`/`NewsSentiment` → `get_news` tool → cited answer.

## Data entities used

- `AssistantInteraction` (prompt, response, `grounded_refs` incl. citations and any suggested ticket).
- Read-only access via tool functions to positions, valuation, transactions, prices and news/sentiment (`NewsItem`/`NewsSentiment` — see [16 — Data Design](16-data-design.md)) — with the caller's own permissions (entities per [03](03-portfolio-management.md), [01](01-market-data.md)).

## API endpoints used

- `POST /api/v1/assistant/query` — the conversational endpoint (permission `ASSISTANT_USE`); returns `{conversation_id, answer, citations, suggested_ticket}`.
- None called by the LLM directly — tool functions only (read-only whitelist).
- Trade confirmation goes through `POST /api/v1/orders` like any other order (FR-AI-003; see 02).
- Standard conventions (former DESIGN.md §9): base `/api/v1`, JSON, error envelope with `traceId`.

## Error / edge cases

- **Direct API misuse** — the assistant identity holds no trading permissions; any direct call is denied by default (NFR-SEC-002) and logged as a security event (see [09](09-rbac-authorization.md), [17](17-security-design.md)).
- **Ungrounded answers** — mitigated by design: tool functions execute with the caller's permissions and figures are cited from tool results (FR-AI-001).
- **No news for the asked scope** — the assistant declines explicitly ("I don't have any news for … in the dataset window") rather than answering ungrounded (FR-AI-001).
- **News beyond the sim clock** — withheld while a replay runs (D-10/D-14): the platform must not know the dataset's future.
- **Scope** — stretch module: attempted only after all Must/Should items pass their acceptance criteria (delivery plan, DESIGN.md §8).

## Acceptance criteria mapping

- No dedicated AC ID is cited for FR-AI in the source design; the guardrail (FR-AI-003) is exercised by the deny-by-default sweep in the security test pass (see [19 — Testing Strategy](19-testing-strategy.md), Security row), and the intents (incl. news/sentiment) by the backend experience tests.
- Per the delivery plan (DESIGN.md §8), the module is built and demoed only if week-3 work is on schedule.
