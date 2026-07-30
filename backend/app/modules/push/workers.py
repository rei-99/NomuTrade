"""Fan-out workers (design 22): one worker per source stream.

- `tick_fanout`: market.ticks -> broadcast {"type":"tick"} to every connection.
- `notify_fanout`: notify -> {"type":"notification"} to data.user_id only.
- `execution_fanout`: trading.executions -> {"type":"execution"} to the
  portfolio owner (the event carries no user_id; resolved via a shielded
  Portfolio lookup — mid-call cancellation can wedge aiosqlite connections).

Workers never die on a bad event: per-event failures are logged and the
stream keeps flowing (same idiom as the notifications worker).
"""

from __future__ import annotations

import asyncio
import logging

from app.core.models import Portfolio
from app.modules.push.manager import manager

logger = logging.getLogger(__name__)


async def _shielded(coro):
    """Run one DB unit-of-work shielded from task cancellation."""
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


async def tick_fanout(bus, sessionmaker) -> None:
    subscription = await bus.subscribe("market.ticks")
    async for event in subscription:
        try:
            await manager.broadcast({"type": "tick", "data": event})
        except Exception:  # keep the worker alive on a bad event
            logger.exception("push: tick broadcast failed")


async def notify_fanout(bus, sessionmaker) -> None:
    subscription = await bus.subscribe("notify")
    async for event in subscription:
        try:
            user_id = event.get("user_id")
            if user_id:
                await manager.send_to_user(
                    user_id, {"type": "notification", "data": event}
                )
        except Exception:
            logger.exception("push: notification fan-out failed")


async def execution_fanout(bus, sessionmaker) -> None:
    subscription = await bus.subscribe("trading.executions")
    async for event in subscription:
        try:
            owner_id = await _shielded(
                _resolve_owner(sessionmaker, event.get("portfolio_id"))
            )
            if owner_id:
                await manager.send_to_user(
                    owner_id, {"type": "execution", "data": event}
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("push: execution fan-out failed")


async def _resolve_owner(sessionmaker, portfolio_id: str | None) -> str | None:
    if not portfolio_id:
        return None
    async with sessionmaker() as session:
        portfolio = await session.get(Portfolio, portfolio_id)
        return portfolio.owner_id if portfolio is not None else None
