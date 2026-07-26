"""Paper trading accounts (DESIGN 06, FR-PTR): a paper account IS a portfolio.

A paper account is a Portfolio with type=PAPER owned by the caller; orders and
executions flow through the shared pipeline (orders module, built in parallel)
with no separate engine. This module only manages the account lifecycle:
create (idempotent), inspect (statistics + equity curve), reset.

INITIAL BALANCE LIMITATION: Portfolio has no initial-balance column, so the
initial cash is kept in a process-local dict {_initial_balances} — lost on
restart and not shared across replicas. Fallbacks: accounts unknown to the
dict report initial_balance = current cash_balance; reset restores the
in-memory initial when known, else DEFAULT_INITIAL_CASH. Persist to a column
when migrations land.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from decimal import Decimal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.db import get_db
from app.core.errors import NotFound, ValidationError
from app.core.models import (
    Execution,
    Order,
    OrderSide,
    Portfolio,
    PortfolioType,
    Position,
    ValuationSnapshot,
)
from app.core.security import SessionData, require_permission
from app.core.timeutil import as_utc, utcnow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["paper"])

PAPER_ACCOUNT_CREATED = "PAPER_ACCOUNT_CREATED"
PAPER_ACCOUNT_RESET = "PAPER_ACCOUNT_RESET"

DEFAULT_INITIAL_CASH = Decimal("10000000")  # 10M JPY

# Process-local initial balances: {portfolio_id: initial_cash}. See docstring.
_initial_balances: dict[str, Decimal] = {}

MIN_TRADES_FOR_STATISTICS = 2


class PaperAccountRequest(BaseModel):
    name: str | None = None
    initial_cash: Decimal | None = None


def _initial_for(portfolio: Portfolio) -> Decimal:
    """Known in-memory initial, else fall back to current cash (documented)."""
    return _initial_balances.get(portfolio.portfolio_id, portfolio.cash_balance)


def _account_json(portfolio: Portfolio) -> dict:
    initial = _initial_for(portfolio)
    return {
        "portfolio_id": portfolio.portfolio_id,
        "name": portfolio.name,
        "cash_balance": float(portfolio.cash_balance),
        "initial_balance": float(initial),
    }


@router.post("/paper/accounts", status_code=201)
async def create_account(
    body: PaperAccountRequest,
    response: Response,
    session: SessionData = Depends(require_permission("PAPER_TRADE")),
    db: AsyncSession = Depends(get_db),
):
    """Create my paper account, or return the existing one (idempotent)."""
    existing = (
        (
            await db.execute(
                select(Portfolio).where(
                    Portfolio.owner_id == session.user_id,
                    Portfolio.type == PortfolioType.PAPER.value,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        response.status_code = 200
        return _account_json(existing)

    initial = body.initial_cash if body.initial_cash is not None else DEFAULT_INITIAL_CASH
    if initial <= 0:
        raise ValidationError("initial_cash must be positive")
    name = f"Paper — {body.name or session.display_name}"
    portfolio = Portfolio(
        name=name,
        type=PortfolioType.PAPER.value,
        owner_id=session.user_id,
        cash_balance=initial,
    )
    db.add(portfolio)
    await db.flush()
    _initial_balances[portfolio.portfolio_id] = initial
    await write_audit(
        db,
        actor_id=session.user_id,
        event_type=PAPER_ACCOUNT_CREATED,
        resource_type="portfolio",
        resource_id=portfolio.portfolio_id,
        payload={"name": name, "initial_cash": str(initial)},
        flush_only=True,
    )
    await db.commit()
    return _account_json(portfolio)


async def _my_paper_portfolio(
    db: AsyncSession, portfolio_id: str, user_id: str
) -> Portfolio:
    portfolio = await db.get(Portfolio, portfolio_id)
    if (
        portfolio is None
        or portfolio.owner_id != user_id
        or portfolio.type != PortfolioType.PAPER.value
    ):
        raise NotFound("paper account not found")
    return portfolio


def _round_trips(executions: list[tuple[Execution, Order]]) -> list[Decimal]:
    """Pair BUY lots with SELLs per instrument (FIFO); one pnl per matched sell."""
    by_instrument: dict[str, list[tuple[Execution, Order]]] = defaultdict(list)
    for execution, order in executions:
        by_instrument[order.instrument_id].append((execution, order))

    pnls: list[Decimal] = []
    for instrument_id in by_instrument:
        lots: deque[list] = deque()  # [remaining_qty, buy_price]
        for execution, order in sorted(
            by_instrument[instrument_id], key=lambda eo: as_utc(eo[0].executed_at)
        ):
            if order.side == OrderSide.BUY.value:
                lots.append([execution.quantity, execution.price])
                continue
            # SELL: consume open buy lots FIFO; unmatched (short) qty is ignored.
            remaining = execution.quantity
            pnl = Decimal("0")
            matched = False
            while remaining > 0 and lots:
                lot_qty, lot_price = lots[0]
                take = min(remaining, lot_qty)
                pnl += (execution.price - lot_price) * take
                lot_qty -= take
                remaining -= take
                matched = True
                if lot_qty == 0:
                    lots.popleft()
                else:
                    lots[0][0] = lot_qty
            if matched:
                pnls.append(pnl)
    return pnls


def _max_drawdown(values: list[float]) -> float:
    """Largest peak-to-trough decline (absolute, JPY) over the equity curve."""
    peak = None
    max_dd = 0.0
    for value in values:
        if peak is None or value > peak:
            peak = value
        drawdown = peak - value
        if drawdown > max_dd:
            max_dd = drawdown
    return max_dd


@router.get("/paper/accounts/{portfolio_id}")
async def get_account(
    portfolio_id: str,
    session: SessionData = Depends(require_permission("PAPER_TRADE")),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _my_paper_portfolio(db, portfolio_id, session.user_id)

    snapshots = (
        (
            await db.execute(
                select(ValuationSnapshot)
                .where(ValuationSnapshot.portfolio_id == portfolio.portfolio_id)
                .order_by(ValuationSnapshot.ts)
            )
        )
        .scalars()
        .all()
    )
    if snapshots:
        equity_curve = [
            {
                "ts": as_utc(snap.ts).isoformat(),
                "value": float(snap.market_value + snap.cash),
            }
            for snap in snapshots
        ]
    else:
        # No valuation history: the curve is just current cash.
        equity_curve = [
            {"ts": utcnow().isoformat(), "value": float(portfolio.cash_balance)}
        ]

    executions = (
        (
            await db.execute(
                select(Execution, Order)
                .join(Order, Execution.order_id == Order.order_id)
                .where(Order.portfolio_id == portfolio.portfolio_id)
                .order_by(Execution.executed_at)
            )
        )
        .all()
    )
    pnls = _round_trips([(row.Execution, row.Order) for row in executions])

    statistics = None
    if len(pnls) >= MIN_TRADES_FOR_STATISTICS:
        wins = sum(1 for pnl in pnls if pnl > 0)
        statistics = {
            "trades": len(pnls),
            "win_rate": wins / len(pnls),
            "avg_pnl_per_trade": float(sum(pnls) / len(pnls)),
            "max_drawdown": _max_drawdown([point["value"] for point in equity_curve]),
        }

    return {
        **_account_json(portfolio),
        "statistics": statistics,
        "equity_curve": equity_curve,
    }


@router.post("/paper/accounts/{portfolio_id}/reset")
async def reset_account(
    portfolio_id: str,
    session: SessionData = Depends(require_permission("PAPER_TRADE")),
    db: AsyncSession = Depends(get_db),
):
    """Restore cash to the initial balance and clear positions.

    Order/execution history is kept (it stays marked PAPER via the portfolio
    join, per AC-008); only open positions and cash are reset.
    """
    portfolio = await _my_paper_portfolio(db, portfolio_id, session.user_id)
    initial = _initial_balances.get(portfolio_id, DEFAULT_INITIAL_CASH)
    portfolio.cash_balance = initial
    await db.execute(
        delete(Position).where(Position.portfolio_id == portfolio.portfolio_id)
    )
    await write_audit(
        db,
        actor_id=session.user_id,
        event_type=PAPER_ACCOUNT_RESET,
        resource_type="portfolio",
        resource_id=portfolio.portfolio_id,
        payload={"restored_cash": str(initial)},
        flush_only=True,
    )
    await db.commit()
    return {
        "portfolio_id": portfolio.portfolio_id,
        "cash_balance": float(portfolio.cash_balance),
    }
