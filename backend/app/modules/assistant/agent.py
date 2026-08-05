"""Agent workflow (design 28): LangGraph state graph + conversation memory.

The assistant's reasoning is an explicit LangGraph state machine (D-28.1):

    load_context ──► route ──► resolve_instrument ──┬─► prepare_ticket ─┐
                          │                         ├─► clarify ────────┤
                          ├─► confirm ──────────────┤                   ├─► persist_state ──► END
                          ├─► cancel ───────────────┤                   │
                          │                         └─► answer_question ┘
                          └─► (confirm/cancel edges come straight from route;
                               resolve_instrument fans out by resolution result)

- ``load_context`` rebuilds turn history from ``AssistantInteraction`` rows
  (last 10 turns of this conversation) and the pending action from
  ``ConversationState`` (D-28.2). Both are skipped without a conversation_id.
- ``route`` resolves pending state FIRST: an affirmative/negative reply to a
  pending clarification/confirmation beats any new intent. Pending resolution
  is rule-side in mock AND live mode (guardrails never go through the LLM,
  D-28.1); fresh intents are classified by the mock regex router, or by the
  live LLM classifier (strict JSON) when a chat client is wired.
- ``resolve_instrument`` is fuzzy (D-28.3): exact (case-insensitive, word
  boundary / full-name mention) → prefix → name substring → difflib close
  match (cutoff 0.8) over symbol+name. Only exact matches are confident;
  anything fuzzier becomes a *candidate* that ``clarify`` asks about once
  ("did you mean AAPL?") — confirmed, it sticks in the pending slots.
- ``prepare_ticket`` builds ``suggested_ticket`` with the engine's existing
  rule logic and parks ``pending_confirmation``; ``confirm`` finalizes the
  draft answer (still a prefill — the user confirms in the UI); ``cancel``
  clears state politely; ``answer_question`` delegates to the engine's
  read-only handlers (positions/valuation/transactions/price/news/help/
  review/out_of_scope).
- Pronoun/context: questions with no instrument mention ("and its price?")
  inherit the last instrument mentioned in the conversation history (mock:
  last exact mention in recent turns; live: the LLM classifier may resolve
  it from the same history).

GUARDRAIL (FR-AI-003, D-28.4): no node can create orders. The only trade
artifact remains ``suggested_ticket``, rendered as a user-confirmed prefill.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from typing import TYPE_CHECKING, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.core.models import (
    AssistantInteraction,
    ConversationState,
    Instrument,
)
from app.core.timeutil import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.security import SessionData
    from app.modules.assistant import AssistantEngine
    from app.modules.assistant.llm import LLMClient

logger = logging.getLogger(__name__)

# Conversation memory (D-28.2): last N turns feed routing/pronouns; the scan
# window bounds the DB read (rows are filtered by conversation_id in Python —
# portable across SQLite/PostgreSQL JSON flavours).
HISTORY_TURNS = 10
_HISTORY_SCAN_ROWS = 50

# Question intents the graph can route to (trade handled separately).
INTENTS = (
    "trade",
    "positions",
    "valuation",
    "transactions",
    "price",
    "news",
    "help",
    "review",
    "out_of_scope",
)


class AgentState(TypedDict, total=False):
    """The graph's state object (D-28.3). ``instrument`` holds the resolved
    ORM Instrument (in-process only — never persisted); ``slots`` carries the
    trade slots plus resolution metadata (candidate/confident)."""

    conversation_id: str | None
    question: str
    history: list[dict]
    pending: dict | None
    route: str
    instrument: Instrument | None
    slots: dict
    result: dict
    citations: list[dict]
    suggested_ticket: dict | None
    answer: str


# ---------------------------------------------------------------------------
# Affirmative / negative matcher (rule-side in mock AND live mode)
# ---------------------------------------------------------------------------

_AFFIRMATIVE = re.compile(
    r"\b(yes|yeah|yep|yup|confirm|confirmed|ok|okay|sure|correct|proceed|"
    r"affirmative|go ahead)\b|対|是|はい",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"\b(no|nope|nah|cancel|negative|abort|stop|nevermind|never mind)\b|不要|いいえ",
    re.IGNORECASE,
)


def _affirmative_or_negative(question: str) -> str | None:
    """"yes/confirm/ok/sure/対/是/はい" vs "no/cancel/nope/不要/いいえ".

    Negative wins when both appear ("no, cancel"). Only consulted while a
    pending clarification/confirmation exists, so ordinary questions never
    hit this matcher.
    """
    if _NEGATIVE.search(question):
        return "negative"
    if _AFFIRMATIVE.search(question):
        return "affirmative"
    return None


# ---------------------------------------------------------------------------
# Fuzzy instrument resolution (D-28.3): exact → prefix → name substring →
# difflib close match (cutoff 0.8) over symbol+name
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)?")

# Common words that must never become instrument candidates.
_STOPWORDS = frozenset(
    """
    buy sell stocks stock shares share of at the an and or to for in on my me
    help please want would like get some order trade purchase market limit
    price quote last how much what is it its about tell show news latest
    headline sentiment moving worth value today now does do can could should
    yes no ok okay sure nope confirm cancel stocks usd
    """.split()
)


def _word_pattern(text: str) -> re.Pattern:
    """Case-insensitive match on token boundaries (works for alphanumeric
    symbols; ``(?<!\\w)``/``(?!\\w)`` so "AAPL" does not match inside "AAPL29")."""
    return re.compile(rf"(?<!\w){re.escape(text)}(?!\w)", re.IGNORECASE)


def _exact_match(
    instruments: list[Instrument], text: str
) -> Instrument | None:
    """Exact tier: symbol as a standalone token, then a full-name mention."""
    lowered = text.lower()
    for instrument in instruments:
        if _word_pattern(instrument.symbol).search(text):
            return instrument
    for instrument in instruments:
        if instrument.name.lower() in lowered:
            return instrument
    return None


def _candidate_tokens(text: str) -> list[str]:
    return [
        token
        for token in _TOKEN.findall(text.lower())
        if token not in _STOPWORDS and not token.isdigit() and len(token) >= 3
    ]


def _prefix_match(
    instruments: list[Instrument], tokens: list[str]
) -> Instrument | None:
    """Prefix tier: a question token is a prefix of a symbol ("tsl" → TSLA),
    or a symbol (length ≥ 3) is a prefix of a token ("aapl29x" → AAPL…)."""
    for token in tokens:
        for instrument in instruments:
            symbol = instrument.symbol.lower()
            if symbol.startswith(token):
                return instrument
            if len(symbol) >= 3 and token.startswith(symbol):
                return instrument
    return None


def _name_substring_match(
    instruments: list[Instrument], tokens: list[str]
) -> Instrument | None:
    """Name-substring tier: a question token appears inside an instrument
    name ("appl" ⊂ "apple" → AAPL, "micro" ⊂ "microsoft" → MSFT)."""
    for token in tokens:
        for instrument in instruments:
            if token in instrument.name.lower():
                return instrument
    return None


def _difflib_match(
    instruments: list[Instrument], tokens: list[str]
) -> Instrument | None:
    """difflib tier: get_close_matches (cutoff 0.8) over symbol+name."""
    terms: list[tuple[str, Instrument]] = []
    for instrument in instruments:
        terms.append((instrument.symbol.lower(), instrument))
        terms.append((instrument.name.lower(), instrument))
    choices = [term for term, _ in terms]
    for token in tokens:
        if len(token) < 4:  # short strings make difflib ratios meaningless
            continue
        hits = difflib.get_close_matches(token, choices, n=1, cutoff=0.8)
        if hits:
            return next(inst for term, inst in terms if term == hits[0])
    return None


async def _exact_resolve(db: AsyncSession, text: str) -> Instrument | None:
    instruments = (await db.execute(select(Instrument))).scalars().all()
    return _exact_match(list(instruments), text)


async def _fuzzy_resolve(db: AsyncSession, text: str) -> Instrument | None:
    instruments = list((await db.execute(select(Instrument))).scalars().all())
    tokens = _candidate_tokens(text)
    if not tokens:
        return None
    return (
        _prefix_match(instruments, tokens)
        or _name_substring_match(instruments, tokens)
        or _difflib_match(instruments, tokens)
    )


async def resolve_instrument_text(
    db: AsyncSession, text: str
) -> tuple[Instrument | None, bool]:
    """Full tiered resolution of one text: ``(instrument, confident)``.

    Confident only on the exact tier; prefix/name-substring/difflib hits are
    candidates the caller must clarify ("did you mean …?", D-28.3).
    """
    instrument = await _exact_resolve(db, text)
    if instrument is not None:
        return instrument, True
    instrument = await _fuzzy_resolve(db, text)
    if instrument is not None:
        return instrument, False
    return None, False


def _history_instrument(
    instruments: list[Instrument], history: list[dict]
) -> Instrument | None:
    """Pronoun/context resolution (mock): the last exact instrument mention
    in recent turns, newest turn first, prompt before response."""
    for turn in reversed(history):
        for text in (turn.get("prompt", ""), turn.get("response", "")):
            found = _exact_match(instruments, text)
            if found is not None:
                return found
    return None


# ---------------------------------------------------------------------------
# Live LLM intent classifier (strict JSON) — mock whenever no chat client
# ---------------------------------------------------------------------------

_ROUTE_SYSTEM_PROMPT = (
    "You are the intent router for the STP trading platform assistant. "
    "Classify the LAST user message into exactly one intent and extract the "
    "trade slots. Reply with STRICT JSON only — no prose, no code fences: "
    '{"intent": one of ["trade", "positions", "valuation", "transactions", '
    '"price", "news", "help", "review", "out_of_scope"], '
    '"instrument": ticker symbol or company name or null, '
    '"side": "BUY" | "SELL" | null, '
    '"quantity": number or null, '
    '"order_type": "MARKET" | "LIMIT" | null}. '
    "Use the conversation history to resolve references like 'its' or 'that "
    "stock'. Trade means the user wants to buy/sell; review means they ask "
    "for advice about their book."
)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


async def _llm_classify(
    client: LLMClient, question: str, history: list[dict]
) -> tuple[str, dict] | None:
    """Live route: strict-JSON classification (D-28.3). Any failure — network,
    non-JSON, unknown intent — falls back to the mock router (returns None)."""
    lines: list[str] = []
    for turn in history[-6:]:
        lines.append(f"User: {turn['prompt']}")
        lines.append(f"Assistant: {turn['response'][:200]}")
    lines.append(f"User: {question}")
    user = (
        "Conversation (latest last):\n"
        + "\n".join(lines)
        + "\n\nClassify the LAST user message. JSON only."
    )
    try:
        raw = await client.chat(
            [
                {"role": "system", "content": _ROUTE_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            max_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001 — per-call mock fallback
        logger.warning("LLM route classification failed (%s); mock fallback", exc)
        return None
    fenced = _FENCE.search(raw)
    try:
        data = json.loads(fenced.group(1) if fenced else raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("LLM route returned non-JSON (%r); mock fallback", raw[:120])
        return None
    if not isinstance(data, dict) or data.get("intent") not in INTENTS:
        logger.warning("LLM route returned bad intent (%r); mock fallback", data)
        return None
    side = str(data.get("side") or "").upper()
    order_type = str(data.get("order_type") or "").upper()
    quantity = data.get("quantity")
    try:
        quantity = float(quantity) if quantity is not None else None
    except (TypeError, ValueError):
        quantity = None
    slots = {
        "instrument": data.get("instrument") or None,
        "side": side if side in ("BUY", "SELL") else None,
        "quantity": quantity,
        "order_type": order_type if order_type in ("MARKET", "LIMIT") else None,
    }
    return data["intent"], slots


# ---------------------------------------------------------------------------
# The graph (built per call: nodes close over engine/db/session)
# ---------------------------------------------------------------------------


def _describe_ticket(ticket: dict) -> str:
    """Human-readable one-liner for a suggested ticket dict."""
    side = ticket.get("side") or "?"
    quantity = ticket.get("quantity")
    qty_text = f"{float(quantity):g} × " if quantity is not None else ""
    order_type = ticket.get("order_type")
    tail = f", {order_type} order" if order_type else ""
    return f"{side} {qty_text}{ticket.get('instrument', '?')}{tail}"


def _build_graph(engine: AssistantEngine, db: AsyncSession, session: SessionData):
    """Compile the design-28 graph with node closures over the request scope."""
    # Imported lazily: the package module is fully initialized by the time the
    # engine delegates here, and tests import this submodule after the package.
    from app.modules import assistant as pkg

    async def load_context(state: AgentState) -> dict:
        """History (last 10 turns) + pending state; skipped without an id."""
        conversation_id = state.get("conversation_id")
        if not conversation_id:
            return {"history": [], "pending": None}
        rows = (
            (
                await db.execute(
                    select(AssistantInteraction)
                    .where(AssistantInteraction.user_id == session.user_id)
                    .order_by(AssistantInteraction.created_at.desc())
                    .limit(_HISTORY_SCAN_ROWS)
                )
            )
            .scalars()
            .all()
        )
        turns = [
            row
            for row in rows
            if (row.grounded_refs or {}).get("conversation_id") == conversation_id
        ][:HISTORY_TURNS]
        history = [
            {"prompt": row.prompt, "response": row.response}
            for row in reversed(turns)
        ]
        record = await db.get(ConversationState, conversation_id)
        pending = record.state or None if record is not None else None
        return {"history": history, "pending": pending}

    async def route(state: AgentState) -> dict:
        """Pending resolution first (rule-side always), then new intents."""
        pending = state.get("pending")
        if pending:
            verdict = _affirmative_or_negative(state["question"])
            if verdict == "affirmative":
                return {"route": "confirm_pending"}
            if verdict == "negative":
                return {"route": "cancel_pending"}
            # Not an answer to the pending question: treat as a fresh intent;
            # the pending state is cleared by whichever node handles it.
        client = pkg._RUNTIME.llm_client
        if client is not None:
            classified = await _llm_classify(
                client, state["question"], state.get("history") or []
            )
            if classified is not None:
                intent, slots = classified
                return {"route": intent, "slots": slots}
        return await _mock_route(state)

    async def _mock_route(state: AgentState) -> dict:
        """Regex router (design 07 chain, unchanged) + history inheritance."""
        question = state["question"]
        lowered = question.lower()
        instruments = list((await db.execute(select(Instrument))).scalars().all())
        instrument = _exact_match(instruments, question)
        if instrument is None:
            instrument = _history_instrument(instruments, state.get("history") or [])

        review_match = bool(pkg._REVIEW_WORDS.search(lowered))
        if (
            review_match
            and pkg._LEADING_QUESTION_WORD.match(lowered)
            and pkg._has_platform_word(lowered)
        ):
            # "how do I review a report?" is a usage question, not advice.
            review_match = False

        if review_match:
            intent = "review"
        elif pkg._TRADE_WORDS.search(lowered):
            intent = "trade"
        elif "position" in lowered or "holding" in lowered:
            intent = "positions"
        elif any(
            word in lowered for word in ("valuation", "value", "p&l", "pnl", "worth")
        ):
            intent = "valuation"
        elif any(word in lowered for word in ("transaction", "trade", "execution")):
            intent = "transactions"
        elif instrument is not None and any(
            word in lowered for word in ("price", "quote", "last", "how much")
        ):
            intent = "price"
        elif any(
            word in lowered for word in ("news", "headline", "sentiment", "moving")
        ):
            intent = "news"
        elif pkg._looks_like_platform_help(lowered):
            intent = "help"
        else:
            intent = "out_of_scope"
        return {"route": intent, "instrument": instrument}

    async def resolve_instrument(state: AgentState) -> dict:
        """Fuzzy resolution for the trade path; LLM/history instruments for
        the question intents. Sets slots {side, quantity, order_type,
        candidate, confident}."""
        question = state["question"]
        slots = dict(state.get("slots") or {})
        if state["route"] != "trade":
            instrument = state.get("instrument")
            if instrument is None and slots.get("instrument"):
                instrument, _ = await resolve_instrument_text(
                    db, str(slots["instrument"])
                )
            return {"instrument": instrument}

        instrument = await _exact_resolve(db, question)
        confident = instrument is not None
        candidate: Instrument | None = None
        if instrument is None and slots.get("instrument"):
            resolved, slot_confident = await resolve_instrument_text(
                db, str(slots["instrument"])
            )
            if slot_confident:
                instrument, confident = resolved, True
            else:
                candidate = resolved
        if instrument is None and candidate is None:
            candidate = await _fuzzy_resolve(db, question)
        if instrument is None and candidate is None:
            # "buy 10 more" inherits the conversation's last instrument.
            instrument = state.get("instrument")
            confident = instrument is not None
        side = slots.get("side") or pkg._extract_side(question)
        anchor = instrument or candidate
        quantity = (
            slots.get("quantity")
            if slots.get("quantity") is not None
            else pkg._extract_quantity(question, anchor)
        )
        order_type = slots.get("order_type") or pkg._extract_order_type(question)
        return {
            "instrument": instrument,
            "slots": {
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
                "candidate": candidate,
                "confident": confident,
            },
        }

    async def prepare_ticket(state: AgentState) -> dict:
        """Existing rule ticket logic; parks pending_confirmation (D-28.3)."""
        slots = state.get("slots") or {}
        result = await engine._handle_trade(
            db,
            session,
            state["question"],
            state.get("instrument"),
            side=slots.get("side"),
            quantity=slots.get("quantity"),
            order_type=slots.get("order_type"),
        )
        pending = None
        if result.get("suggested_ticket"):
            pending = {"pending_confirmation": {"ticket": result["suggested_ticket"]}}
        return {"route": "trade", "result": result, "pending": pending}

    async def clarify(state: AgentState) -> dict:
        """One-time "did you mean X?" for a fuzzy candidate — no ticket yet."""
        slots = state.get("slots") or {}
        candidate: Instrument = slots["candidate"]
        side = slots.get("side") or "BUY"
        quantity = slots.get("quantity")
        order_type = slots.get("order_type")
        pending = {
            "pending_clarification": {
                "instrument": candidate.symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
            }
        }
        qty_text = f"{float(quantity):g} " if quantity is not None else ""
        bits = f"{side} {qty_text}{candidate.symbol} ({candidate.name})"
        if order_type:
            bits += f" at {order_type}"
        answer = (
            f"Just to be sure: did you mean {candidate.symbol} "
            f"({candidate.name})? If so I'll prepare a suggested ticket — "
            f"{bits} — for you to review and confirm in the order ticket; I "
            f"never place orders myself. "
            f'Reply "yes" to continue or "no" to cancel.'
        )
        return {
            "route": "trade",
            "result": {"answer": answer, "citations": [], "suggested_ticket": None},
            "pending": pending,
        }

    async def confirm(state: AgentState) -> dict:
        """Affirmative to a pending state (D-28.3): a clarified instrument
        becomes the suggested ticket (parked as pending_confirmation); an
        already-prepared ticket is finalized as a draft — still just a
        prefill, the user confirms in the UI (FR-AI-003)."""
        pending = state.get("pending") or {}
        if "pending_clarification" in pending:
            slots = pending["pending_clarification"]
            instrument = (
                await db.execute(
                    select(Instrument).where(
                        Instrument.symbol == slots.get("instrument")
                    )
                )
            ).scalar_one_or_none()
            if instrument is None:  # instrument vanished: decline gracefully
                return {
                    "route": "out_of_scope",
                    "result": {
                        "answer": pkg.DECLINE_TEXT,
                        "citations": [],
                        "suggested_ticket": None,
                    },
                    "pending": None,
                }
            result = await engine._handle_trade(
                db,
                session,
                state["question"],
                instrument,
                side=slots.get("side"),
                quantity=slots.get("quantity"),
                order_type=slots.get("order_type"),
            )
            new_pending = None
            if result.get("suggested_ticket"):
                new_pending = {
                    "pending_confirmation": {"ticket": result["suggested_ticket"]}
                }
            return {
                "route": "trade",
                "instrument": instrument,
                "result": result,
                "pending": new_pending,
            }
        if "pending_confirmation" in pending:
            ticket = pending["pending_confirmation"].get("ticket") or {}
            answer = (
                f"Confirmed — the suggested ticket "
                f"{_describe_ticket(ticket)} is ready as a prefill in the "
                f"order ticket. Review it there and hit submit to send the "
                f"order; I never place orders myself, so nothing is booked "
                f"until you do."
            )
            return {
                "route": "trade",
                "result": {
                    "answer": answer,
                    "citations": [],
                    "suggested_ticket": ticket or None,
                },
                "pending": None,
            }
        # Route guard means a pending always exists here; be safe anyway.
        return {
            "route": "out_of_scope",
            "result": {
                "answer": pkg.DECLINE_TEXT,
                "citations": [],
                "suggested_ticket": None,
            },
            "pending": None,
        }

    async def cancel(state: AgentState) -> dict:
        """Negative to a pending state: clear it politely, no ticket."""
        answer = (
            "Understood — I've discarded the pending trade idea; nothing was "
            "prepared or submitted. Just tell me what you'd like to do next."
        )
        return {
            "route": "trade",
            "result": {"answer": answer, "citations": [], "suggested_ticket": None},
            "pending": None,
        }

    async def answer_question(state: AgentState) -> dict:
        """Read-only question intents — the engine's handlers, unchanged."""
        intent = state["route"]
        instrument = state.get("instrument")
        question = state["question"]
        if intent == "review":
            result = await engine._handle_review(db, session, question, instrument)
        elif intent == "positions":
            result = await engine._handle_positions(db, session)
        elif intent == "valuation":
            result = await engine._handle_valuation(db, session)
        elif intent == "transactions":
            result = await engine._handle_transactions(db, session)
        elif intent == "price" and instrument is not None:
            result = await engine._handle_price(db, instrument)
        elif intent == "news":
            result = await engine._handle_news(db, instrument)
        elif intent == "help":
            result = await engine._handle_help(db, question)
        else:
            intent = "out_of_scope"
            result = {
                "answer": pkg.DECLINE_TEXT,
                "citations": [],
                "suggested_ticket": None,
            }
        return {"route": intent, "result": result, "pending": None}

    async def persist_state(state: AgentState) -> dict:
        """Write/clear the pending action (D-28.2) so it survives restarts.

        Commits on the request session: the SSE route never commits its
        request-scoped db (it persists the interaction on a fresh session),
        so this is the only way the pending state becomes durable there; on
        the one-shot route the interaction + audit commit separately after.
        """
        conversation_id = state.get("conversation_id")
        if not conversation_id:
            return {}
        pending = state.get("pending")
        record = await db.get(ConversationState, conversation_id)
        if pending:
            if record is None:
                db.add(
                    ConversationState(conversation_id=conversation_id, state=pending)
                )
            else:
                record.state = pending
                record.updated_at = utcnow()
        elif record is not None:
            record.state = {}
            record.updated_at = utcnow()
        await db.commit()
        return {}

    def _after_route(state: AgentState) -> str:
        if state["route"] == "confirm_pending":
            return "confirm"
        if state["route"] == "cancel_pending":
            return "cancel"
        return "resolve_instrument"

    def _after_resolve(state: AgentState) -> str:
        if state["route"] == "trade":
            slots = state.get("slots") or {}
            if slots.get("candidate") is not None and not slots.get("confident"):
                return "clarify"
            return "prepare_ticket"
        return "answer_question"

    builder = StateGraph(AgentState)
    builder.add_node("load_context", load_context)
    builder.add_node("route", route)
    builder.add_node("resolve_instrument", resolve_instrument)
    builder.add_node("prepare_ticket", prepare_ticket)
    builder.add_node("clarify", clarify)
    builder.add_node("confirm", confirm)
    builder.add_node("cancel", cancel)
    builder.add_node("answer_question", answer_question)
    builder.add_node("persist_state", persist_state)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "route")
    builder.add_conditional_edges(
        "route",
        _after_route,
        {
            "confirm": "confirm",
            "cancel": "cancel",
            "resolve_instrument": "resolve_instrument",
        },
    )
    builder.add_conditional_edges(
        "resolve_instrument",
        _after_resolve,
        {
            "prepare_ticket": "prepare_ticket",
            "clarify": "clarify",
            "answer_question": "answer_question",
        },
    )
    for node in ("prepare_ticket", "clarify", "confirm", "cancel", "answer_question"):
        builder.add_edge(node, "persist_state")
    builder.add_edge("persist_state", END)
    return builder.compile()


async def run_agent(
    engine: AssistantEngine,
    db: AsyncSession,
    session: SessionData,
    question: str,
    conversation_id: str | None,
) -> tuple[str, dict]:
    """One turn through the design-28 graph; returns ``(intent, result)`` —
    the exact contract ``AssistantEngine.ground`` has always had."""
    graph = _build_graph(engine, db, session)
    final = await graph.ainvoke(
        {"conversation_id": conversation_id, "question": question}
    )
    return final["route"], final["result"]
