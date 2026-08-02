# 28 — Agent workflow: LangGraph state machine + conversation memory

Driver: owner report — the assistant fails multi-turn conversations. Concrete
failure: "Help me buy 10 stocks of APPL at market price" → the assistant asks
"did you mean AAPL?" → user: "yes" → assistant answers DECLINE (out of scope),
because every question is routed statelessly. Owner direction: use a real
agent framework (LangChain/LangGraph class) and give the agent conversation
memory.

## D-28.1 — Framework: LangGraph

The assistant's reasoning becomes an explicit **LangGraph state graph**
(`langgraph==1.2.10`, `langchain-core==1.5.3`, pure-Python, no LangSmith —
tracing stays off). The graph is the right shape for this problem: clarify →
confirm → draft is a state machine, not a single LLM call. The graph
orchestrates; the data work reuses the existing engine handlers
(positions/valuation/transactions/price/news/help/review) unchanged. The
mock/LLM split is unchanged: with no provider configured, routing and slot
extraction are rule-based and fully deterministic; when live, the LLM
classifies and extracts — but ticket construction, validation and guardrails
stay rule-side, always.

## D-28.2 — Conversation memory (two layers)

- **Turn history**: rebuilt from the existing `assistant_interactions` rows
  by `conversation_id` (last 10 turns, prompt + response). No new storage;
  the rows were already persisted per turn. The frontend now sends
  `conversation_id` back (kept in sessionStorage with the chat).
- **Pending action state**: new `ConversationState` table
  (`conversation_id` PK, `state` JSON, `updated_at`; create_all). Holds the
  in-flight action across turns, e.g.
  `{"pending_clarification": {"instrument": "AAPL", "side": "BUY", "quantity": 10, "order_type": "MARKET"}}`
  or `{"pending_confirmation": {…ticket…}}`. Written/cleared by graph nodes.

## D-28.3 — The graph

```
load_context ──► route ──┬─► prepare_ticket ─► (pending_confirmation) ─► END
 (history,               ├─► clarify ────────► (pending_clarification) ─► END
  pending state)          ├─► confirm/cancel ─► (ticket ready / state cleared) ─► END
                          └─► answer_question (positions/news/help/review/…) ─► END
```

- `route` sees history + pending state. Priority: pending clarification +
  affirmative/negative resolves it before any new intent; then new intents.
  Live: LLM classifier returns strict JSON `{intent, instrument, side,
  quantity, order_type}`; mock: the existing regex router + an
  affirmative/negative matcher (yes/confirm/对/はい vs no/cancel/不要/いいえ).
- `resolve_instrument` is fuzzy: exact (case-insensitive) → prefix → name
  substring → `difflib` close match (cutoff 0.8). "APPL" → AAPL with a
  one-time clarification question; confirmed once, it sticks for the
  conversation.
- `prepare_ticket` builds `suggested_ticket` with the *existing* rule logic
  and parks it in `pending_confirmation`; `confirm` finalizes the draft
  answer (still just a prefill — the user confirms in the UI); `cancel`
  clears state politely.
- Pronoun/context resolution: "and its price?" / "what about the sentiment?"
  inherit the last-mentioned instrument from history.

## D-28.4 — Guardrails (unchanged, FR-AI-003)

No node can create orders. The only trade artifact remains
`suggested_ticket`, rendered as a user-confirmed prefill. Trade/review
answers keep the fixed disclaimer. Audit rows continue per turn.

## D-28.5 — Integration

`POST /assistant/query` and `/assistant/query/stream` both run through the
graph (the stream route's `ground()` becomes a graph invocation; SSE
contract unchanged). `/assistant/news-summary` untouched.

## Testing

The reported failure becomes a pinned test, in mock mode AND with a fake
live LLM: "Help me buy 10 stocks of APPL at market price" → clarification
mentioning AAPL → "yes" → `suggested_ticket` = BUY 10 AAPL MARKET with a
confirm instruction, zero Order rows. Plus: fuzzy matching unit tests,
pending-cancel ("no" clears state), pronoun resolution from history,
conversation_id round-trip (two calls share memory). Existing 126 tests
stay green.

## Verification

Suite + build; live in mock mode replaying the owner's exact conversation;
headless screenshot of the three-turn flow; live-LLM sanity if a key is
present. Demo-guide AI section gains the multi-turn example.
