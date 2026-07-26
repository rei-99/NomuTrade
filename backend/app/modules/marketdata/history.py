"""History bootstrap and live-tick price math for the simulated feed.

There is no `data.zip` in this environment (TBD-06 open item), so the loader
generates a deterministic 120-day daily OHLC random walk per tradable
instrument instead. Prices respect each instrument's tick size; start prices
are between 1000 and 9000 JPY.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.core.models import Instrument, PriceTick
from app.core.timeutil import utcnow

HISTORY_DAYS = 120
MAX_TICK_STEP_PCT = Decimal("0.003")  # ±0.3% max step per live tick


def round_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    """Round a price to the instrument's tick size; never below one tick."""
    if tick_size <= 0:
        return price
    ticks = (price / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if ticks < 1:
        ticks = Decimal("1")
    return ticks * tick_size


def generate_daily_history(instruments: list[Instrument]) -> list[PriceTick]:
    """120 days of daily OHLC rows per instrument (random walk with drift).

    Deterministic per symbol so re-created dev databases look alike.
    """
    rows: list[PriceTick] = []
    today = utcnow().date()
    for instrument in instruments:
        rng = random.Random(f"stp-history-{instrument.symbol}")
        price = round_to_tick(
            Decimal(str(rng.uniform(1000, 9000))), instrument.tick_size
        )
        for days_ago in range(HISTORY_DAYS, 0, -1):
            day = today - timedelta(days=days_ago)
            ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            daily_return = rng.gauss(0.0004, 0.012)  # slight upward drift
            open_ = price
            close = round_to_tick(
                open_ * Decimal(str(1 + daily_return)), instrument.tick_size
            )
            high = round_to_tick(
                max(open_, close) * Decimal(str(1 + abs(rng.gauss(0, 0.004)))),
                instrument.tick_size,
            )
            low = round_to_tick(
                min(open_, close) * Decimal(str(1 - abs(rng.gauss(0, 0.004)))),
                instrument.tick_size,
            )
            high = max(high, open_, close)
            low = min(low, open_, close)
            rows.append(
                PriceTick(
                    instrument_id=instrument.instrument_id,
                    ts=ts,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=Decimal(rng.randint(100_000, 10_000_000)),
                )
            )
            price = close
    return rows


def next_live_price(latest: Decimal, tick_size: Decimal, rng: random.Random) -> Decimal:
    """One random-walk step of at most ±0.3%, rounded to the tick size."""
    step = Decimal(str(rng.uniform(-float(MAX_TICK_STEP_PCT), float(MAX_TICK_STEP_PCT))))
    candidate = round_to_tick(latest * (1 + step), tick_size)
    if candidate == latest:  # rounding absorbed the move; nudge one tick
        candidate = latest + tick_size if step >= 0 else latest - tick_size
    if candidate <= 0:
        candidate = latest
    return candidate
