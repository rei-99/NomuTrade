"""Shared valuation math for the portfolio endpoints and the projector worker.

Latest prices come from the marketdata module's in-process registry (with a
DB fallback for the RUN_WORKERS=false case). Cross-module import is safe: the
platform is a single-process modular monolith and registry.py is side-effect
free. Cash math is bond-aware via `trade_value` (bonds quote % of par,
quantity = face value, design 21 §A2).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import (
    Execution,
    Instrument,
    Order,
    Portfolio,
    Position,
    PriceTick,
    ValuationSnapshot,
)
from app.core.timeutil import as_utc, utcnow
from app.modules.marketdata.registry import (
    PriceSnapshot,
    get_sim_now,
    get_snapshot,
    warm_from_db,
)
from app.modules.orders.validation import trade_value

STALE_PRICE_SECONDS = 60.0


@dataclass
class PositionValuation:
    position: Position
    instrument: Instrument
    latest_price: Decimal | None
    stale: bool
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    # Per-position day change (design 21 §A5): from the registry snapshot's
    # running day open; None when no snapshot exists.
    day_open: Decimal | None = None
    day_change: Decimal | None = None
    day_change_pct: Decimal | None = None


async def value_positions(
    db: AsyncSession, portfolio_id: str
) -> list[PositionValuation]:
    """Current non-zero positions of a portfolio marked at latest prices."""
    rows = await session_positions(db, portfolio_id)
    instrument_ids = [position.instrument_id for position, _i in rows]
    await warm_from_db(db, instrument_ids)
    # Staleness is measured against the simulation clock when one is running
    # (D-10): replayed dataset ticks carry dataset timestamps, so comparing
    # them to wall-clock utcnow() would mark every price permanently stale.
    now = get_sim_now() or utcnow()
    valuations: list[PositionValuation] = []
    for position, instrument in rows:
        snapshot: PriceSnapshot | None = get_snapshot(position.instrument_id)
        if snapshot is None:
            valuations.append(
                PositionValuation(position, instrument, None, True, None, None)
            )
            continue
        stale = (now - as_utc(snapshot.ts)).total_seconds() > STALE_PRICE_SECONDS
        market_value = trade_value(instrument, position.quantity, snapshot.price)
        unrealized = market_value - trade_value(
            instrument, position.quantity, position.avg_cost
        )
        day_open = snapshot.day_open
        day_change = trade_value(
            instrument, position.quantity, snapshot.price - day_open
        )
        base = trade_value(instrument, position.quantity, day_open)
        day_change_pct = (day_change / base * 100) if base else None
        valuations.append(
            PositionValuation(
                position,
                instrument,
                snapshot.price,
                stale,
                market_value,
                unrealized,
                day_open,
                day_change,
                day_change_pct,
            )
        )
    return valuations


async def session_positions(db: AsyncSession, portfolio_id: str):
    result = await db.execute(
        select(Position, Instrument)
        .join(Instrument, Position.instrument_id == Instrument.instrument_id)
        .where(
            Position.portfolio_id == portfolio_id,
            Position.quantity != 0,
        )
        .order_by(Instrument.symbol)
    )
    return result.all()


async def compute_realized(db: AsyncSession, portfolio_id: str) -> Decimal:
    """Approximate realized P&L over all SELL executions.

    Simplification agreed for the MVP (no realized-P&L column exists and core
    models may not change): each sale is valued against the *current* avg_cost
    of the position rather than the avg cost at the time of the sale, i.e.
    realized_pnl ~= SUM over SELL executions of (exec_price - current
    avg_cost) * qty. Exact for portfolios that never re-buy after selling.
    """
    rows = (
        await db.execute(
            select(Execution, Order, Instrument)
            .join(Order, Execution.order_id == Order.order_id)
            .join(Instrument, Order.instrument_id == Instrument.instrument_id)
            .where(Order.portfolio_id == portfolio_id, Order.side == "SELL")
        )
    ).all()
    instrument_ids = {order.instrument_id for _e, order, _i in rows}
    avg_costs: dict[str, Decimal] = {}
    for instrument_id in instrument_ids:
        position = await db.get(Position, (portfolio_id, instrument_id))
        avg_costs[instrument_id] = (
            position.avg_cost if position is not None else Decimal("0")
        )
    realized = Decimal("0")
    for execution, order, instrument in rows:
        # Bond-aware (§A2): face × (price - avg_cost) / 100 for bonds.
        realized += trade_value(
            instrument, execution.quantity, execution.price - avg_costs[order.instrument_id]
        )
    return realized


async def previous_close_map(
    db: AsyncSession, instrument_ids: list[str], today: datetime
) -> dict[str, Decimal]:
    """Latest daily close before today, per instrument (for day change)."""
    result: dict[str, Decimal] = {}
    for instrument_id in instrument_ids:
        row = (
            await db.execute(
                select(PriceTick)
                .where(
                    PriceTick.instrument_id == instrument_id,
                    PriceTick.ts < today,
                )
                .order_by(PriceTick.ts.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            result[instrument_id] = row.close
    return result


async def annualized_volatility_pct(
    db: AsyncSession, portfolio_id: str
) -> float | None:
    """Annualized volatility of daily total_value from ValuationSnapshots.

    Per the contract: stdev of the daily total_value series, annualized with
    sqrt(252), expressed as a percentage of the mean total_value. None when
    fewer than 10 daily points exist (FR-PFM-003 minimum history).
    """
    rows = (
        (
            await db.execute(
                select(ValuationSnapshot)
                .where(ValuationSnapshot.portfolio_id == portfolio_id)
                .order_by(ValuationSnapshot.ts)
            )
        )
        .scalars()
        .all()
    )
    daily: dict[object, Decimal] = {}
    for row in rows:
        daily[as_utc(row.ts).date()] = row.market_value + row.cash
    values = [float(v) for v in daily.values()]
    if len(values) < 10:
        return None
    mean = statistics.fmean(values)
    if mean == 0:
        return None
    return statistics.stdev(values) * (252**0.5) / mean * 100


async def compute_total_value(db: AsyncSession, portfolio: Portfolio) -> Decimal:
    """cash + market value of positions at latest prices."""
    valuations = await value_positions(db, portfolio.portfolio_id)
    market = sum(
        (v.market_value for v in valuations if v.market_value is not None),
        Decimal("0"),
    )
    return portfolio.cash_balance + market
