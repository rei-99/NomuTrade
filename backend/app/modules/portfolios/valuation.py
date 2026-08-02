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

from sqlalchemy import and_, func, select
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
from app.modules.analytics.bonds import (
    modified_duration as _bond_mod_duration,
    payment_count as _bond_payment_count,
    solve_ytm as _bond_solve_ytm,
)

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
    db: AsyncSession, portfolio_id: str, *, values: list[float] | None = None
) -> float | None:
    """Annualized volatility of daily total_value.

    Per the contract: stdev of the daily total_value series, annualized with
    sqrt(252), expressed as a percentage of the mean total_value. None when
    fewer than 10 daily points exist (FR-PFM-003 minimum history). Series
    source: ValuationSnapshots, falling back to the current book repriced
    through stored daily closes (see `_daily_total_values`).
    """
    values = values if values is not None else await _daily_total_values(db, portfolio_id)
    if len(values) < 10:
        return None
    mean = statistics.fmean(values)
    if mean == 0:
        return None
    return statistics.stdev(values) * (252**0.5) / mean * 100


async def _daily_total_values(db: AsyncSession, portfolio_id: str) -> list[float]:
    """Daily total_value (market value + cash) series, one point per day,
    chronological. Primary source: ValuationSnapshots. When live snapshot
    history is too short (< 10 days, e.g. a fresh book), falls back to
    repricing the CURRENT book through stored daily closes — the standard
    "how would today's book have moved" approximation — so risk KPIs are
    meaningful from day one instead of N/A."""
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
    if len(values) >= 10:
        return values
    fallback = await _repriced_daily_values(db, portfolio_id)
    return fallback if len(fallback) > len(values) else values


async def _repriced_daily_values(db: AsyncSession, portfolio_id: str) -> list[float]:
    """Current positions repriced through stored daily closes + current cash
    (held constant), sim-clock capped (D-10). Days missing any held
    instrument's close are skipped. Empty when the book has no positions."""
    rows = (
        await db.execute(
            select(Position, Instrument)
            .join(Instrument, Instrument.instrument_id == Position.instrument_id)
            .where(Position.portfolio_id == portfolio_id, Position.quantity != 0)
        )
    ).all()
    if not rows:
        return []
    portfolio = await db.get(Portfolio, portfolio_id)
    if portfolio is None:
        return []
    now = get_sim_now() or utcnow()
    # SQLite stores naive datetimes and silently mis-compares tz-aware bind
    # params (AGENTS.md pitfall); Postgres needs the aware value for
    # timestamptz. Bind per dialect.
    if db.get_bind().dialect.name == "sqlite":
        now = now.replace(tzinfo=None)
    ids = [instrument.instrument_id for _, instrument in rows]
    # One close per (instrument, day) = the close of that day's last tick,
    # aggregated in SQL so minute bars never leave the database.
    day_col = func.date(PriceTick.ts)
    last_ts = (
        select(
            PriceTick.instrument_id.label("iid"),
            day_col.label("day"),
            func.max(PriceTick.ts).label("last_ts"),
        )
        .where(PriceTick.instrument_id.in_(ids), PriceTick.ts <= now)
        .group_by(PriceTick.instrument_id, day_col)
        .subquery()
    )
    closes = (
        await db.execute(
            select(last_ts.c.iid, last_ts.c.day, PriceTick.close).join(
                PriceTick,
                and_(
                    PriceTick.instrument_id == last_ts.c.iid,
                    PriceTick.ts == last_ts.c.last_ts,
                ),
            )
        )
    ).all()
    by_day: dict[object, dict[str, Decimal]] = {}
    for iid, day, close in closes:
        by_day.setdefault(day, {})[iid] = close
    values: list[float] = []
    for day in sorted(by_day):
        priced = by_day[day]
        total = portfolio.cash_balance
        complete = True
        for position, instrument in rows:
            close = priced.get(instrument.instrument_id)
            if close is None:
                complete = False
                break
            total += trade_value(instrument, position.quantity, close)
        if complete:
            values.append(float(total))
    return values


async def var_95_1d_pct(
    db: AsyncSession, portfolio_id: str, *, values: list[float] | None = None
) -> float | None:
    """Historical 1-day 95% VaR as % of portfolio value (risk metric, A-feat).

    Percentile of daily total-value returns: VaR = -5th percentile x 100, so a
    positive number means "5% of days lost more than this". 0 when the 5th
    percentile is non-negative (no observed losses at that confidence).
    None with fewer than 10 return observations (same minimum as volatility).
    Series source: ValuationSnapshots, falling back to the current book
    repriced through stored daily closes (see `_daily_total_values`).
    """
    values = values if values is not None else await _daily_total_values(db, portfolio_id)
    returns = _returns(values)
    if len(returns) < 10:
        return None
    q5 = statistics.quantiles(returns, n=20)[0]  # 5th percentile
    return max(0.0, -q5 * 100)


async def expected_shortfall_95_1d_pct(
    db: AsyncSession, portfolio_id: str, *, values: list[float] | None = None
) -> float | None:
    """Historical 1-day 95% expected shortfall (CVaR) as % of portfolio value.

    Mean of the daily returns at/below the 5th percentile — "when the worst
    5% of days happen, this is the average loss". Always >= VaR-95 by
    construction. Same series and minimum-history rules as VaR.
    """
    values = values if values is not None else await _daily_total_values(db, portfolio_id)
    returns = _returns(values)
    if len(returns) < 10:
        return None
    q5 = statistics.quantiles(returns, n=20)[0]
    tail = [r for r in returns if r <= q5]
    if not tail:
        return 0.0
    return max(0.0, -statistics.fmean(tail) * 100)


async def sharpe_ratio(
    db: AsyncSession, portfolio_id: str, *, values: list[float] | None = None
) -> float | None:
    """Annualized Sharpe ratio of daily total-value returns (rf = 0 —
    training-environment simplification, documented). None with fewer than
    10 observations or zero dispersion."""
    values = values if values is not None else await _daily_total_values(db, portfolio_id)
    returns = _returns(values)
    if len(returns) < 10:
        return None
    sd = statistics.stdev(returns)
    if sd == 0:
        return None
    return statistics.fmean(returns) / sd * (252**0.5)


def _returns(values: list[float]) -> list[float]:
    return [b / a - 1 for a, b in zip(values, values[1:]) if a]


async def max_drawdown_pct(
    db: AsyncSession, portfolio_id: str, *, values: list[float] | None = None
) -> float | None:
    """Largest peak-to-trough decline of daily total_value, as % of the peak.

    None with fewer than 2 daily points.
    """
    values = values if values is not None else await _daily_total_values(db, portfolio_id)
    if len(values) < 2:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = max(worst, (peak - value) / peak)
    return worst * 100


async def compute_total_value(db: AsyncSession, portfolio: Portfolio) -> Decimal:
    """cash + market value of positions at latest prices."""
    valuations = await value_positions(db, portfolio.portfolio_id)
    market = sum(
        (v.market_value for v in valuations if v.market_value is not None),
        Decimal("0"),
    )
    return portfolio.cash_balance + market


def bond_book_metrics(valuations: list[PositionValuation]) -> dict[str, float | None]:
    """Market-value-weighted YTM % and modified duration of bond holdings.

    Same conventions as GET /instruments/{symbol}/bond-analytics (design 24):
    annual coupons, clean price, years measured on the sim clock (D-10).
    Both keys are None when the book holds no priced bonds with
    coupon/maturity data (e.g. equity-only books).
    """
    now = get_sim_now() or utcnow()
    wtd_ytm = 0.0
    wtd_dur = 0.0
    total = 0.0
    for v in valuations:
        instrument = v.instrument
        if (
            instrument.asset_class != "BOND"
            or instrument.coupon_rate is None
            or instrument.maturity_date is None
            or v.market_value is None
            or v.latest_price is None
        ):
            continue
        seconds = (as_utc(instrument.maturity_date) - as_utc(now)).total_seconds()
        n = _bond_payment_count(max(0.0, seconds / (365.25 * 86_400)))
        coupon = float(instrument.coupon_rate)
        ytm = _bond_solve_ytm(float(v.latest_price), coupon, n)
        duration = _bond_mod_duration(coupon, n, ytm)
        weight = float(v.market_value)
        wtd_ytm += ytm * weight
        wtd_dur += duration * weight
        total += weight
    if total == 0.0:
        return {"bond_wtd_ytm_pct": None, "bond_wtd_mod_duration": None}
    return {
        "bond_wtd_ytm_pct": wtd_ytm / total,
        "bond_wtd_mod_duration": wtd_dur / total,
    }
