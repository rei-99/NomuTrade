"""Technical analytics: on-demand indicators over PriceTick + price alerts.

Indicators (DESIGN 05) are computed on demand from PriceTick closes via the
pure functions in indicators.py — no separate persistence. Price alerts are
evaluated by the alert_evaluator worker against the `market.ticks` stream.

The evaluator keeps ACTIVE rules in an in-memory cache (refreshed every 60 s,
invalidated synchronously by the create/disable endpoints) and touches the DB
only on cache refreshes and actual triggers — a hot tick stream costs no DB
work when nothing fires. Rules are one-shot: a triggered rule moves to status
TRIGGERED and is re-armed by DELETE + recreate. With zero ticks on the stream
the worker simply idles.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.db import get_db
from app.core.errors import NotFound, ValidationError
from app.core.events import write_outbox
from app.core.models import AlertRule, Instrument, PriceTick
from app.core.security import SessionData, get_current_user
from app.core.timeutil import as_utc, utcnow

from app.modules.analytics import indicators as ind

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])

PRICE_ALERT_TRIGGERED = "PRICE_ALERT_TRIGGERED"

TIMEFRAME_DAYS: dict[str, int | None] = {
    "1D": 1,
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "1Y": 365,
    "MAX": None,
}
KNOWN_INDICATORS = frozenset({"SMA", "EMA", "RSI", "MACD", "BB"})
MAX_POINTS = 1000  # keep responses bounded; ticks beyond this are truncated

CONDITIONS = ("ABOVE", "BELOW", "CROSSES_ABOVE", "CROSSES_BELOW")


# ---------------------------------------------------------------------------
# Indicators endpoint
# ---------------------------------------------------------------------------


@router.get("/instruments/{symbol}/indicators")
async def get_indicators(
    symbol: str,
    timeframe: str = Query("3M"),
    indicators: str = Query("SMA,EMA,RSI,MACD,BB"),
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tf = timeframe.upper()
    if tf not in TIMEFRAME_DAYS:
        raise ValidationError(
            f"unknown timeframe {timeframe!r}; expected one of {sorted(TIMEFRAME_DAYS)}"
        )
    requested = [s.strip().upper() for s in indicators.split(",") if s.strip()]
    unknown = sorted(set(requested) - KNOWN_INDICATORS)
    if unknown:
        raise ValidationError(f"unknown indicators: {', '.join(unknown)}")

    instrument = (
        await db.execute(select(Instrument).where(Instrument.symbol == symbol))
    ).scalar_one_or_none()
    if instrument is None:
        raise NotFound(f"unknown instrument symbol: {symbol}")

    stmt = (
        select(PriceTick.ts, PriceTick.close)
        .where(PriceTick.instrument_id == instrument.instrument_id)
        .order_by(PriceTick.ts.desc())
        .limit(MAX_POINTS)
    )
    days = TIMEFRAME_DAYS[tf]
    if days is not None:
        stmt = stmt.where(PriceTick.ts >= utcnow() - timedelta(days=days))
    rows = (await db.execute(stmt)).all()
    rows.reverse()  # chronological order

    timestamps = [as_utc(ts).isoformat() for ts, _ in rows]
    closes = [float(close) for _, close in rows]

    result: dict[str, list] = {}
    if "SMA" in requested:
        result["SMA"] = [
            {"ts": ts, "value": value}
            for value, ts in zip(ind.sma(closes, 20), timestamps)
            if value is not None
        ]
    if "EMA" in requested:
        result["EMA"] = [
            {"ts": ts, "value": value}
            for value, ts in zip(ind.ema(closes, 20), timestamps)
            if value is not None
        ]
    if "RSI" in requested:
        result["RSI"] = [
            {"ts": ts, "value": value}
            for value, ts in zip(ind.rsi(closes, 14), timestamps)
            if value is not None
        ]
    if "MACD" in requested:
        result["MACD"] = [
            {"ts": ts, "macd": entry[0], "signal": entry[1], "histogram": entry[2]}
            for entry, ts in zip(ind.macd(closes), timestamps)
            if entry is not None
        ]
    if "BB" in requested:
        upper, middle, lower = ind.bollinger(closes, 20, 2.0)
        result["BB"] = [
            {"ts": ts, "upper": u, "middle": m, "lower": lo}
            for u, m, lo, ts in zip(upper, middle, lower, timestamps)
            if m is not None
        ]
    # Insufficient data yields an empty series for that indicator, not an error.
    return {"symbol": symbol, "timeframe": tf, "indicators": result}


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------


class AlertRuleRequest(BaseModel):
    instrument: str  # symbol, e.g. "7203.T"
    condition: Literal["ABOVE", "BELOW", "CROSSES_ABOVE", "CROSSES_BELOW"]
    threshold: Decimal


def _rule_json(rule: AlertRule, symbol: str) -> dict:
    return {
        "rule_id": rule.rule_id,
        "instrument": symbol,
        "instrument_id": rule.instrument_id,
        "condition": rule.condition,
        "threshold": float(rule.threshold),
        "status": rule.status,
        "created_at": as_utc(rule.created_at).isoformat(),
    }


@router.get("/analytics/alerts")
async def list_alerts(
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        (
            await db.execute(
                select(AlertRule, Instrument.symbol)
                .join(Instrument, AlertRule.instrument_id == Instrument.instrument_id)
                .where(AlertRule.user_id == session.user_id)
                .order_by(AlertRule.created_at.desc())
            )
        )
        .all()
    )
    return {
        "items": [_rule_json(rule, symbol) for rule, symbol in rows],
        "next_cursor": None,
    }


@router.post("/analytics/alerts", status_code=201)
async def create_alert(
    body: AlertRuleRequest,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    instrument = (
        await db.execute(select(Instrument).where(Instrument.symbol == body.instrument))
    ).scalar_one_or_none()
    if instrument is None:
        raise ValidationError(f"unknown instrument symbol: {body.instrument}")
    if body.threshold <= 0:
        raise ValidationError("threshold must be positive")
    rule = AlertRule(
        user_id=session.user_id,
        instrument_id=instrument.instrument_id,
        condition=body.condition,
        threshold=body.threshold,
        status="ACTIVE",
    )
    db.add(rule)
    await db.commit()
    invalidate_alert_cache()
    return _rule_json(rule, instrument.symbol)


@router.delete("/analytics/alerts/{rule_id}")
async def disable_alert(
    rule_id: str,
    session: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await db.get(AlertRule, rule_id)
    if rule is None or rule.user_id != session.user_id:
        raise NotFound("alert rule not found")
    rule.status = "DISABLED"
    await db.commit()
    invalidate_alert_cache()
    return {"rule_id": rule.rule_id, "status": rule.status}


# ---------------------------------------------------------------------------
# Alert evaluator worker (market.ticks -> notify + audit + TRIGGERED)
# ---------------------------------------------------------------------------

# Last price seen per symbol, for CROSSES_* conditions. Process-local: after a
# restart the first tick has no "previous" and cannot fire a CROSSES_* rule
# (documented MVP behavior; ABOVE/BELOW are unaffected).
_last_prices: dict[str, Decimal] = {}

# In-memory cache of ACTIVE rules by symbol. Refreshed every
# RULE_CACHE_TTL_SECONDS and invalidated synchronously by the create/disable
# endpoints, so the evaluator does zero DB work per tick unless a rule fires.
RULE_CACHE_TTL_SECONDS = 60.0
_rule_cache: dict[str, list[dict]] = {}
_rule_cache_at = 0.0


def invalidate_alert_cache() -> None:
    """Force the next tick to reload ACTIVE rules (called by the endpoints)."""
    global _rule_cache_at
    _rule_cache_at = 0.0


async def _active_rules_for(sessionmaker, symbol: str) -> list[dict]:
    global _rule_cache_at
    now = time.monotonic()
    if now - _rule_cache_at > RULE_CACHE_TTL_SECONDS:
        async with sessionmaker() as session:
            rows = (
                (
                    await session.execute(
                        select(AlertRule, Instrument.symbol)
                        .join(
                            Instrument,
                            AlertRule.instrument_id == Instrument.instrument_id,
                        )
                        .where(AlertRule.status == "ACTIVE")
                    )
                )
                .all()
            )
        cache: dict[str, list[dict]] = {}
        for rule, rule_symbol in rows:
            cache.setdefault(rule_symbol, []).append(
                {
                    "rule_id": rule.rule_id,
                    "user_id": rule.user_id,
                    "condition": rule.condition,
                    "threshold": rule.threshold,
                }
            )
        _rule_cache.clear()
        _rule_cache.update(cache)
        _rule_cache_at = now
    return _rule_cache.get(symbol, [])


def _condition_met(
    condition: str, price: Decimal, prev: Decimal | None, threshold: Decimal
) -> bool:
    if condition == "ABOVE":
        return price > threshold
    if condition == "BELOW":
        return price < threshold
    if condition == "CROSSES_ABOVE":
        return prev is not None and prev <= threshold and price > threshold
    if condition == "CROSSES_BELOW":
        return prev is not None and prev >= threshold and price < threshold
    return False


async def handle_tick(sessionmaker, tick: dict) -> int:
    """Evaluate ACTIVE rules for one market tick; returns how many triggered.

    Rules come from the in-memory cache (see above); the DB is touched only on
    a cache refresh or an actual trigger. On trigger: publish `notify`
    (category ALERT) + audit PRICE_ALERT_TRIGGERED + mark the rule TRIGGERED,
    all in one transaction. Importable on its own so tests can drive it with
    synthetic ticks.
    """
    symbol = tick.get("symbol")
    raw_price = tick.get("price")
    if symbol is None or raw_price is None:
        return 0
    price = Decimal(str(raw_price))
    prev = _last_prices.get(symbol)
    _last_prices[symbol] = price

    rules = await _active_rules_for(sessionmaker, symbol)
    fired = [
        rule
        for rule in rules
        if _condition_met(rule["condition"], price, prev, rule["threshold"])
    ]
    if not fired:
        return 0

    triggered = 0
    async with sessionmaker() as session:
        for entry in fired:
            rule = await session.get(AlertRule, entry["rule_id"])
            if rule is None or rule.status != "ACTIVE":
                continue  # changed since the cache snapshot; skip
            rule.status = "TRIGGERED"  # re-arm via DELETE + recreate
            await write_audit(
                session,
                actor_id=rule.user_id,
                event_type=PRICE_ALERT_TRIGGERED,
                resource_type="alert_rule",
                resource_id=rule.rule_id,
                payload={
                    "symbol": symbol,
                    "condition": rule.condition,
                    "threshold": str(rule.threshold),
                    "price": str(price),
                    "previous_price": str(prev) if prev is not None else None,
                },
                flush_only=True,
            )
            await write_outbox(
                session,
                "notify",
                {
                    "user_id": rule.user_id,
                    "category": "ALERT",
                    "title": f"Price alert: {symbol} {rule.condition} {rule.threshold}",
                    "body": (
                        f"{symbol} traded at {price}"
                        + (f" (previous {prev})" if prev is not None else "")
                        + f"; your {rule.condition} {rule.threshold} rule triggered."
                    ),
                },
            )
            triggered += 1
        await session.commit()

    # Evict fired rules so a stale cache cannot re-fire them.
    fired_ids = {entry["rule_id"] for entry in fired}
    remaining = [
        rule for rule in _rule_cache.get(symbol, []) if rule["rule_id"] not in fired_ids
    ]
    if remaining:
        _rule_cache[symbol] = remaining
    else:
        _rule_cache.pop(symbol, None)
    return triggered


async def alert_evaluator(bus, sessionmaker) -> None:
    """Consume `market.ticks`; isolate per-tick failures; idle on zero ticks."""
    subscription = await bus.subscribe("market.ticks")
    async for tick in subscription:
        try:
            await handle_tick(sessionmaker, tick)
        except Exception:  # keep the worker alive on a bad tick
            logger.exception("alert evaluator failed on tick: %r", tick)


def get_workers(settings):
    return [alert_evaluator]
