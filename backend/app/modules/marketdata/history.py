"""History bootstrap and live-tick price math for the simulated feed.

Used only when the simulation dataset (data/, INT-04) is absent — the loader
falls back to a deterministic 120-day daily OHLC random walk per tradable
instrument. Prices respect each instrument's tick size; start prices are
between 100 and 500 (USD-scale, matching the dataset universe).

Bonds (design 21 §A2): the dataset pack ships no bond data, so bond series
are GENERATED — a mean-reverting (Ornstein-Uhlenbeck) walk around par 100,
~0.3% daily vol with minute vol scaled by 1/sqrt(390), visibly calmer than
the equity random walk. `generate_bond_series` builds the dataset-mode
series (daily backfill + minute bars on the dataset's own minute
timestamps); the fallback daily history below routes BOND instruments
through the same OU step.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.core.models import Instrument, PriceTick
from app.core.timeutil import as_utc, utcnow

HISTORY_DAYS = 120
MAX_TICK_STEP_PCT = Decimal("0.003")  # ±0.3% max step per live tick

# Generated bond series (quoted % of par): mean-reversion around par 100.
BOND_PAR = Decimal("100")
BOND_START_RANGE = (99.5, 100.5)
BOND_DAILY_KAPPA = 0.08  # per-step pull toward par
BOND_DAILY_SIGMA = 0.30  # ~0.3% of par per day
BOND_MINUTES_PER_DAY = 390  # 09:30–15:59
BOND_DAILY_WIGGLE = 0.0005  # high/low spread around open/close, daily bars
BOND_MINUTE_WIGGLE = 0.0002  # same, minute bars


def round_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    """Round a price to the instrument's tick size; never below one tick."""
    if tick_size <= 0:
        return price
    ticks = (price / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if ticks < 1:
        ticks = Decimal("1")
    return ticks * tick_size


def _bar(
    instrument: Instrument,
    ts: datetime,
    open_: Decimal,
    close: Decimal,
    rng: random.Random,
    wiggle: float,
    volume_range: tuple[int, int],
) -> PriceTick:
    """One OHLC row: high/low jittered around open/close, random volume."""
    high = round_to_tick(
        max(open_, close) * Decimal(str(1 + abs(rng.gauss(0, wiggle)))),
        instrument.tick_size,
    )
    low = round_to_tick(
        min(open_, close) * Decimal(str(1 - abs(rng.gauss(0, wiggle)))),
        instrument.tick_size,
    )
    return PriceTick(
        instrument_id=instrument.instrument_id,
        ts=ts,
        open=open_,
        high=max(high, open_, close),
        low=min(low, open_, close),
        close=close,
        volume=Decimal(rng.randint(*volume_range)),
    )


def _bond_step(
    price: Decimal,
    tick_size: Decimal,
    rng: random.Random,
    kappa: float,
    sigma: float,
) -> Decimal:
    """One OU step toward par: mean-reversion pull + gaussian shock."""
    pull = Decimal(str(kappa)) * (BOND_PAR - price)
    shock = Decimal(str(rng.gauss(0.0, sigma)))
    return round_to_tick(price + pull + shock, tick_size)


def generate_daily_history(instruments: list[Instrument]) -> list[PriceTick]:
    """120 days of daily OHLC rows per instrument.

    Equities random-walk with drift (start 100–500); bonds mean-revert
    around par (GENERATED — the dataset has no bond feed, §A2).
    Deterministic per symbol so re-created dev databases look alike.
    """
    rows: list[PriceTick] = []
    today = utcnow().date()
    for instrument in instruments:
        rng = random.Random(f"stp-history-{instrument.symbol}")
        bond = instrument.asset_class == "BOND"
        lo, hi = BOND_START_RANGE if bond else (100, 500)
        wiggle = BOND_DAILY_WIGGLE if bond else 0.004
        volume_range = (10_000, 2_000_000) if bond else (100_000, 10_000_000)
        price = round_to_tick(
            Decimal(str(rng.uniform(lo, hi))), instrument.tick_size
        )
        for days_ago in range(HISTORY_DAYS, 0, -1):
            day = today - timedelta(days=days_ago)
            ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            open_ = price
            if bond:
                close = _bond_step(
                    price, instrument.tick_size, rng, BOND_DAILY_KAPPA,
                    BOND_DAILY_SIGMA,
                )
            else:
                daily_return = rng.gauss(0.0004, 0.012)  # slight upward drift
                close = round_to_tick(
                    open_ * Decimal(str(1 + daily_return)), instrument.tick_size
                )
            rows.append(
                _bar(instrument, ts, open_, close, rng, wiggle, volume_range)
            )
            price = close
    return rows


def generate_bond_series(
    instrument: Instrument,
    live_start: date,
    minute_timestamps: list[datetime],
) -> list[PriceTick]:
    """Full GENERATED price series for one bond (dataset mode, §A2).

    120 daily bars backfilling the window plus one minute bar per dataset
    live timestamp (~17k) — mean-reverting around par 100, ~0.3% daily vol,
    deterministic per symbol. Generated because data.zip has no bond data.
    `minute_timestamps` are the equity live CSVs' own timestamps, so the
    generated bonds replay in lockstep with the rest of the tape.
    """
    rng = random.Random(f"stp-bond-{instrument.symbol}")
    tick = instrument.tick_size
    price = round_to_tick(Decimal(str(rng.uniform(*BOND_START_RANGE))), tick)
    minute_kappa = BOND_DAILY_KAPPA / BOND_MINUTES_PER_DAY
    minute_sigma = BOND_DAILY_SIGMA / math.sqrt(BOND_MINUTES_PER_DAY)
    rows: list[PriceTick] = []

    # Daily backfill: 120 business days before the live window (same role as
    # the equities' historical CSVs; inside the window the minute bars win).
    days: list[date] = []
    day = live_start - timedelta(days=1)
    while len(days) < HISTORY_DAYS:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    for d in reversed(days):
        ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        open_ = price
        close = _bond_step(price, tick, rng, BOND_DAILY_KAPPA, BOND_DAILY_SIGMA)
        rows.append(
            _bar(instrument, ts, open_, close, rng, BOND_DAILY_WIGGLE,
                 (10_000, 2_000_000))
        )
        price = close

    # Minute bars across the live window.
    for ts in minute_timestamps:
        open_ = price
        close = _bond_step(price, tick, rng, minute_kappa, minute_sigma)
        rows.append(
            _bar(instrument, as_utc(ts), open_, close, rng, BOND_MINUTE_WIGGLE,
                 (1_000, 200_000))
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
