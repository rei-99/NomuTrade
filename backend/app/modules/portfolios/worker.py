"""Valuation projector worker (docs/design/03, FR-PFM-002).

Writes a ValuationSnapshot for every portfolio that has positions, triggered
by each `trading.executions` event and by a 30 s wall-clock timer. Snapshots
feed the performance series and the volatility KPI.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.models import Portfolio, Position, ValuationSnapshot
from app.core.timeutil import utcnow
from app.modules.portfolios.valuation import compute_realized, value_positions

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_SECONDS = 30.0


async def _shielded(coro):
    """Run one DB unit-of-work shielded from task cancellation (cancelling a
    task mid-aiosqlite-call can wedge the connection and hang app shutdown)."""
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


async def valuation_projector(bus, sessionmaker):
    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def pump() -> None:
        subscription = await bus.subscribe("trading.executions")
        async for event in subscription:
            queue.put_nowait(event)

    task = asyncio.create_task(pump(), name="valuation-pump")
    try:
        while True:
            try:
                await asyncio.wait_for(queue.get(), timeout=SNAPSHOT_INTERVAL_SECONDS)
            except TimeoutError:
                pass  # wall-clock tick
            try:
                await _shielded(snapshot_all(sessionmaker))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("valuation projector: snapshot failed")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def snapshot_all(sessionmaker) -> None:
    """One ValuationSnapshot per portfolio that currently holds positions."""
    async with sessionmaker() as session:
        portfolio_ids = (
            (
                await session.execute(
                    select(Position.portfolio_id)
                    .where(Position.quantity != 0)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if not portfolio_ids:
            return
        ts = utcnow()
        for portfolio_id in portfolio_ids:
            portfolio = await session.get(Portfolio, portfolio_id)
            if portfolio is None:
                continue
            valuations = await value_positions(session, portfolio_id)
            market_value = sum(
                (v.market_value for v in valuations if v.market_value is not None),
                Decimal("0"),
            )
            unrealized = sum(
                (v.unrealized_pnl for v in valuations if v.unrealized_pnl is not None),
                Decimal("0"),
            )
            realized = await compute_realized(session, portfolio_id)
            session.add(
                ValuationSnapshot(
                    portfolio_id=portfolio_id,
                    ts=ts,
                    market_value=market_value,
                    cash=portfolio.cash_balance,
                    realized_pnl=realized,
                    unrealized_pnl=unrealized,
                )
            )
        try:
            await session.commit()
        except IntegrityError:
            # Duplicate (portfolio_id, ts) on a rapid double trigger: harmless.
            await session.rollback()
