"""In-memory latest-price registry (process-local).

The platform runs as a single-process modular monolith, so the tick replayer,
execution engine, valuation projector and API endpoints all share this dict
instead of the `px:latest:*` Redis keys from the design doc (the dev default
has no Redis). On cold start the replayer warms it from the latest PriceTick
rows; API endpoints warm it lazily for instruments missing here (covers the
RUN_WORKERS=false case).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import PriceTick
from app.core.timeutil import as_utc, utcnow


@dataclass
class PriceSnapshot:
    instrument_id: str
    symbol: str
    price: Decimal
    ts: datetime
    day_open: Decimal
    day_high: Decimal
    day_low: Decimal
    day_volume: Decimal
    day: date


_LATEST: dict[str, PriceSnapshot] = {}
_SIM_NOW: datetime | None = None


def reset_registry() -> None:
    """Clear all process-local state (called at app startup).

    The registry belongs to one app instance; without a reset, a second app
    in the same process (e.g. the next test) would inherit its sim clock and
    latest prices.
    """
    global _SIM_NOW
    _LATEST.clear()
    _SIM_NOW = None


def set_sim_now(ts: datetime | None) -> None:
    """Move the simulation clock explicitly (replay start position).

    Needed because warm_from_db seeds the clock from the latest *stored* tick
    (the dataset's end); the replay then re-bases it to the first bar being
    replayed so consumers never see past the replay position.
    """
    global _SIM_NOW
    _SIM_NOW = ts


def get_sim_now() -> datetime | None:
    """The simulation clock (D-10): latest tick timestamp seen across all
    instruments. When replaying dataset history this is a dataset timestamp,
    not wall-clock time — price staleness and chart ranges are measured
    against it, never against utcnow(). None before the first tick."""
    return _SIM_NOW


def business_now() -> datetime:
    """Sim clock when a feed is running, wall clock otherwise.

    Business-domain timestamps (orders, executions, settlement display times)
    use this so blotter times live in market time alongside charts and news.
    Operational timing (sweeper cadence, audit rows, notifications) keeps
    utcnow(). Note: a replay loop re-bases the sim clock backwards — order
    times then sit "ahead" of the clock, which is the honest sim-world story.
    """
    return get_sim_now() or utcnow()


def get_snapshot(instrument_id: str) -> PriceSnapshot | None:
    """Full latest-price snapshot (price + running day OHLC), or None."""
    return _LATEST.get(instrument_id)


def get_latest_price(instrument_id: str) -> Decimal | None:
    """Latest known price for an instrument, or None if never seen."""
    snap = _LATEST.get(instrument_id)
    return snap.price if snap is not None else None


def set_tick(
    instrument_id: str,
    symbol: str,
    price: Decimal,
    ts: datetime,
    volume: Decimal,
) -> PriceSnapshot:
    """Record a live tick, maintaining running intraday OHLC/volume."""
    global _SIM_NOW
    ts = as_utc(ts)
    if _SIM_NOW is None or ts > _SIM_NOW:
        _SIM_NOW = ts
    snap = _LATEST.get(instrument_id)
    if snap is None or snap.day != ts.date():
        snap = PriceSnapshot(
            instrument_id=instrument_id,
            symbol=symbol,
            price=price,
            ts=ts,
            day_open=price,
            day_high=price,
            day_low=price,
            day_volume=volume,
            day=ts.date(),
        )
    else:
        snap.price = price
        snap.ts = ts
        snap.day_high = max(snap.day_high, price)
        snap.day_low = min(snap.day_low, price)
        snap.day_volume += volume
    _LATEST[instrument_id] = snap
    return snap


async def warm_from_db(
    session: AsyncSession,
    instrument_ids: list[str],
    symbols: dict[str, str] | None = None,
) -> None:
    """Cold-start fallback: fill missing registry entries from the latest
    PriceTick close of each instrument."""
    missing = [iid for iid in instrument_ids if iid not in _LATEST]
    for instrument_id in missing:
        row = (
            await session.execute(
                select(PriceTick)
                .where(PriceTick.instrument_id == instrument_id)
                .order_by(PriceTick.ts.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            continue
        ts = as_utc(row.ts)
        global _SIM_NOW
        if _SIM_NOW is None or ts > _SIM_NOW:
            _SIM_NOW = ts
        _LATEST[instrument_id] = PriceSnapshot(
            instrument_id=instrument_id,
            symbol=(symbols or {}).get(instrument_id, ""),
            price=row.close,
            ts=ts,
            day_open=row.open,
            day_high=row.high,
            day_low=row.low,
            day_volume=row.volume,
            day=ts.date(),
        )
