"""GenAI assistant (DESIGN 07, FR-AI): advisory-only, grounded in caller data.

MVP is a rule-based responder (AssistantEngine) computing answers directly from
the DB through the same read-only tool whitelist the LLM will use later
(get_positions / get_valuation / get_transactions / get_prices), so answers are
grounded and every figure is cited from a tool result (FR-AI-001).

LLM SEAM: AssistantEngine(llm=...) accepts an async callable
`llm(intent, tool_results, question) -> str | None` that may draft the prose;
intent detection, tool calls, citations and the ticket guardrail stay
server-side and deterministic regardless.

GUARDRAIL (FR-AI-003): the assistant NEVER places orders. Buy/sell intents
produce advice text plus a `suggested_ticket` for the UI to render into the
standard order ticket; confirmation goes through POST /orders by the user.
The assistant identity holds no trading permissions, so any direct API misuse
is denied by default and logged as a security event (DESIGN 09/17).
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.db import get_db
from app.core.errors import ValidationError
from app.core.models import (
    AssistantInteraction,
    Execution,
    Instrument,
    Order,
    Portfolio,
    Position,
    PriceTick,
)
from app.core.security import SessionData, require_permission
from app.core.timeutil import as_utc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])

ASSISTANT_QUERY = "ASSISTANT_QUERY"

RECENT_TRANSACTIONS_LIMIT = 10


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
    for position in positions:
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


# ---------------------------------------------------------------------------
# AssistantEngine — rule-based intents with an LLM seam
# ---------------------------------------------------------------------------

_TRADE_WORDS = re.compile(r"\b(buy|sell)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

DECLINE_TEXT = (
    "I'm the STP platform assistant — I can answer questions about your "
    "positions, portfolio valuation, recent transactions and latest prices, "
    "and I can prepare (but never place) trade tickets. Your question looks "
    "outside that scope; could you rephrase it?"
)


class AssistantEngine:
    """Advisory-only responder. See module docstring for the LLM seam."""

    def __init__(
        self,
        llm: Callable[[str, dict, str], Awaitable[str | None]] | None = None,
    ):
        self._llm = llm

    async def _resolve_instrument(
        self, db: AsyncSession, question: str
    ) -> Instrument | None:
        instruments = (await db.execute(select(Instrument))).scalars().all()
        lowered = question.lower()
        for instrument in instruments:  # exact symbol mention wins ("7203.T")
            if instrument.symbol.lower() in lowered:
                return instrument
        for instrument in instruments:  # then name mention ("Sony")
            if instrument.name.lower() in lowered:
                return instrument
        return None

    async def answer(
        self, db: AsyncSession, session: SessionData, question: str
    ) -> dict:
        lowered = question.lower()
        instrument = await self._resolve_instrument(db, question)

        if _TRADE_WORDS.search(lowered):
            intent = "trade"
            result = await self._handle_trade(db, session, question, instrument)
        elif "position" in lowered or "holding" in lowered:
            intent = "positions"
            result = await self._handle_positions(db, session)
        elif any(
            word in lowered
            for word in ("valuation", "value", "p&l", "pnl", "worth")
        ):
            intent = "valuation"
            result = await self._handle_valuation(db, session)
        elif any(
            word in lowered for word in ("transaction", "trade", "execution")
        ):
            intent = "transactions"
            result = await self._handle_transactions(db, session)
        elif instrument is not None and any(
            word in lowered for word in ("price", "quote", "last", "how much")
        ):
            intent = "price"
            result = await self._handle_price(db, instrument)
        else:
            intent = "out_of_scope"
            result = {"answer": DECLINE_TEXT, "citations": [], "suggested_ticket": None}

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
    ) -> dict:
        lowered = question.lower()
        side = "BUY" if "buy" in lowered else "SELL"
        # Quantity: first number that is not part of the symbol/name mention.
        scrubbed = lowered
        if instrument is not None:
            scrubbed = scrubbed.replace(instrument.symbol.lower(), " ").replace(
                instrument.name.lower(), " "
            )
        match = _NUMBER.search(scrubbed)
        quantity = float(match.group()) if match else None

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
                price_note = f" Latest price: ¥{float(price):,.2f}."

        ticket = None
        if instrument is not None:
            ticket = {
                "portfolio_id": portfolio.portfolio_id if portfolio else None,
                "instrument": instrument.symbol,
                "side": side,
                "quantity": quantity,
            }
            qty_text = f"{quantity:g} " if quantity is not None else ""
            answer = (
                f"I can't place trades — I'm advisory only, and orders are yours "
                f"to confirm. Based on the latest data I've prepared a suggested "
                f"ticket: {side} {qty_text}{instrument.symbol} "
                f"({instrument.name}).{price_note} Review it in the order ticket "
                f"and confirm to submit."
            )
        else:
            answer = (
                f"I can't place trades — I'm advisory only. Tell me which "
                f"instrument you want to {side.lower()} (symbol or name) and I'll "
                f"prepare a suggested ticket for you to confirm in the order ticket."
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
                    f" worth ¥{float(p['market_value']):,.2f}"
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
                    "currency": "JPY",
                },
            }
            for v in valuations
        ]
        if not valuations:
            answer = "You don't have any portfolios yet."
        else:
            total = sum(v["total_value"] for v in valuations)
            parts = [
                f"'{v['portfolio']}' ¥{float(v['total_value']):,.2f} "
                f"(cash ¥{float(v['cash']):,.2f}, positions ¥{float(v['positions_value']):,.2f})"
                for v in valuations
            ]
            answer = (
                f"Your portfolios are worth a total of ¥{float(total):,.2f}: "
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
                f"¥{float(t['price']):,.2f} ({as_utc(t['executed_at']).date().isoformat()})"
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
            f"¥{float(price):,.2f} as of {as_utc(ts).isoformat()}."
        )
        return {"answer": answer, "citations": [citation], "suggested_ticket": None}


engine = AssistantEngine()


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

    result = await engine.answer(db, session, question)

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
