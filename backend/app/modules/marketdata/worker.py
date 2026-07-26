"""Tick replayer worker: history bootstrap + live random-walk tick loop.

Contract: publishes `market.ticks` events directly to the bus (fan-out to the
execution engine, valuation projector, etc.) and throttles PriceTick row
persistence to at most one row per instrument per 5 seconds.

DB units-of-work run through `_shielded` (see orders.workers for the
rationale: mid-call task cancellation can wedge aiosqlite connections and
hang app shutdown).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from decimal import Decimal

from sqlalchemy import func, select

from app.core.models import Instrument, PriceTick
from app.core.timeutil import utcnow
from app.modules.marketdata.history import generate_daily_history, next_live_price
from app.modules.marketdata.registry import (
    get_snapshot,
    set_tick,
    warm_from_db,
)

logger = logging.getLogger(__name__)

PERSIST_THROTTLE_SECONDS = 5.0


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


async def _bootstrap(sessionmaker):
    """Generate daily history on an empty PriceTick table, then warm the
    latest-price registry from the most recent closes."""
    async with sessionmaker() as session:
        instruments = (
            (
                await session.execute(
                    select(Instrument).where(Instrument.tradable.is_(True))
                )
            )
            .scalars()
            .all()
        )
        tick_count = await session.scalar(select(func.count(PriceTick.instrument_id)))
        if not tick_count:
            session.add_all(generate_daily_history(instruments))
            await session.commit()
            logger.info(
                "tick replayer: generated history for %d instruments",
                len(instruments),
            )
        await warm_from_db(
            session,
            [i.instrument_id for i in instruments],
            {i.instrument_id: i.symbol for i in instruments},
        )
        return list(instruments)


async def _persist(sessionmaker, rows: list[PriceTick]) -> None:
    try:
        async with sessionmaker() as session:
            session.add_all(rows)
            await session.commit()
    except Exception:
        logger.exception("tick replayer: persist failed (skipped)")


def build_tick_replayer(settings):
    """Bind settings; returns the `tick_replayer(bus, sessionmaker)` worker."""

    async def tick_replayer(bus, sessionmaker):
        interval = max(settings.TICK_INTERVAL_MS, 10) / 1000.0
        rng = random.Random()

        instruments = await _shielded(_bootstrap(sessionmaker))

        last_persist: dict[str, float] = {}
        while True:
            try:
                persist_rows: list[PriceTick] = []
                now_mono = time.monotonic()
                for instrument in instruments:
                    snapshot = get_snapshot(instrument.instrument_id)
                    base = snapshot.price if snapshot else instrument.tick_size * 2000
                    price = next_live_price(base, instrument.tick_size, rng)
                    volume = Decimal(rng.randint(100, 50_000))
                    ts = utcnow()
                    snapshot = set_tick(
                        instrument.instrument_id,
                        instrument.symbol,
                        price,
                        ts,
                        volume,
                    )
                    await bus.publish(
                        "market.ticks",
                        {
                            "instrument_id": instrument.instrument_id,
                            "symbol": instrument.symbol,
                            "ts": ts.isoformat(),
                            "price": float(price),
                            "open": float(snapshot.day_open),
                            "high": float(snapshot.day_high),
                            "low": float(snapshot.day_low),
                            "close": float(price),
                            "volume": float(snapshot.day_volume),
                        },
                    )
                    last = last_persist.get(instrument.instrument_id, -1e9)
                    if now_mono - last >= PERSIST_THROTTLE_SECONDS:
                        last_persist[instrument.instrument_id] = now_mono
                        persist_rows.append(
                            PriceTick(
                                instrument_id=instrument.instrument_id,
                                ts=ts,
                                open=snapshot.day_open,
                                high=snapshot.day_high,
                                low=snapshot.day_low,
                                close=price,
                                volume=snapshot.day_volume,
                            )
                        )
                if persist_rows:
                    await _shielded(_persist(sessionmaker, persist_rows))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("tick replayer: loop error")
            await asyncio.sleep(interval)

    return tick_replayer
