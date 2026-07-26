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
from app.core.timeutil import as_utc


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
    ts = as_utc(ts)
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
