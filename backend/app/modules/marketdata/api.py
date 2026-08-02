"""Market-data read endpoints: instrument catalog + OHLC price series."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.db import get_db
from app.core.errors import NotFound, StateConflict, ValidationError
from app.core.models import Instrument, PriceTick
from app.core.security import SessionData, get_current_user
from app.core.timeutil import as_utc
from app.modules.marketdata import worker as replay_worker
from app.modules.marketdata.registry import (
    get_latest_price,
    get_sim_now,
    warm_from_db,
)

router = APIRouter(tags=["marketdata"])

TIMEFRAMES: dict[str, int | None] = {
    "1D": 1,
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "1Y": 365,
    "MAX": None,
}


def _candle(ts, open_, high, low, close, volume) -> dict:
    return {
        "ts": ts.isoformat(),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


def _aggregate_daily(rows) -> list[dict]:
    """Aggregate ts-ordered ticks into daily candles (D-13).

    Works for both stored daily rows (one per day) and minute bars (~390 per
    day); gaps in the dataset are simply absent days.
    """
    days: dict[str, dict] = {}
    for r in rows:
        key = as_utc(r.ts).date().isoformat()
        candle = days.get(key)
        if candle is None:
            days[key] = {
                "ts": key,
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": float(r.volume),
            }
        else:
            candle["high"] = max(candle["high"], float(r.high))
            candle["low"] = min(candle["low"], float(r.low))
            candle["close"] = float(r.close)
            candle["volume"] += float(r.volume)
    return list(days.values())


@router.get("/instruments")
async def list_instruments(
    user: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    instruments = (
        (await db.execute(select(Instrument).order_by(Instrument.symbol)))
        .scalars()
        .all()
    )
    await warm_from_db(
        db,
        [i.instrument_id for i in instruments],
        {i.instrument_id: i.symbol for i in instruments},
    )
    items = [
        {
            "instrument_id": i.instrument_id,
            "symbol": i.symbol,
            "name": i.name,
            "asset_class": i.asset_class,
            "currency": i.currency,
            "lot_size": float(i.lot_size),
            "tick_size": float(i.tick_size),
            "tradable": i.tradable,
            "latest_price": (
                float(price)
                if (price := get_latest_price(i.instrument_id)) is not None
                else None
            ),
        }
        for i in instruments
    ]
    return {"items": items, "next_cursor": None}


@router.get("/instruments/{symbol}/prices")
async def get_prices(
    symbol: str,
    timeframe: str = "1M",
    user: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    timeframe = timeframe.upper()
    if timeframe not in TIMEFRAMES:
        raise ValidationError(
            f"unsupported timeframe: {timeframe}",
            details=[{"code": "INVALID_TIMEFRAME", "timeframe": timeframe}],
        )
    instrument = (
        await db.execute(select(Instrument).where(Instrument.symbol == symbol))
    ).scalar_one_or_none()
    if instrument is None:
        raise NotFound(f"instrument not found: {symbol}")

    # Reference day = the simulation clock's latest tick date for this
    # instrument (D-10), not wall-clock today: the feed replays dataset time.
    # While a replay is running, data beyond the sim clock is withheld — the
    # platform must not "know the future" of the dataset.
    sim_now = get_sim_now()
    if sim_now is not None:
        latest_ts = await db.scalar(
            select(func.max(PriceTick.ts)).where(
                PriceTick.instrument_id == instrument.instrument_id,
                PriceTick.ts <= sim_now,
            )
        )
    else:  # no replay running (tests, RUN_WORKERS=false): latest stored tick
        latest_ts = await db.scalar(
            select(func.max(PriceTick.ts)).where(
                PriceTick.instrument_id == instrument.instrument_id
            )
        )
    if latest_ts is None:
        return {"symbol": symbol, "timeframe": timeframe, "candles": []}
    ref_day = as_utc(latest_ts).replace(hour=0, minute=0, second=0, microsecond=0)

    if timeframe == "1D":
        # Intraday minute bars of the reference day; gaps are simply omitted.
        stmt = select(PriceTick).where(
            PriceTick.instrument_id == instrument.instrument_id,
            PriceTick.ts >= ref_day,
        )
        if sim_now is not None:
            stmt = stmt.where(PriceTick.ts <= sim_now)
        rows = (
            (await db.execute(stmt.order_by(PriceTick.ts))).scalars().all()
        )
        candles = [
            _candle(r.ts, r.open, r.high, r.low, r.close, r.volume) for r in rows
        ]
    else:
        days = TIMEFRAMES[timeframe]
        cutoff = ref_day - timedelta(days=days) if days is not None else None
        stmt = select(PriceTick).where(
            PriceTick.instrument_id == instrument.instrument_id,
        )
        if cutoff is not None:
            stmt = stmt.where(PriceTick.ts >= cutoff)
        if sim_now is not None:
            stmt = stmt.where(PriceTick.ts <= sim_now)
        rows = (
            (await db.execute(stmt.order_by(PriceTick.ts))).scalars().all()
        )
        daily = _aggregate_daily(rows)
        # Fold the reference day's minute bars into one partial candle so the
        # last candle tracks the live feed.
        ref_key = ref_day.date().isoformat()
        ref_rows = [r for r in rows if as_utc(r.ts) >= ref_day]
        if ref_rows and daily:
            daily[-1] = _candle(
                as_utc(ref_rows[-1].ts),
                ref_rows[0].open,
                max(r.high for r in ref_rows),
                min(r.low for r in ref_rows),
                ref_rows[-1].close,
                sum(r.volume for r in ref_rows),
            )
            daily[-1]["ts"] = ref_key
        candles = daily

    return {"symbol": symbol, "timeframe": timeframe, "candles": candles}


# ---------------------------------------------------------------------------
# Replay fast-forward (training/demo control)
# ---------------------------------------------------------------------------


class ReplaySkipRequest(BaseModel):
    days: int = Field(1, ge=1, le=10)


@router.post("/marketdata/replay/skip")
async def replay_skip(
    body: ReplaySkipRequest,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Flush the replay forward by whole market days at full speed: every bar
    up to the target day's first bar is published and processed (fills,
    triggers, snapshots), then normal pacing resumes. Prices, sim clock, news
    visibility and settlement timing all follow the tick stream. Any
    authenticated user may use it — training-environment control, audited.
    409 when the fallback feed is running (there is no replay to flush)."""
    if not replay_worker.request_replay_skip(body.days):
        raise StateConflict("replay skip is available only in dataset replay mode")
    await write_audit(
        db,
        actor_id=session.user_id,
        event_type="REPLAY_SKIP",
        resource_type="SIMULATION",
        resource_id="replay",
        severity="INFO",
        payload={"days": body.days, "mode": "flush"},
        flush_only=True,
    )
    await db.commit()
    return {"skipped_days": body.days}
