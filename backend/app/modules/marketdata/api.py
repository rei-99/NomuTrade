"""Market-data read endpoints: instrument catalog + OHLC price series."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import NotFound, ValidationError
from app.core.models import Instrument, PriceTick
from app.core.security import SessionData, get_current_user
from app.core.timeutil import utcnow
from app.modules.marketdata.registry import get_latest_price, warm_from_db

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

    now = utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if timeframe == "1D":
        # Today's live ticks only; gaps are simply omitted.
        rows = (
            (
                await db.execute(
                    select(PriceTick)
                    .where(
                        PriceTick.instrument_id == instrument.instrument_id,
                        PriceTick.ts >= today,
                    )
                    .order_by(PriceTick.ts)
                )
            )
            .scalars()
            .all()
        )
        candles = [
            _candle(r.ts, r.open, r.high, r.low, r.close, r.volume) for r in rows
        ]
    else:
        days = TIMEFRAMES[timeframe]
        cutoff = today - timedelta(days=days) if days is not None else None
        stmt = select(PriceTick).where(
            PriceTick.instrument_id == instrument.instrument_id,
            PriceTick.ts < today,
        )
        if cutoff is not None:
            stmt = stmt.where(PriceTick.ts >= cutoff)
        rows = (
            (await db.execute(stmt.order_by(PriceTick.ts))).scalars().all()
        )
        candles = [
            _candle(r.ts, r.open, r.high, r.low, r.close, r.volume) for r in rows
        ]
        # Fold today's live ticks into one partial candle at the end.
        today_rows = (
            (
                await db.execute(
                    select(PriceTick)
                    .where(
                        PriceTick.instrument_id == instrument.instrument_id,
                        PriceTick.ts >= today,
                    )
                    .order_by(PriceTick.ts)
                )
            )
            .scalars()
            .all()
        )
        if today_rows:
            candles.append(
                _candle(
                    today_rows[-1].ts,
                    today_rows[0].open,
                    max(r.high for r in today_rows),
                    min(r.low for r in today_rows),
                    today_rows[-1].close,
                    sum(r.volume for r in today_rows),
                )
            )

    return {"symbol": symbol, "timeframe": timeframe, "candles": candles}
