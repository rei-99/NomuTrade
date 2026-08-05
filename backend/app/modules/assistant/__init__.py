"""GenAI assistant (DESIGN 07 + 27, FR-AI): advisory-only, grounded in caller data.

Rule-based responder (AssistantEngine) computing answers directly from the DB
through the same read-only tool whitelist the LLM uses when one is wired in
(get_positions / get_valuation / get_transactions / get_prices), so answers
are grounded and every figure is cited from a tool result (FR-AI-001).

LLM SEAM: AssistantEngine(llm=...) accepts an async callable
`llm(intent, tool_results, question) -> str | None` that may draft the prose;
intent detection, tool calls, citations and the ticket guardrail stay
server-side and deterministic regardless. Design 27 wires the seam at
startup: `configure(settings, llm_status)` installs a real LLM prose drafter
when the provider self-check passed (D-27.2), plus a RAG `help` intent over
the platform docs (D-27.4) and an advisory `review` intent (D-27.5). Default
and every failure path is the rule-based mock behaviour.

GUARDRAIL (FR-AI-003): the assistant NEVER places orders. Buy/sell intents
produce advice text plus a `suggested_ticket` for the UI to render into the
standard order ticket; confirmation goes through POST /orders by the user.
The assistant identity holds no trading permissions, so any direct API misuse
is denied by default and logged as a security event (DESIGN 09/17).

AGENT WORKFLOW (design 28): `AssistantEngine.answer`/`ground` delegate to the
LangGraph state graph in `agent.py` (D-28.1) — intent routing, fuzzy
instrument resolution, clarify/confirm/cancel and the read-only question
handlers run as graph nodes, with conversation memory (turn history from
AssistantInteraction rows + pending action in ConversationState, D-28.2).
The handlers below are the graph's tools and stay rule-side and
deterministic regardless of the mock/LLM split.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.core.audit import write_audit
from app.core.db import get_db
from app.core.errors import NotFound, ValidationError
from app.core.models import (
    AssistantInteraction,
    Execution,
    Instrument,
    NewsItem,
    NewsSentiment,
    Order,
    Portfolio,
    Position,
    PriceTick,
)
from app.core.security import SessionData, require_permission
from app.core.timeutil import as_utc
from app.modules.assistant.llm import MOCK_STATUS, LLMClient, validate_llm
from app.modules.assistant.rag import build_rag_index, retrieve
from app.modules.marketdata.registry import get_sim_now

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])

ASSISTANT_QUERY = "ASSISTANT_QUERY"

RECENT_TRANSACTIONS_LIMIT = 10

# Advisory disclaimer (design 27, D-27.5/D-27.6 compliance guard): attached
# inline ONLY to advice-shaped "review" answers (owner decision 2026-08-05 —
# per-message hedging on every reply was noisy; the ambient disclaimer now
# lives as a static line in the Assistant UI). The behavioral guardrail is
# unchanged: the assistant never places orders; suggestions land as ticket
# prefills the user confirms (FR-AI-003).
DISCLAIMER_TEXT = (
    "This is advisory only, not investment advice — the decision to trade is "
    "always yours."
)

_CURRENCY_SYMBOLS = {"USD": "$", "JPY": "¥", "EUR": "€", "GBP": "£"}


def _money(amount: float, currency: str) -> str:
    """Money rendering in the instrument/book currency (design 27 §D-27.5 fix)."""
    symbol = _CURRENCY_SYMBOLS.get(currency)
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{currency} {amount:,.2f}"


# ---------------------------------------------------------------------------
# Read-only tool whitelist (DESIGN 07) — run with the caller's own data scope
# ---------------------------------------------------------------------------


async def get_positions(db: AsyncSession, user_id: str) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(Position, Portfolio, Instrument)
                .join(Portfolio, Position.portfolio_id == Portfolio.portfolio_id)
                .join(Instrument, Position.instrument_id == Instrument.instrument_id)
                .where(Portfolio.owner_id == user_id)
                .order_by(Portfolio.name, Instrument.symbol)
            )
        )
        .all()
    )
    out = []
    for position, portfolio, instrument in rows:
        last = await get_latest_price(db, instrument.instrument_id)
        market_value = (
            position.quantity * last[0] if last is not None else None
        )
        out.append(
            {
                "portfolio_id": portfolio.portfolio_id,
                "portfolio": portfolio.name,
                "symbol": instrument.symbol,
                "name": instrument.name,
                "currency": instrument.currency,
                "quantity": position.quantity,
                "avg_cost": position.avg_cost,
                "last_price": last[0] if last else None,
                "last_price_ts": last[1] if last else None,
                "market_value": market_value,
            }
        )
    return out


async def get_valuation(db: AsyncSession, user_id: str) -> list[dict]:
    portfolios = (
        (
            await db.execute(
                select(Portfolio)
                .where(Portfolio.owner_id == user_id)
                .order_by(Portfolio.name)
            )
        )
        .scalars()
        .all()
    )
    positions = await get_positions(db, user_id)
    by_portfolio: dict[str, Decimal] = {}
    currency_by_portfolio: dict[str, str] = {}
    for position in positions:
        currency_by_portfolio.setdefault(position["portfolio_id"], position["currency"])
        if position["market_value"] is not None:
            by_portfolio[position["portfolio_id"]] = (
                by_portfolio.get(position["portfolio_id"], Decimal("0"))
                + position["market_value"]
            )
    return [
        {
            "portfolio_id": p.portfolio_id,
            "portfolio": p.name,
            "type": p.type,
            "cash": p.cash_balance,
            "positions_value": by_portfolio.get(p.portfolio_id, Decimal("0")),
            "total_value": p.cash_balance
            + by_portfolio.get(p.portfolio_id, Decimal("0")),
            # Book currency: the currency of its holdings; cash-only books
            # default to the platform's seeded USD books.
            "currency": currency_by_portfolio.get(p.portfolio_id, "USD"),
        }
        for p in portfolios
    ]


async def get_transactions(db: AsyncSession, user_id: str) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(Execution, Order, Instrument)
                .join(Order, Execution.order_id == Order.order_id)
                .join(Instrument, Order.instrument_id == Instrument.instrument_id)
                .where(Order.created_by == user_id)
                .order_by(Execution.executed_at.desc())
                .limit(RECENT_TRANSACTIONS_LIMIT)
            )
        )
        .all()
    )
    return [
        {
            "execution_id": execution.execution_id,
            "symbol": instrument.symbol,
            "currency": instrument.currency,
            "side": order.side,
            "quantity": execution.quantity,
            "price": execution.price,
            "executed_at": execution.executed_at,
        }
        for execution, order, instrument in rows
    ]


async def get_latest_price(
    db: AsyncSession, instrument_id: str
) -> tuple[Decimal, object] | None:
    tick = (
        await db.execute(
            select(PriceTick)
            .where(PriceTick.instrument_id == instrument_id)
            .order_by(PriceTick.ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if tick is None:
        return None
    return tick.close, tick.ts


async def get_prices(db: AsyncSession, symbol: str) -> dict | None:
    instrument = (
        await db.execute(select(Instrument).where(Instrument.symbol == symbol))
    ).scalar_one_or_none()
    if instrument is None:
        return None
    latest = await get_latest_price(db, instrument.instrument_id)
    if latest is None:
        return {"symbol": symbol, "name": instrument.name, "price": None, "ts": None}
    return {
        "symbol": symbol,
        "name": instrument.name,
        "price": latest[0],
        "ts": latest[1],
    }


async def get_news(
    db: AsyncSession, ticker: str, limit: int = 5
) -> dict:
    """Latest headlines + per-ticker sentiment for one ticker (news pack, D-14).

    Returns {"items": [...], "mean_score_7d": float|None, "latest_ts": str|None};
    the 7-day mean is relative to the latest news timestamp (news is reference
    data with its own clock, like prices — never utcnow()). While a replay
    runs, headlines beyond the simulation clock are withheld.
    """
    sim_now = get_sim_now()
    clock_filter = [NewsItem.ts <= sim_now] if sim_now is not None else []
    items = (
        (
            await db.execute(
                select(NewsItem)
                .join(NewsSentiment, NewsSentiment.news_id == NewsItem.news_id)
                .where(NewsSentiment.ticker == ticker)
                .where(*clock_filter)
                .options(selectinload(NewsItem.sentiments))
                .order_by(NewsItem.ts.desc())
                .limit(limit)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    latest_ts = await db.scalar(
        select(func.max(NewsItem.ts))
        .join(NewsSentiment, NewsSentiment.news_id == NewsItem.news_id)
        .where(NewsSentiment.ticker == ticker)
        .where(*clock_filter)
    )
    mean_score: float | None = None
    if latest_ts is not None:
        from datetime import timedelta

        mean_score = await db.scalar(
            select(func.avg(NewsSentiment.score))
            .join(NewsItem, NewsSentiment.news_id == NewsItem.news_id)
            .where(
                NewsSentiment.ticker == ticker,
                NewsItem.ts >= as_utc(latest_ts) - timedelta(days=7),
                *clock_filter,
            )
        )
        mean_score = round(float(mean_score), 4) if mean_score is not None else None
    return {
        "items": [
            {
                "news_id": item.news_id,
                "ts": as_utc(item.ts).isoformat(),
                "title": item.title,
                "sentiments": [
                    {
                        "ticker": s.ticker,
                        "score": float(s.score) if s.score is not None else None,
                        "label": s.label,
                    }
                    for s in item.sentiments
                ],
            }
            for item in items
        ],
        "mean_score_7d": mean_score,
        "latest_ts": as_utc(latest_ts).isoformat() if latest_ts is not None else None,
    }


async def get_news_summary(db: AsyncSession, instrument: Instrument) -> dict:
    """Rule-based news summary grounding for the Trading workspace (D-27.3).

    Deterministic template over the sim-clock-capped news pack — no LLM call
    (`mock: true`, `model: "rules-v1"` so the UI can badge it honestly; the
    route rewords the prose via `_reword_news_summary` when a chat model is
    live). All figures come straight from NewsItem/NewsSentiment: symbol,
    7-day sentiment mean, article count, label mix, top topics, up to 3
    driving headlines.
    """
    from datetime import timedelta

    sim_now = get_sim_now()
    clock_filter = [NewsItem.ts <= sim_now] if sim_now is not None else []
    ticker = instrument.symbol

    latest_ts = await db.scalar(
        select(func.max(NewsItem.ts))
        .join(NewsSentiment, NewsSentiment.news_id == NewsItem.news_id)
        .where(NewsSentiment.ticker == ticker, *clock_filter)
    )
    empty = {
        "symbol": ticker,
        "as_of": None,
        "sentiment_mean_7d": None,
        "article_count_7d": 0,
        "label_mix": {},
        "top_topics": [],
        "summary": (
            f"There is no news coverage for {ticker} ({instrument.name}) "
            "in the dataset window to summarise."
        ),
        "headlines": [],
        "mock": True,
        "model": "rules-v1",
    }
    if latest_ts is None:
        return empty

    window_start = as_utc(latest_ts) - timedelta(days=7)
    rows = (
        (
            await db.execute(
                select(NewsItem, NewsSentiment)
                .join(NewsSentiment, NewsSentiment.news_id == NewsItem.news_id)
                .where(
                    NewsSentiment.ticker == ticker,
                    NewsItem.ts >= window_start,
                    *clock_filter,
                )
                .order_by(NewsItem.ts.desc())
            )
        )
        .all()
    )

    scores = [float(s.score) for _i, s in rows if s.score is not None]
    mean = round(sum(scores) / len(scores), 4) if scores else None
    label_mix: dict[str, int] = {}
    topics: dict[str, int] = {}
    for item, s in rows:
        if s.label:
            label_mix[s.label] = label_mix.get(s.label, 0) + 1
        for topic in item.topics or []:
            if topic:
                topics[topic] = topics.get(topic, 0) + 1
    top_topics = sorted(topics, key=topics.get, reverse=True)[:3]

    if mean is None:
        tone = "unscored"
    elif mean >= 0.2:
        tone = "bullish"
    elif mean >= 0.05:
        tone = "mildly bullish"
    elif mean > -0.05:
        tone = "neutral"
    elif mean > -0.2:
        tone = "mildly bearish"
    else:
        tone = "bearish"

    seen: set[str] = set()
    headlines: list[dict] = []
    for item, s in rows:
        if item.news_id in seen or len(headlines) >= 3:
            continue
        seen.add(item.news_id)
        headlines.append(
            {
                "ts": as_utc(item.ts).isoformat(),
                "title": item.title,
                "label": s.label,
                "score": float(s.score) if s.score is not None else None,
            }
        )

    article_count = len({item.news_id for item, _s in rows})
    if article_count == 0:
        return empty

    # Prose is deliberately the one-line coverage summary only: themes and
    # notable headlines ship in the structured `top_topics` / `headlines`
    # fields, which the panel renders as chips and a citation list. Embedding
    # them in the prose too made every item appear three times in the UI.
    parts = [
        f"{ticker} coverage this week is {tone}"
        + (f" (mean sentiment {mean:+.2f} across {article_count} articles)."
           if mean is not None else f" across {article_count} articles."),
    ]
    return {
        "symbol": ticker,
        "as_of": as_utc(latest_ts).isoformat(),
        "sentiment_mean_7d": mean,
        "article_count_7d": article_count,
        "label_mix": label_mix,
        "top_topics": top_topics,
        "summary": " ".join(parts),
        "headlines": headlines,
        "mock": True,
        "model": "rules-v1",
    }


# ---------------------------------------------------------------------------
# AssistantEngine — rule-based intents with an LLM seam
# ---------------------------------------------------------------------------

_TRADE_WORDS = re.compile(r"\b(buy|sell)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# Review intent (design 27, D-27.5): advisory "should I …" questions about the
# caller's own book. Checked BEFORE the trade intent so "should I sell MSFT?"
# is advice (no ticket) while "buy 100 TSLA" stays a ticket draft.
_REVIEW_WORDS = re.compile(
    r"\b(should i|review|worth\s+\w*ing|trim|hold or sell|sell or hold|"
    r"rebalance|take profit|cut losses?)\b",
    re.IGNORECASE,
)

# Help intent (design 27, D-27.4): how-do-I questions about using the platform,
# answered by RAG over the project docs. Checked AFTER data intents so it
# never steals trade/positions/news questions.
_QUESTION_WORD = re.compile(
    r"\b(how|what|where|when|who|which|why|can|could|does|do|is|are|explain)\b",
    re.IGNORECASE,
)
_LEADING_QUESTION_WORD = re.compile(
    r"^\s*(how|what|where|when|who|which|why|can|could|does|do|is|are)\b",
    re.IGNORECASE,
)
_PLATFORM_WORDS = (
    "approve", "approval", "access", "role", "grant", "tab", "report",
    "portfolio", "alert", "persona", "login", "log in", "order", "settle",
    "settlement", "demo", "notification", "audit", "permission", "break-glass",
    "break glass", "dashboard", "workspace", "platform", "system", "ticket",
    "paper", "kpi", "admin", "assistant", "request", "entitlement",
    "instrument",
)


def _has_platform_word(lowered: str) -> bool:
    return any(word in lowered for word in _PLATFORM_WORDS)


def _looks_like_platform_help(lowered: str) -> bool:
    """Platform-related usage question (also catches DECLINE-looking phrasing)."""
    if not _has_platform_word(lowered):
        return False
    return bool(_QUESTION_WORD.search(lowered)) or "?" in lowered or "tell me" in lowered


DECLINE_TEXT = (
    "I'm the STP platform assistant — I can answer questions about your "
    "positions, portfolio valuation, recent transactions, latest prices and "
    "market news/sentiment, and I can prepare (but never place) trade "
    "tickets. Your question looks outside that scope; could you rephrase it?"
)


# ---------------------------------------------------------------------------
# Trade-slot extraction (shared by _handle_trade and the design-28 agent graph)
# ---------------------------------------------------------------------------


def _extract_side(question: str) -> str:
    """BUY/SELL keyword; BUY when ambiguous ("purchase 10 AAPL")."""
    return "SELL" if "sell" in question.lower() else "BUY"


def _extract_quantity(question: str, instrument: Instrument | None) -> float | None:
    """First number that is not part of the symbol/name mention."""
    scrubbed = question.lower()
    if instrument is not None:
        scrubbed = scrubbed.replace(instrument.symbol.lower(), " ").replace(
            instrument.name.lower(), " "
        )
    match = _NUMBER.search(scrubbed)
    return float(match.group()) if match else None


def _extract_order_type(question: str) -> str | None:
    """"at market price" → MARKET, "limit" → LIMIT, else unspecified."""
    lowered = question.lower()
    if "market" in lowered:
        return "MARKET"
    if "limit" in lowered:
        return "LIMIT"
    return None


class AssistantEngine:
    """Advisory-only responder. See module docstring for the LLM seam."""

    def __init__(
        self,
        llm: Callable[[str, dict, str], Awaitable[str | None]] | None = None,
    ):
        self._llm = llm

    def set_llm(
        self, llm: Callable[[str, dict, str], Awaitable[str | None]] | None
    ) -> None:
        """Install/remove the LLM prose seam (design 27 startup wiring)."""
        self._llm = llm

    async def ground(
        self,
        db: AsyncSession,
        session: SessionData,
        question: str,
        conversation_id: str | None = None,
    ) -> tuple[str, dict]:
        """Intent routing + rule-based grounding — NO LLM prose drafting.

        Returns ``(intent, result)`` where result carries the rule answer,
        citations and suggested_ticket. The streaming route calls this so all
        DB work finishes before the first byte goes out; ``answer()`` adds
        the optional LLM reword on top for the one-shot route.

        Design 28: delegates to the LangGraph agent graph (``agent.py``).
        With a ``conversation_id`` the graph loads turn history and any
        pending clarification/confirmation (conversation memory, D-28.2);
        without one it answers statelessly, exactly as before.
        """
        from app.modules.assistant.agent import run_agent

        return await run_agent(self, db, session, question, conversation_id)

    async def answer(
        self,
        db: AsyncSession,
        session: SessionData,
        question: str,
        conversation_id: str | None = None,
    ) -> dict:
        intent, result = await self.ground(db, session, question, conversation_id)
        if self._llm is not None:  # prose only; data and citations stay ours
            drafted = await self._llm(intent, result, question)
            if drafted:
                result["answer"] = drafted
        return result

    # -- intent handlers ----------------------------------------------------

    async def _handle_trade(
        self,
        db: AsyncSession,
        session: SessionData,
        question: str,
        instrument: Instrument | None,
        *,
        side: str | None = None,
        quantity: float | None = None,
        order_type: str | None = None,
    ) -> dict:
        """Rule ticket builder (also the design-28 graph's prepare_ticket
        tool): parse side/quantity/order_type from the question, or take them
        as explicit overrides when the graph supplies confirmed slots."""
        side = side or _extract_side(question)
        if quantity is None:
            # Quantity: first number that is not part of the symbol/name mention.
            quantity = _extract_quantity(question, instrument)
        order_type = order_type or _extract_order_type(question)

        portfolio = (
            (
                await db.execute(
                    select(Portfolio)
                    .where(Portfolio.owner_id == session.user_id)
                    .order_by(Portfolio.created_at)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        citations: list[dict] = []
        price_note = ""
        if instrument is not None:
            latest = await get_latest_price(db, instrument.instrument_id)
            if latest is not None:
                price, ts = latest
                citations.append(
                    {
                        "kind": "price",
                        "ref": instrument.symbol,
                        "figures": {
                            "symbol": instrument.symbol,
                            "price": float(price),
                            "ts": as_utc(ts).isoformat(),
                        },
                    }
                )
                price_note = f" Latest price: {_money(float(price), instrument.currency)}."
            # Advisory grounding (design 27, D-27.5): the caller's position in
            # this instrument (if any) and its news sentiment go to the LLM as
            # citations — it rewords, it never invents.
            held = [
                p
                for p in await get_positions(db, session.user_id)
                if p["symbol"] == instrument.symbol
            ]
            for p in held:
                citations.append(
                    {
                        "kind": "position",
                        "ref": f"{p['portfolio_id']}:{p['symbol']}",
                        "figures": {
                            "portfolio": p["portfolio"],
                            "symbol": p["symbol"],
                            "quantity": float(p["quantity"]),
                            "avg_cost": float(p["avg_cost"]),
                            "market_value": (
                                float(p["market_value"])
                                if p["market_value"] is not None
                                else None
                            ),
                        },
                    }
                )
            news = await get_news(db, instrument.symbol, limit=3)
            if news["items"]:
                citations.append(
                    {
                        "kind": "news",
                        "ref": instrument.symbol,
                        "figures": {
                            "mean_score_7d": news["mean_score_7d"],
                            "latest_ts": news["latest_ts"],
                            "headlines": [item["title"] for item in news["items"]],
                        },
                    }
                )

        ticket = None
        if instrument is not None:
            ticket = {
                "portfolio_id": portfolio.portfolio_id if portfolio else None,
                "instrument": instrument.symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
            }
            qty_text = f"{quantity:g} " if quantity is not None else ""
            order_type_text = f" at {order_type}" if order_type else ""
            answer = (
                f"I've prepared a suggested ticket: {side} {qty_text}"
                f"{instrument.symbol} ({instrument.name}){order_type_text}."
                f"{price_note} Review it in the order ticket and confirm to "
                f"submit — nothing is booked until you do."
            )
        else:
            answer = (
                f"Sure — tell me which instrument you want to "
                f"{side.lower()} (symbol or name) and I'll prepare a "
                f"suggested ticket for you to review and confirm in the "
                f"order ticket."
            )
        return {"answer": answer, "citations": citations, "suggested_ticket": ticket}

    async def _handle_positions(self, db: AsyncSession, session: SessionData) -> dict:
        positions = await get_positions(db, session.user_id)
        citations = [
            {
                "kind": "position",
                "ref": f"{p['portfolio_id']}:{p['symbol']}",
                "figures": {
                    "portfolio": p["portfolio"],
                    "symbol": p["symbol"],
                    "quantity": float(p["quantity"]),
                    "avg_cost": float(p["avg_cost"]),
                    "last_price": float(p["last_price"]) if p["last_price"] else None,
                    "market_value": (
                        float(p["market_value"]) if p["market_value"] is not None else None
                    ),
                },
            }
            for p in positions
        ]
        if not positions:
            answer = "You currently hold no positions; your portfolios are fully in cash."
        else:
            parts = [
                f"{float(p['quantity']):g} × {p['symbol']} ({p['name']}) in "
                f"'{p['portfolio']}'"
                + (
                    f" worth {_money(float(p['market_value']), p['currency'])}"
                    if p["market_value"] is not None
                    else " (no recent price)"
                )
                for p in positions
            ]
            answer = (
                f"You hold {len(positions)} position(s): " + "; ".join(parts) + "."
            )
        return {"answer": answer, "citations": citations, "suggested_ticket": None}

    async def _handle_valuation(self, db: AsyncSession, session: SessionData) -> dict:
        valuations = await get_valuation(db, session.user_id)
        citations = [
            {
                "kind": "valuation",
                "ref": v["portfolio_id"],
                "figures": {
                    "portfolio": v["portfolio"],
                    "cash": float(v["cash"]),
                    "positions_value": float(v["positions_value"]),
                    "total_value": float(v["total_value"]),
                    "currency": v["currency"],
                },
            }
            for v in valuations
        ]
        if not valuations:
            answer = "You don't have any portfolios yet."
        else:
            total = sum(v["total_value"] for v in valuations)
            currency = valuations[0]["currency"]
            parts = [
                f"'{v['portfolio']}' {_money(float(v['total_value']), v['currency'])} "
                f"(cash {_money(float(v['cash']), v['currency'])}, "
                f"positions {_money(float(v['positions_value']), v['currency'])})"
                for v in valuations
            ]
            answer = (
                f"Your portfolios are worth a total of {_money(float(total), currency)}: "
                + "; ".join(parts)
                + "."
            )
        return {"answer": answer, "citations": citations, "suggested_ticket": None}

    async def _handle_transactions(self, db: AsyncSession, session: SessionData) -> dict:
        transactions = await get_transactions(db, session.user_id)
        citations = [
            {
                "kind": "transaction",
                "ref": t["execution_id"],
                "figures": {
                    "symbol": t["symbol"],
                    "side": t["side"],
                    "quantity": float(t["quantity"]),
                    "price": float(t["price"]),
                    "executed_at": as_utc(t["executed_at"]).isoformat(),
                },
            }
            for t in transactions
        ]
        if not transactions:
            answer = "You have no executions yet."
        else:
            parts = [
                f"{t['side']} {float(t['quantity']):g} {t['symbol']} @ "
                f"{_money(float(t['price']), t['currency'])} "
                f"({as_utc(t['executed_at']).date().isoformat()})"
                for t in transactions
            ]
            answer = (
                f"Your {len(transactions)} most recent execution(s): "
                + "; ".join(parts)
                + "."
            )
        return {"answer": answer, "citations": citations, "suggested_ticket": None}

    async def _handle_price(self, db: AsyncSession, instrument: Instrument) -> dict:
        latest = await get_latest_price(db, instrument.instrument_id)
        if latest is None:
            return {
                "answer": f"I don't have any price data for {instrument.symbol} ({instrument.name}) yet.",
                "citations": [],
                "suggested_ticket": None,
            }
        price, ts = latest
        citation = {
            "kind": "price",
            "ref": instrument.symbol,
            "figures": {
                "symbol": instrument.symbol,
                "name": instrument.name,
                "price": float(price),
                "ts": as_utc(ts).isoformat(),
            },
        }
        answer = (
            f"{instrument.symbol} ({instrument.name}) last traded at "
            f"{_money(float(price), instrument.currency)} as of {as_utc(ts).isoformat()}."
        )
        return {"answer": answer, "citations": [citation], "suggested_ticket": None}

    async def _handle_news(
        self, db: AsyncSession, instrument: Instrument | None
    ) -> dict:
        """News/sentiment intent (dataset news pack, D-15). Grounded in
        NewsItem/NewsSentiment only; declines explicitly when there is no
        news for the asked scope (FR-AI-001)."""
        if instrument is not None:
            news = await get_news(db, instrument.symbol)
            if not news["items"]:
                return {
                    "answer": (
                        f"I don't have any news for {instrument.symbol} "
                        f"({instrument.name}) in the dataset window."
                    ),
                    "citations": [],
                    "suggested_ticket": None,
                }
            sentiment_text = (
                f"average sentiment over the last 7 covered days is "
                f"{news['mean_score_7d']:+.2f}"
                if news["mean_score_7d"] is not None
                else "no scored sentiment in the last 7 covered days"
            )
            headlines = "; ".join(
                f"- {item['title']} ({item['ts'][:10]})"
                for item in news["items"][:3]
            )
            citations = [
                {
                    "kind": "news",
                    "ref": instrument.symbol,
                    "figures": {
                        "title": item["title"],
                        "ts": item["ts"],
                        "sentiments": item["sentiments"],
                    },
                }
                for item in news["items"]
            ]
            answer = (
                f"For {instrument.symbol} ({instrument.name}): {sentiment_text}. "
                f"Latest headlines: {headlines}"
            )
            return {"answer": answer, "citations": citations, "suggested_ticket": None}

        # Market-wide overview across platform tickers.
        instruments = (await db.execute(select(Instrument))).scalars().all()
        parts: list[str] = []
        citations: list[dict] = []
        for inst in instruments:
            news = await get_news(db, inst.symbol, limit=2)
            if news["mean_score_7d"] is not None:
                parts.append(f"{inst.symbol} {news['mean_score_7d']:+.2f}")
            citations.extend(
                {
                    "kind": "news",
                    "ref": inst.symbol,
                    "figures": {
                        "title": item["title"],
                        "ts": item["ts"],
                        "sentiments": item["sentiments"],
                    },
                }
                for item in news["items"]
            )
        if not citations:
            return {
                "answer": "There is no market news in the dataset window to summarise.",
                "citations": [],
                "suggested_ticket": None,
            }
        answer = (
            "7-day average news sentiment by ticker: "
            + ", ".join(parts)
            + ". Positive values lean bullish; see the cited headlines for detail."
        )
        return {"answer": answer, "citations": citations, "suggested_ticket": None}

    async def _handle_review(
        self,
        db: AsyncSession,
        session: SessionData,
        question: str,
        instrument: Instrument | None,
    ) -> dict:
        """Advisory review intent (design 27, D-27.5): the caller's positions +
        valuation risk KPIs (concentration, VaR/ES, drawdown, bond duration) +
        news sentiment as grounding. Mock mode answers with a rule-based KPI
        summary; a live LLM rewords it via the engine seam. Advisory only —
        never a ticket, never an order."""
        from app.modules.portfolios import valuation as pv

        positions = await get_positions(db, session.user_id)
        portfolios = (
            (
                await db.execute(
                    select(Portfolio)
                    .where(Portfolio.owner_id == session.user_id)
                    .order_by(Portfolio.name)
                )
            )
            .scalars()
            .all()
        )

        citations: list[dict] = []
        kpi_summaries: list[dict] = []
        for portfolio in portfolios:
            valuations = await pv.value_positions(db, portfolio.portfolio_id)
            market_value = sum(
                (v.market_value for v in valuations if v.market_value is not None),
                Decimal("0"),
            )
            top = max(
                (v.market_value for v in valuations if v.market_value is not None),
                default=None,
            )
            concentration = (
                float(top / market_value * 100) if market_value and top else 0.0
            )
            currency = valuations[0].instrument.currency if valuations else "USD"
            kpis = {
                "portfolio": portfolio.name,
                "currency": currency,
                "cash": float(portfolio.cash_balance),
                "market_value": float(market_value),
                "total_value": float(portfolio.cash_balance + market_value),
                "concentration_pct": round(concentration, 2),
                "volatility_annualized_pct": await pv.annualized_volatility_pct(
                    db, portfolio.portfolio_id
                ),
                "var_95_1d_pct": await pv.var_95_1d_pct(db, portfolio.portfolio_id),
                "es_95_1d_pct": await pv.expected_shortfall_95_1d_pct(
                    db, portfolio.portfolio_id
                ),
                "sharpe_ratio": await pv.sharpe_ratio(db, portfolio.portfolio_id),
                "max_drawdown_pct": await pv.max_drawdown_pct(
                    db, portfolio.portfolio_id
                ),
                **pv.bond_book_metrics(valuations),
            }
            kpi_summaries.append(kpis)
            citations.append(
                {"kind": "valuation", "ref": portfolio.portfolio_id, "figures": kpis}
            )
        for p in positions:
            citations.append(
                {
                    "kind": "position",
                    "ref": f"{p['portfolio_id']}:{p['symbol']}",
                    "figures": {
                        "portfolio": p["portfolio"],
                        "symbol": p["symbol"],
                        "quantity": float(p["quantity"]),
                        "avg_cost": float(p["avg_cost"]),
                        "last_price": (
                            float(p["last_price"]) if p["last_price"] else None
                        ),
                        "market_value": (
                            float(p["market_value"])
                            if p["market_value"] is not None
                            else None
                        ),
                    },
                }
            )

        instrument_grounding: dict | None = None
        if instrument is not None:
            latest = await get_latest_price(db, instrument.instrument_id)
            news = await get_news(db, instrument.symbol, limit=3)
            held = next(
                (p for p in positions if p["symbol"] == instrument.symbol), None
            )
            instrument_grounding = {
                "symbol": instrument.symbol,
                "name": instrument.name,
                "currency": instrument.currency,
                "held_quantity": float(held["quantity"]) if held else 0.0,
                "last_price": float(latest[0]) if latest else None,
                "last_price_ts": as_utc(latest[1]).isoformat() if latest else None,
                "news_mean_score_7d": news["mean_score_7d"],
                "recent_headlines": [item["title"] for item in news["items"]],
            }
            if latest is not None:
                citations.append(
                    {
                        "kind": "price",
                        "ref": instrument.symbol,
                        "figures": {
                            "symbol": instrument.symbol,
                            "price": float(latest[0]),
                            "ts": as_utc(latest[1]).isoformat(),
                        },
                    }
                )
            citations.extend(
                {
                    "kind": "news",
                    "ref": instrument.symbol,
                    "figures": {
                        "title": item["title"],
                        "ts": item["ts"],
                        "sentiments": item["sentiments"],
                    },
                }
                for item in news["items"]
            )

        # Mock review: compact rule-based KPI summary (the LLM, when wired,
        # rewords this same grounding through the engine prose seam).
        parts = ["Advisory review (rule-based — mock LLM)."]
        if positions:
            holdings = "; ".join(
                f"{float(p['quantity']):g} × {p['symbol']} in '{p['portfolio']}'"
                + (
                    f" worth {_money(float(p['market_value']), p['currency'])}"
                    if p["market_value"] is not None
                    else ""
                )
                for p in positions
            )
            parts.append(f"You hold {len(positions)} position(s): {holdings}.")
        else:
            parts.append(
                "You currently hold no positions; your portfolios are fully in cash."
            )
        for kpis in kpi_summaries:
            bits = [
                f"total value {_money(kpis['total_value'], kpis['currency'])}",
                f"top-holding concentration {kpis['concentration_pct']:.1f}%",
            ]
            if kpis["var_95_1d_pct"] is not None:
                bits.append(f"1-day 95% VaR {kpis['var_95_1d_pct']:.2f}%")
            if kpis["es_95_1d_pct"] is not None:
                bits.append(f"expected shortfall {kpis['es_95_1d_pct']:.2f}%")
            if kpis["volatility_annualized_pct"] is not None:
                bits.append(
                    f"annualized volatility {kpis['volatility_annualized_pct']:.1f}%"
                )
            if kpis["max_drawdown_pct"] is not None:
                bits.append(f"max drawdown {kpis['max_drawdown_pct']:.1f}%")
            if kpis["sharpe_ratio"] is not None:
                bits.append(f"Sharpe {kpis['sharpe_ratio']:.2f}")
            if kpis.get("bond_wtd_ytm_pct") is not None:
                bits.append(f"bond YTM {kpis['bond_wtd_ytm_pct']:.2f}%")
                bits.append(
                    f"bond modified duration {kpis['bond_wtd_mod_duration']:.1f}"
                )
            parts.append(f"Portfolio '{kpis['portfolio']}': " + ", ".join(bits) + ".")
        if instrument_grounding is not None:
            ig = instrument_grounding
            details = [
                (
                    f"you hold {ig['held_quantity']:g}"
                    if ig["held_quantity"]
                    else "you hold none"
                )
            ]
            if ig["last_price"] is not None:
                details.append(f"latest price {_money(ig['last_price'], ig['currency'])}")
            if ig["news_mean_score_7d"] is not None:
                details.append(f"7-day news sentiment {ig['news_mean_score_7d']:+.2f}")
            parts.append(f"On {ig['symbol']} ({ig['name']}): " + ", ".join(details) + ".")
        parts.append(DISCLAIMER_TEXT)
        return {
            "answer": " ".join(parts),
            "citations": citations,
            "suggested_ticket": None,
            "grounding": {
                "positions": [
                    {
                        "symbol": p["symbol"],
                        "quantity": float(p["quantity"]),
                        "market_value": (
                            float(p["market_value"])
                            if p["market_value"] is not None
                            else None
                        ),
                        "currency": p["currency"],
                    }
                    for p in positions
                ],
                "kpis": kpi_summaries,
                "instrument": instrument_grounding,
            },
        }

    async def _handle_help(self, db: AsyncSession, question: str) -> dict:
        """RAG help intent (design 27, D-27.4): answer platform-usage questions
        from the project's own docs (README.md, DESIGN.md, docs/design/*.md).
        Live embeddings -> cosine retrieval; otherwise keyword token-overlap.
        Live chat -> LLM prose citing sources; otherwise the top chunks,
        trimmed, with their source names. Citations are kind "doc"."""
        settings = _RUNTIME.settings or Settings()
        embeddings_ok = _RUNTIME.llm_status.get("embeddings") == "ok"
        chunks = await retrieve(
            db, settings, question, embeddings_ok, llm_client=_RUNTIME.llm_client
        )
        if not chunks:
            return {"answer": DECLINE_TEXT, "citations": [], "suggested_ticket": None}
        citations = [
            {
                "kind": "doc",
                "ref": c["source"],
                "figures": {"chunk": _trim_passage(c["chunk"], 280)},
            }
            for c in chunks
        ]
        answer = None
        if _RUNTIME.llm_client is not None:
            excerpts = "\n\n".join(f"[{c['source']}]\n{c['chunk']}" for c in chunks)
            user = (
                "Platform documentation excerpts (the only facts you may use):\n"
                f"{excerpts}\n\nUser question: {question}\n"
                "Answer using only these excerpts and cite the sources inline "
                "as [source path]. If they do not cover the question, say so."
            )
            try:
                answer = await _RUNTIME.llm_client.chat(
                    [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=320,
                )
            except Exception as exc:  # noqa: BLE001 — per-call mock fallback
                logger.warning("LLM help answer failed (%s); mock fallback", exc)
                answer = None
        if not answer:
            mode = "semantic retrieval" if embeddings_ok else "keyword retrieval"
            lines = [
                f"Here's what the platform documentation says ({mode} — mock LLM):"
            ]
            lines.extend(
                f"{rank}. [{chunk['source']}] {_trim_passage(chunk['chunk'])}"
                for rank, chunk in enumerate(chunks, 1)
            )
            answer = "\n".join(lines)
        return {"answer": answer, "citations": citations, "suggested_ticket": None}


_FENCED_BLOCK = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_HEADING_MARK = re.compile(r"^#{1,6}\s*", re.MULTILINE)


def _trim_passage(text: str, limit: int = 320) -> str:
    """Flatten a doc chunk to a readable one-line excerpt of at most `limit`
    chars, cut at a sentence boundary when possible (mock help answers).
    Fenced code blocks (mermaid diagrams) and heading markers are stripped —
    they render as noise in chat answers."""
    text = _FENCED_BLOCK.sub(" ", text)
    text = _HEADING_MARK.sub("", text)
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx >= limit // 2:
            return cut[: idx + 1]
    return cut.rstrip() + " …"


engine = AssistantEngine()


# ---------------------------------------------------------------------------
# GenAI wiring (design 27): runtime state, strict prompts, startup configure()
# ---------------------------------------------------------------------------


class _Runtime:
    """Process-global GenAI wiring, installed by configure() at startup."""

    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.llm_status: dict = dict(MOCK_STATUS)
        self.llm_client: LLMClient | None = None


_RUNTIME = _Runtime()

_SYSTEM_PROMPT = (
    "You are the STP trading platform's assistant — a sharp desk assistant. "
    "Answer directly and confidently, in the language of the user's question "
    "(English or Japanese). Strict rules: use only the facts in the supplied "
    "grounding — never invent figures, prices, dates or events; never "
    "promise returns or give price targets; never claim to have placed an "
    "order — suggestions are always drafted as an order-ticket prefill the "
    "user confirms. Do not add disclaimers and do not refer to yourself as "
    "an AI model."
)


def _prose_messages(intent: str, result: dict, question: str) -> list[dict] | None:
    """The strict prompt pair the LLM prose seam drafts from (design 27).

    Shared by the one-shot seam (`_llm_prose`) and the streaming route, so
    both draft from the exact same system/user prompts. Returns None for
    intents that manage their own prose (help) or must stay verbatim
    (out_of_scope) — the caller keeps the rule-based answer for those.
    """
    if intent in ("help", "out_of_scope"):
        return None
    user = (
        f"Intent: {intent}\nUser question: {question}\n"
        "Grounding (JSON — the only facts you may use):\n"
        f"{json.dumps(result, ensure_ascii=False, default=str)}\n\n"
        "Rewrite the grounding's 'answer' into a natural, concise reply "
        "(at most 80 words)."
    )
    if intent == "review":
        user += f' End the reply with exactly this sentence: "{DISCLAIMER_TEXT}"'
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


async def _llm_prose(intent: str, result: dict, question: str) -> str | None:
    """Engine prose seam (design 27): the LLM rewords our grounded answer.

    Data, citations and tickets stay ours; the model only drafts prose.
    Returns None for intents that manage their own prose (help) or must stay
    verbatim (out_of_scope), and on any LLM error — the caller then keeps the
    rule-based answer (per-call resilience, D-27.2).
    """
    client = _RUNTIME.llm_client
    messages = _prose_messages(intent, result, question)
    if client is None or messages is None:
        return None
    try:
        prose = await client.chat(messages, max_tokens=320)
    except Exception as exc:  # noqa: BLE001 — per-call mock fallback
        logger.warning("LLM prose drafting failed (%s); keeping rules answer", exc)
        return None
    if not prose:
        return None
    if intent == "review" and DISCLAIMER_TEXT not in prose:
        prose = f"{prose} {DISCLAIMER_TEXT}"
    return prose


async def _llm_prose_stream(
    intent: str, result: dict, question: str
) -> AsyncIterator[str]:
    """Streaming variant of `_llm_prose`: yields the LLM's prose deltas.

    Drafts from the same strict prompts (`_prose_messages`). Unlike the
    one-shot seam, exceptions propagate — the stream route decides how to
    degrade (silent rules fallback before the first delta, honest error
    event mid-stream). Intents that keep rule prose yield nothing.
    """
    client = _RUNTIME.llm_client
    messages = _prose_messages(intent, result, question)
    if client is None or messages is None:
        return
    async for delta in client.chat_stream(messages, max_tokens=320):
        yield delta


def configure(
    settings: Settings, llm_status: dict, *, llm_client: LLMClient | None = None
) -> None:
    """Wire the GenAI seams from the startup self-check (D-27.2).

    Called once from the app lifespan (tests may call it directly with a
    fabricated llm_status / injected client). When the chat capability is
    live, the engine drafts prose through the LLM; anything else keeps the
    rule-based mock behaviour everywhere.
    """
    _RUNTIME.settings = settings
    _RUNTIME.llm_status = llm_status
    chat_live = llm_status.get("chat") == "ok"
    _RUNTIME.llm_client = llm_client or (LLMClient(settings) if chat_live else None)
    engine.set_llm(_llm_prose if _RUNTIME.llm_client is not None else None)


async def _reword_news_summary(summary: dict) -> dict:
    """D-27.3: a live chat model rewords the rules brief (<=60 words, figures
    verbatim). The rules dict is returned untouched in mock mode and on any
    LLM error — structured fields are always computed either way."""
    client = _RUNTIME.llm_client
    settings = _RUNTIME.settings
    if client is None or settings is None or not summary.get("article_count_7d"):
        return summary
    grounding = {
        key: value
        for key, value in summary.items()
        if key not in ("summary", "mock", "model")
    }
    user = (
        "News grounding for one instrument (JSON — the only figures you may "
        f"use):\n{json.dumps(grounding, ensure_ascii=False, default=str)}\n\n"
        "Write a short market-news brief (at most 60 words) in an advisory "
        "tone."
    )
    try:
        prose = await client.chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            max_tokens=180,
        )
    except Exception as exc:  # noqa: BLE001 — per-call rules fallback
        logger.warning("LLM news summary failed (%s); keeping rules summary", exc)
        return summary
    if not prose:
        return summary
    return {
        **summary,
        "summary": prose,
        "mock": False,
        "model": settings.LLM_CHAT_MODEL,
    }


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


class AssistantQueryRequest(BaseModel):
    question: str
    conversation_id: str | None = None


@router.post("/assistant/query")
async def query_assistant(
    body: AssistantQueryRequest,
    session: SessionData = Depends(require_permission("ASSISTANT_USE")),
    db: AsyncSession = Depends(get_db),
):
    question = body.question.strip()
    if not question:
        raise ValidationError("question must not be empty")
    conversation_id = body.conversation_id or uuid.uuid4().hex

    result = await engine.answer(db, session, question, conversation_id)

    interaction = AssistantInteraction(
        user_id=session.user_id,
        prompt=question,
        response=result["answer"],
        grounded_refs={
            "conversation_id": conversation_id,
            "citations": result["citations"],
            "suggested_ticket": result["suggested_ticket"],
        },
    )
    db.add(interaction)
    await db.flush()
    await write_audit(
        db,
        actor_id=session.user_id,
        event_type=ASSISTANT_QUERY,
        resource_type="assistant_interaction",
        resource_id=interaction.interaction_id,
        payload={"conversation_id": conversation_id},
        flush_only=True,  # low-value event: commit with the business transaction
    )
    await db.commit()

    return {
        "conversation_id": conversation_id,
        "answer": result["answer"],
        "citations": result["citations"],
        "suggested_ticket": result["suggested_ticket"],
    }


# ---------------------------------------------------------------------------
# Streaming variant (SSE)
# ---------------------------------------------------------------------------

# Mock-mode pacing: the rules answer goes out in ~40-char chunks with a short
# delay so the UI animates like the live token stream (consistent UX).
STREAM_CHUNK_SIZE = 40
STREAM_CHUNK_DELAY_SECONDS = 0.015


def _sse(event: str, data: dict) -> str:
    """One SSE frame: a named event with a single-line JSON payload."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def _rule_chunks(text: str) -> AsyncIterator[str]:
    """Mock-mode delivery: the rules answer in paced fixed-size chunks."""
    for i in range(0, len(text), STREAM_CHUNK_SIZE):
        yield text[i : i + STREAM_CHUNK_SIZE]
        await asyncio.sleep(STREAM_CHUNK_DELAY_SECONDS)


async def _persist_streamed_interaction(
    sessionmaker,
    user_id: str,
    question: str,
    conversation_id: str,
    answer: str,
    result: dict,
) -> None:
    """AssistantInteraction row + ASSISTANT_QUERY audit for a streamed reply —
    the same trail the one-shot route writes, with the full assembled text.
    Runs on a fresh session: the request-scoped one may already be closed by
    the time the stream finishes."""
    async with sessionmaker() as db:
        interaction = AssistantInteraction(
            user_id=user_id,
            prompt=question,
            response=answer,
            grounded_refs={
                "conversation_id": conversation_id,
                "citations": result["citations"],
                "suggested_ticket": result["suggested_ticket"],
            },
        )
        db.add(interaction)
        await db.flush()
        await write_audit(
            db,
            actor_id=user_id,
            event_type=ASSISTANT_QUERY,
            resource_type="assistant_interaction",
            resource_id=interaction.interaction_id,
            payload={"conversation_id": conversation_id},
            flush_only=True,  # same as the one-shot route: one commit
        )
        await db.commit()


@router.post("/assistant/query/stream")
async def query_assistant_stream(
    body: AssistantQueryRequest,
    request: Request,
    session: SessionData = Depends(require_permission("ASSISTANT_USE")),
    db: AsyncSession = Depends(get_db),
):
    """Server-sent-events variant of POST /assistant/query.

    Same grounding pipeline as the one-shot route (`engine.ground` runs up
    front, so all DB reads finish before the first byte); only the reply
    delivery differs. Event contract:
      meta  {"conversation_id", "intent"}                    — always first
      delta {"text"}                                         — reply fragments:
        live LLM token deltas, or paced ~40-char chunks of the rules answer
      error {"code": "LLM_STREAM_FAILED"}                    — only when the
        LLM dies MID-reply; the rules answer follows as deltas
      final {"answer", "citations", "suggested_ticket"}      — always last;
        `answer` is the authoritative full text (and what gets persisted)
    """
    question = body.question.strip()
    if not question:
        raise ValidationError("question must not be empty")
    conversation_id = body.conversation_id or uuid.uuid4().hex

    intent, result = await engine.ground(db, session, question, conversation_id)
    sessionmaker = request.app.state.sessionmaker

    async def event_stream() -> AsyncIterator[str]:
        rule_answer: str = result["answer"]
        assembled = ""
        try:
            yield _sse(
                "meta", {"conversation_id": conversation_id, "intent": intent}
            )
            if _RUNTIME.llm_client is not None and _prose_messages(
                intent, result, question
            ):
                try:
                    async for delta in _llm_prose_stream(intent, result, question):
                        assembled += delta
                        yield _sse("delta", {"text": delta})
                except Exception as exc:  # noqa: BLE001 — honest degrade
                    logger.warning(
                        "LLM stream failed (%s); falling back to rules answer", exc
                    )
                    if assembled:
                        # Died mid-reply: the partial prose is incomplete, so
                        # flag it and deliver the grounded rules answer instead
                        # of leaving the user hanging on a cut-off sentence.
                        yield _sse("error", {"code": "LLM_STREAM_FAILED"})
                    assembled = ""
                if not assembled:
                    # Setup failure / empty completion: same silent per-call
                    # rules fallback the one-shot route applies.
                    async for chunk in _rule_chunks(rule_answer):
                        assembled += chunk
                        yield _sse("delta", {"text": chunk})
                elif (
                    intent == "review"
                    and DISCLAIMER_TEXT not in assembled
                ):
                    suffix = f" {DISCLAIMER_TEXT}"
                    assembled += suffix
                    yield _sse("delta", {"text": suffix})
            else:
                async for chunk in _rule_chunks(rule_answer):
                    assembled += chunk
                    yield _sse("delta", {"text": chunk})
            yield _sse(
                "final",
                {
                    "answer": assembled,
                    "citations": result["citations"],
                    "suggested_ticket": result["suggested_ticket"],
                },
            )
        except (asyncio.CancelledError, GeneratorExit):
            logger.info("assistant stream closed before completion (client away?)")
            raise
        finally:
            # Best-effort: when the client disconnected mid-stream the cancel
            # scope may block this write — log and move on, never crash.
            try:
                await _persist_streamed_interaction(
                    sessionmaker,
                    session.user_id,
                    question,
                    conversation_id,
                    assembled or rule_answer,
                    result,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort persist
                logger.warning("streamed assistant interaction persist failed: %s", exc)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/assistant/news-summary")
async def news_summary(
    symbol: str,
    session: SessionData = Depends(require_permission("ASSISTANT_USE")),
    db: AsyncSession = Depends(get_db),
):
    """GenAI news summary for one instrument (Trading workspace panel, D-27.3).

    Advisory-only; every figure is grounded in NewsItem/NewsSentiment. The
    structured grounding is always computed by the rules; when the chat model
    passed the startup self-check, it rewords the prose and the response is
    marked `mock: false, model: <chat model>` — otherwise or on any LLM error
    it stays `mock: true, model: "rules-v1"`.
    """
    instrument = (
        await db.execute(select(Instrument).where(Instrument.symbol == symbol))
    ).scalar_one_or_none()
    if instrument is None:
        raise NotFound(f"unknown instrument symbol: {symbol}")
    return await _reword_news_summary(await get_news_summary(db, instrument))
