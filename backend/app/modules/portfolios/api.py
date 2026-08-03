"""Portfolio read endpoints: list, positions, valuation, transactions, performance."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit as audit_log
from app.core.db import get_db
from app.core.errors import Forbidden, NotFound, ValidationError
from app.core.models import (
    Execution,
    Instrument,
    Order,
    Portfolio,
    PortfolioType,
    ValuationSnapshot,
)
from app.core.security import (
    SessionData,
    get_current_user,
    get_effective_permissions,
    require_permission,
)
from app.core.timeutil import as_utc, utcnow
from app.modules.orders.validation import trade_value
from app.modules.portfolios.valuation import (
    _daily_total_values,
    annualized_volatility_pct,
    bond_book_metrics,
    compute_realized,
    compute_total_value,
    expected_shortfall_95_1d_pct,
    max_drawdown_pct,
    previous_close_map,
    sharpe_ratio,
    value_positions,
    var_95_1d_pct,
)

router = APIRouter(tags=["portfolios"])

PAGE_SIZE = 50

TIMEFRAMES: dict[str, int | None] = {
    "1D": 1,
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "1Y": 365,
    "MAX": None,
}


def _iso(dt) -> str:
    return as_utc(dt).isoformat()


def _parse_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except (TypeError, ValueError):
        raise ValidationError("invalid cursor")
    if offset < 0:
        raise ValidationError("invalid cursor")
    return offset


def _parse_datetime(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"invalid {name} datetime: {value}")
    return as_utc(parsed)


async def _require_portfolio_access(
    db: AsyncSession, user: SessionData, portfolio_id: str
) -> Portfolio:
    """Portfolio-scoped read access: PORTFOLIO_VIEW on own portfolios,
    PORTFOLIO_VIEW_ALL for any portfolio. Anything else -> 403."""
    portfolio = await db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise NotFound("portfolio not found")
    perms = await get_effective_permissions(db, user.user_id)
    if not (perms & {"PORTFOLIO_VIEW", "PORTFOLIO_VIEW_ALL"}):
        raise Forbidden("missing required permission: PORTFOLIO_VIEW")
    if portfolio.owner_id != user.user_id and "PORTFOLIO_VIEW_ALL" not in perms:
        raise Forbidden("portfolio is not owned by the caller")
    return portfolio


@router.get("/portfolios")
async def list_portfolios(
    user: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    perms = await get_effective_permissions(db, user.user_id)
    stmt = select(Portfolio).order_by(Portfolio.name)
    if "PORTFOLIO_VIEW_ALL" not in perms:
        stmt = stmt.where(Portfolio.owner_id == user.user_id)
    portfolios = (await db.execute(stmt)).scalars().all()
    items = []
    for portfolio in portfolios:
        total_value = await compute_total_value(db, portfolio)
        items.append(
            {
                "portfolio_id": portfolio.portfolio_id,
                "name": portfolio.name,
                "type": portfolio.type,
                "owner_id": portfolio.owner_id,
                "cash_balance": float(portfolio.cash_balance),
                "total_value": float(total_value),
            }
        )
    return {"items": items, "next_cursor": None}


# ---------------------------------------------------------------------------
# POST /portfolios — open a new trading book
# ---------------------------------------------------------------------------

PORTFOLIO_CREATED = "PORTFOLIO_CREATED"
DEFAULT_BOOK_CASH = Decimal("1000000")
MAX_BOOK_CASH = Decimal("1000000000000")


class PortfolioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    initial_cash: Decimal | None = None


@router.post("/portfolios", status_code=201)
async def create_portfolio(
    body: PortfolioCreateRequest,
    response: Response,
    user: SessionData = Depends(require_permission("ORDER_SUBMIT")),
    db: AsyncSession = Depends(get_db),
):
    """Open a new HOUSE trading book owned by the caller.

    Any role that can trade (ORDER_SUBMIT) may open a book to trade from —
    reuses an existing permission so no seed change is needed on live DBs
    (the once-only-seed pitfall). PAPER accounts stay with the paper module;
    CLIENT books remain seeded. Idempotent by (owner, name): a repeat with an
    existing name returns 200 and the existing book instead of duplicating.
    """
    name = body.name.strip()
    if not name:
        raise ValidationError("name must not be blank")
    initial = body.initial_cash if body.initial_cash is not None else DEFAULT_BOOK_CASH
    if initial <= 0:
        raise ValidationError("initial_cash must be positive")
    if initial > MAX_BOOK_CASH:
        raise ValidationError(f"initial_cash must not exceed {MAX_BOOK_CASH}")

    existing = (
        (
            await db.execute(
                select(Portfolio).where(
                    Portfolio.owner_id == user.user_id,
                    Portfolio.name == name,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        response.status_code = 200
        return {
            "portfolio_id": existing.portfolio_id,
            "name": existing.name,
            "type": existing.type,
            "owner_id": existing.owner_id,
            "cash_balance": float(existing.cash_balance),
            "total_value": float(await compute_total_value(db, existing)),
        }

    portfolio = Portfolio(
        name=name,
        type=PortfolioType.HOUSE.value,
        owner_id=user.user_id,
        cash_balance=initial,
    )
    db.add(portfolio)
    await db.flush()
    await audit_log.write_audit(
        db,
        actor_id=user.user_id,
        event_type=PORTFOLIO_CREATED,
        resource_type="portfolio",
        resource_id=portfolio.portfolio_id,
        payload={"name": name, "type": portfolio.type, "initial_cash": str(initial)},
        flush_only=True,
    )
    await db.commit()
    return {
        "portfolio_id": portfolio.portfolio_id,
        "name": portfolio.name,
        "type": portfolio.type,
        "owner_id": portfolio.owner_id,
        "cash_balance": float(portfolio.cash_balance),
        "total_value": float(portfolio.cash_balance),
    }


@router.get("/portfolios/{portfolio_id}/positions")
async def get_positions(
    portfolio_id: str,
    user: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _require_portfolio_access(db, user, portfolio_id)
    valuations = await value_positions(db, portfolio.portfolio_id)
    items = [
        {
            "instrument_symbol": v.instrument.symbol,
            "name": v.instrument.name,
            "asset_class": v.instrument.asset_class,
            "quantity": float(v.position.quantity),
            "avg_cost": float(v.position.avg_cost),
            "latest_price": (
                float(v.latest_price) if v.latest_price is not None else None
            ),
            "market_value": (
                float(v.market_value) if v.market_value is not None else None
            ),
            "unrealized_pnl": (
                float(v.unrealized_pnl) if v.unrealized_pnl is not None else None
            ),
            # Per-position day change (design 21 §A5): sim-day open from the
            # price registry; nulls when no snapshot exists.
            "prev_day_open": (
                float(v.day_open) if v.day_open is not None else None
            ),
            "day_change": (
                float(v.day_change) if v.day_change is not None else None
            ),
            "day_change_pct": (
                float(v.day_change_pct) if v.day_change_pct is not None else None
            ),
            "stale_price": v.stale,
        }
        for v in valuations
    ]
    total_market = sum(
        (v.market_value for v in valuations if v.market_value is not None),
        Decimal("0"),
    )
    total_unrealized = sum(
        (v.unrealized_pnl for v in valuations if v.unrealized_pnl is not None),
        Decimal("0"),
    )
    return {
        "portfolio_id": portfolio.portfolio_id,
        "as_of": utcnow().isoformat(),
        "items": items,
        "totals": {
            "market_value": float(total_market),
            "unrealized_pnl": float(total_unrealized),
        },
    }


@router.get("/portfolios/{portfolio_id}/valuation")
async def get_valuation(
    portfolio_id: str,
    user: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _require_portfolio_access(db, user, portfolio_id)
    now = utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    valuations = await value_positions(db, portfolio.portfolio_id)
    market_value = sum(
        (v.market_value for v in valuations if v.market_value is not None),
        Decimal("0"),
    )
    unrealized = sum(
        (v.unrealized_pnl for v in valuations if v.unrealized_pnl is not None),
        Decimal("0"),
    )
    realized = await compute_realized(db, portfolio.portfolio_id)
    prev_closes = await previous_close_map(
        db, [v.instrument.instrument_id for v in valuations], today
    )
    day_change = Decimal("0")
    for v in valuations:
        prev = prev_closes.get(v.instrument.instrument_id)
        if prev is not None and v.latest_price is not None:
            # Bond-aware (§A2): face × (price - prev_close) / 100 for bonds.
            day_change += trade_value(
                v.instrument, v.position.quantity, v.latest_price - prev
            )

    # KPIs (FR-PFM-003). allocation/top/concentration percentages are relative
    # to the total market value of holdings (cash excluded).
    by_asset_class: dict[str, Decimal] = {}
    for v in valuations:
        if v.market_value is None:
            continue
        by_asset_class[v.instrument.asset_class] = (
            by_asset_class.get(v.instrument.asset_class, Decimal("0")) + v.market_value
        )
    allocation = [
        {
            "asset_class": asset_class,
            "value": float(value),
            "pct": float(value / market_value * 100) if market_value else 0.0,
        }
        for asset_class, value in sorted(
            by_asset_class.items(), key=lambda kv: kv[1], reverse=True
        )
    ]
    holdings = sorted(
        (v for v in valuations if v.market_value is not None),
        key=lambda v: v.market_value,
        reverse=True,
    )
    top_holdings = [
        {
            "instrument_symbol": v.instrument.symbol,
            "market_value": float(v.market_value),
            "pct": float(v.market_value / market_value * 100) if market_value else 0.0,
        }
        for v in holdings[:5]
    ]
    concentration_pct = top_holdings[0]["pct"] if top_holdings else 0.0
    # One history fetch shared by all series-based KPIs (volatility, VaR, ES,
    # Sharpe, drawdown) — each used to rescan the snapshots independently.
    daily_values = await _daily_total_values(db, portfolio.portfolio_id)
    volatility = await annualized_volatility_pct(
        db, portfolio.portfolio_id, values=daily_values
    )
    var_95 = await var_95_1d_pct(db, portfolio.portfolio_id, values=daily_values)
    es_95 = await expected_shortfall_95_1d_pct(
        db, portfolio.portfolio_id, values=daily_values
    )
    sharpe = await sharpe_ratio(db, portfolio.portfolio_id, values=daily_values)
    max_dd = await max_drawdown_pct(db, portfolio.portfolio_id, values=daily_values)
    bond_metrics = bond_book_metrics(valuations)

    return {
        "portfolio_id": portfolio.portfolio_id,
        "ts": now.isoformat(),
        "cash": float(portfolio.cash_balance),
        "market_value": float(market_value),
        "total_value": float(portfolio.cash_balance + market_value),
        "realized_pnl": float(realized),
        "unrealized_pnl": float(unrealized),
        "day_change": float(day_change),
        "kpis": {
            "allocation": allocation,
            "top_holdings": top_holdings,
            "concentration_pct": concentration_pct,
            "volatility_annualized_pct": volatility,
            "var_95_1d_pct": var_95,
            "es_95_1d_pct": es_95,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": max_dd,
            **bond_metrics,
        },
    }


@router.get("/portfolios/{portfolio_id}/transactions")
async def get_transactions(
    portfolio_id: str,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    symbol: str | None = None,
    side: str | None = None,
    cursor: str | None = None,
    user: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _require_portfolio_access(db, user, portfolio_id)
    offset = _parse_cursor(cursor)
    from_dt = _parse_datetime(from_, "from")
    to_dt = _parse_datetime(to, "to")
    if side is not None and side not in ("BUY", "SELL"):
        raise ValidationError(f"invalid side: {side}")

    # Cash movements are derived from executions (no separate cash table):
    # `amount` is the signed cash effect of the trade (BUY negative, SELL
    # positive). kind is always EXECUTION for the MVP.
    stmt = (
        select(Execution, Order, Instrument)
        .join(Order, Execution.order_id == Order.order_id)
        .join(Instrument, Order.instrument_id == Instrument.instrument_id)
        .where(Order.portfolio_id == portfolio.portfolio_id)
    )
    if from_dt is not None:
        stmt = stmt.where(Execution.executed_at >= from_dt)
    if to_dt is not None:
        stmt = stmt.where(Execution.executed_at <= to_dt)
    if symbol is not None:
        stmt = stmt.where(Instrument.symbol == symbol)
    if side is not None:
        stmt = stmt.where(Order.side == side)
    stmt = (
        stmt.order_by(Execution.executed_at.desc(), Execution.execution_id)
        .offset(offset)
        .limit(PAGE_SIZE + 1)
    )
    rows = (await db.execute(stmt)).all()
    next_cursor = str(offset + PAGE_SIZE) if len(rows) > PAGE_SIZE else None
    items = []
    for execution, order, instrument in rows[:PAGE_SIZE]:
        # Bond-aware cash effect (§A2): face × price / 100 for bonds.
        gross = trade_value(instrument, execution.quantity, execution.price)
        amount = -gross if order.side == "BUY" else gross
        items.append(
            {
                "ts": _iso(execution.executed_at),
                "kind": "EXECUTION",
                "instrument_symbol": instrument.symbol,
                "side": order.side,
                "quantity": float(execution.quantity),
                "price": float(execution.price),
                "amount": float(amount),
                "ref_id": execution.execution_id,
            }
        )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/portfolios/{portfolio_id}/performance")
async def get_performance(
    portfolio_id: str,
    timeframe: str = "MAX",
    user: SessionData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _require_portfolio_access(db, user, portfolio_id)
    timeframe = timeframe.upper()
    if timeframe not in TIMEFRAMES:
        raise ValidationError(
            f"unsupported timeframe: {timeframe}",
            details=[{"code": "INVALID_TIMEFRAME", "timeframe": timeframe}],
        )
    days = TIMEFRAMES[timeframe]
    stmt = select(ValuationSnapshot).where(
        ValuationSnapshot.portfolio_id == portfolio.portfolio_id
    )
    if days is not None:
        cutoff = utcnow() - timedelta(days=days)
        stmt = stmt.where(ValuationSnapshot.ts >= cutoff)
    rows = (
        (await db.execute(stmt.order_by(ValuationSnapshot.ts))).scalars().all()
    )
    series = [
        {"ts": _iso(row.ts), "total_value": float(row.market_value + row.cash)}
        for row in rows
    ]
    return {
        "portfolio_id": portfolio.portfolio_id,
        "timeframe": timeframe,
        "series": series,
    }
