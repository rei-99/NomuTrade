"""Tick replayer worker: dataset replay (primary) or random-walk fallback.

Dataset mode (D-10/D-11): after `load_dataset` has populated PriceTick, the
replayer walks the stored 1-minute bars in dataset-time order at
`REPLAY_BARS_PER_SECOND` unique timestamps per second (~78 s per market day
at the default 5/s), publishing each bar as a `market.ticks` event with the
bar's dataset timestamp. At the end of the dataset it loops to the start
(`REPLAY_MODE=loop`) or goes idle (`hold`).

Fallback mode (no `data/` directory): deterministic daily history is
generated on an empty PriceTick table and live prices are random-walked at
`TICK_INTERVAL_MS` (the pre-dataset behavior), persisting throttled rows.

DB units-of-work run through `_shielded` (mid-call task cancellation can
wedge aiosqlite connections and hang app shutdown).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.core.models import Instrument, PriceTick
from app.core.timeutil import as_utc, utcnow
from app.modules.marketdata.history import generate_daily_history, next_live_price
from app.modules.marketdata.loader import (
    LIVE_START,
    ensure_dataset_instruments,
    load_dataset,
    resolve_data_dir,
)
from app.modules.marketdata.registry import (
    get_snapshot,
    set_sim_now,
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


async def _bootstrap(sessionmaker, settings) -> tuple[list[Instrument], str]:
    """Load the dataset (or generate fallback history), warm the registry.

    Returns (instruments, mode) where mode is "dataset" or "fallback".
    """
    data_dir = resolve_data_dir(settings.DATA_DIR)
    async with sessionmaker() as session:
        if data_dir is not None:
            stats = await load_dataset(session, data_dir)
            logger.info("tick replayer: dataset loaded %s", stats)
            mode = "dataset"
        else:
            instruments = await ensure_dataset_instruments(session)
            tick_count = await session.scalar(
                select(func.count(PriceTick.instrument_id))
            )
            if not tick_count:
                session.add_all(generate_daily_history(instruments))
            await session.commit()
            logger.warning(
                "tick replayer: data dir %r not found, using generated feed",
                settings.DATA_DIR,
            )
            mode = "fallback"
        instruments = (
            (
                await session.execute(
                    select(Instrument).where(Instrument.tradable.is_(True))
                )
            )
            .scalars()
            .all()
        )
        await warm_from_db(
            session,
            [i.instrument_id for i in instruments],
            {i.instrument_id: i.symbol for i in instruments},
        )
        return list(instruments), mode


def _tick_payload(instrument: Instrument, snapshot, ts: datetime) -> dict:
    return {
        "instrument_id": instrument.instrument_id,
        "symbol": instrument.symbol,
        "ts": ts.isoformat(),
        "price": float(snapshot.price),
        "open": float(snapshot.day_open),
        "high": float(snapshot.day_high),
        "low": float(snapshot.day_low),
        "close": float(snapshot.price),
        "volume": float(snapshot.day_volume),
    }


async def _load_bars(sessionmaker) -> list[tuple]:
    """All minute bars of the live window, ordered by (ts, instrument).

    Loaded as plain tuples (~120k rows) — the whole replay fits comfortably
    in memory and avoids per-minute queries against SQLite.
    """
    live_from = datetime(*LIVE_START.timetuple()[:3], tzinfo=timezone.utc)
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(
                    PriceTick.instrument_id,
                    PriceTick.ts,
                    PriceTick.open,
                    PriceTick.high,
                    PriceTick.low,
                    PriceTick.close,
                    PriceTick.volume,
                )
                .where(PriceTick.ts >= live_from)
                .order_by(PriceTick.ts, PriceTick.instrument_id)
            )
        ).all()
    return [tuple(r) for r in rows]


async def _replay_dataset(bus, sessionmaker, settings, instruments) -> None:
    by_id = {i.instrument_id: i for i in instruments}
    pause = 1.0 / max(settings.REPLAY_BARS_PER_SECOND, 0.1)
    bars = await _shielded(_load_bars(sessionmaker))
    if not bars:
        logger.error("tick replayer: dataset has no live bars; going idle")
        while True:
            await asyncio.sleep(3600)
    logger.info("tick replayer: replaying %d minute bars (mode=%s, %.1f ts/s)",
                len(bars), settings.REPLAY_MODE, settings.REPLAY_BARS_PER_SECOND)
    while True:
        # Re-base the sim clock at the replay start (the bootstrap warm had
        # seeded it from the dataset's end) so consumers never see the future.
        set_sim_now(as_utc(bars[0][1]))
        i = 0
        n = len(bars)
        while i < n:
            # Publish all instruments' bars sharing this dataset timestamp,
            # then pace one unique timestamp per interval. Grouping compares
            # the raw (possibly naive) values; `ts` is normalized for use.
            raw_ts = bars[i][1]
            ts = as_utc(raw_ts)
            j = i
            while j < n and bars[j][1] == raw_ts:
                instrument_id, _ts, open_, high, low, close, volume = bars[j]
                instrument = by_id.get(instrument_id)
                if instrument is not None:
                    snapshot = set_tick(
                        instrument_id, instrument.symbol, close, ts, volume
                    )
                    await bus.publish(
                        "market.ticks", _tick_payload(instrument, snapshot, ts)
                    )
                j += 1
            i = j
            await asyncio.sleep(pause)
        if settings.REPLAY_MODE == "hold":
            logger.info("tick replayer: dataset exhausted, holding last prices")
            while True:
                await asyncio.sleep(3600)
        logger.info("tick replayer: dataset exhausted, looping to start")


async def _persist(sessionmaker, rows: list[PriceTick]) -> None:
    try:
        async with sessionmaker() as session:
            session.add_all(rows)
            await session.commit()
    except Exception:
        logger.exception("tick replayer: persist failed (skipped)")


async def _replay_fallback(bus, sessionmaker, settings, instruments) -> None:
    """Random-walk live ticks (no dataset available)."""
    interval = max(settings.TICK_INTERVAL_MS, 10) / 1000.0
    rng = random.Random()
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
                    "market.ticks", _tick_payload(instrument, snapshot, ts)
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


def build_tick_replayer(settings):
    """Bind settings; returns the `tick_replayer(bus, sessionmaker)` worker."""

    async def tick_replayer(bus, sessionmaker):
        instruments, mode = await _shielded(_bootstrap(sessionmaker, settings))
        if mode == "dataset":
            await _replay_dataset(bus, sessionmaker, settings, instruments)
        else:
            await _replay_fallback(bus, sessionmaker, settings, instruments)

    return tick_replayer
