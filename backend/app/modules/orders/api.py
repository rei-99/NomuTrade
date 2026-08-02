"""Order ticket + trade blotter endpoints.

Audit event types emitted here: ORDER_SUBMITTED, ORDER_REJECTED,
ORDER_CANCELLED, ORDER_AMENDED (ORDER_FILLED, STOP_TRIGGERED and
ORDER_EXPIRED are emitted by the execution engine worker). Notifications
are published to the shared `notify` stream; a parallel module persists
them.

Design 24: tickets carry a time-in-force (DAY/GTC/IOC, default GTC) and
TRAILING_STOP tickets trail params; DAY orders get `expire_after` from the
simulation clock at acceptance.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.db import get_db
from app.core.errors import (
    BusinessRuleViolation,
    Forbidden,
    NotFound,
    StateConflict,
    ValidationError,
)
from app.core.events import write_outbox
from app.core.models import (
    Execution,
    Instrument,
    Order,
    OrderStatus,
    Portfolio,
    SettlementInstruction,
    TimeInForce,
)
from app.core.security import (
    SessionData,
    get_current_user,
    get_effective_permissions,
    require_permission,
)
from app.core.timeutil import as_utc, utcnow
from app.modules.marketdata.registry import get_sim_now
from app.modules.orders.validation import Rejection, validate_order

router = APIRouter(tags=["orders"])

ORDER_SUBMITTED = "ORDER_SUBMITTED"
ORDER_REJECTED = "ORDER_REJECTED"
ORDER_CANCELLED = "ORDER_CANCELLED"
ORDER_AMENDED = "ORDER_AMENDED"

PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Schemas / serializers
# ---------------------------------------------------------------------------


class OrderTicket(BaseModel):
    portfolio_id: str
    instrument: str  # symbol
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAILING_STOP"]
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None  # required for STOP / STOP_LIMIT (§A3)
    time_in_force: Literal["DAY", "GTC", "IOC"] = "GTC"  # design 24 §D-24.1
    trail_amount: Decimal | None = None  # TRAILING_STOP: exactly one of…
    trail_pct: Decimal | None = None    # …amount/pct (> 0), §D-24.2


class OrderAmendment(BaseModel):
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    trail_amount: Decimal | None = None
    trail_pct: Decimal | None = None


def _iso(dt) -> str:
    return as_utc(dt).isoformat()


def order_json(order: Order, symbol: str) -> dict:
    return {
        "order_id": order.order_id,
        "portfolio_id": order.portfolio_id,
        "instrument_symbol": symbol,
        "side": order.side,
        "order_type": order.order_type,
        "quantity": float(order.quantity),
        "limit_price": (
            float(order.limit_price) if order.limit_price is not None else None
        ),
        "stop_price": (
            float(order.stop_price) if order.stop_price is not None else None
        ),
        "time_in_force": order.time_in_force,
        "expire_after": (
            _iso(order.expire_after) if order.expire_after is not None else None
        ),
        "trail_amount": (
            float(order.trail_amount) if order.trail_amount is not None else None
        ),
        "trail_pct": (
            float(order.trail_pct) if order.trail_pct is not None else None
        ),
        "trail_reference": (
            float(order.trail_reference)
            if order.trail_reference is not None
            else None
        ),
        "status": order.status,
        "reject_reason": order.reject_reason,
        "created_by": order.created_by,
        "created_at": _iso(order.created_at),
        "updated_at": _iso(order.updated_at),
        "executions": [
            {
                "execution_id": e.execution_id,
                "price": float(e.price),
                "quantity": float(e.quantity),
                "executed_at": _iso(e.executed_at),
            }
            for e in sorted(order.executions, key=lambda e: e.executed_at)
        ],
    }


def _submit_response(order: Order) -> dict:
    return {
        "order_id": order.order_id,
        "status": order.status,
        "submitted_at": _iso(order.created_at),
    }


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


def _rejection_detail(rejection: Rejection) -> dict:
    """422 details payload: machine code + message + rule-specific extras
    (restricted-list reason, notional limit/actual)."""
    detail = {"code": rejection.code, "message": rejection.message}
    if rejection.details:
        detail.update(rejection.details)
    return detail


async def _notify(db: AsyncSession, user_id: str, title: str, body: str) -> None:
    await write_outbox(
        db,
        "notify",
        {"user_id": user_id, "category": "ORDER", "title": title, "body": body},
    )


def _day_expire_after() -> datetime:
    """End of the current *simulation* day (design 24 §D-24.1, D-10).

    DAY orders expire when a tick beyond this instant reaches the engine.
    Falls back to the wall clock when no replay has started (no sim clock).
    """
    clock = as_utc(get_sim_now() or utcnow())
    return clock.replace(hour=23, minute=59, second=59, microsecond=999999)


async def _visible_or_403(
    db: AsyncSession, user: SessionData, portfolio: Portfolio
) -> None:
    if portfolio.owner_id == user.user_id:
        return
    perms = await get_effective_permissions(db, user.user_id)
    if "PORTFOLIO_VIEW_ALL" not in perms:
        raise Forbidden("portfolio is not owned by the caller")


async def _symbol_map(db: AsyncSession, instrument_ids: set[str]) -> dict[str, str]:
    if not instrument_ids:
        return {}
    rows = (
        await db.execute(
            select(Instrument.instrument_id, Instrument.symbol).where(
                Instrument.instrument_id.in_(instrument_ids)
            )
        )
    ).all()
    return {iid: symbol for iid, symbol in rows}


# ---------------------------------------------------------------------------
# POST /orders
# ---------------------------------------------------------------------------


@router.post("/orders", status_code=201)
async def submit_order(
    body: OrderTicket,
    request: Request,
    response: Response,
    user: SessionData = Depends(require_permission("ORDER_SUBMIT")),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Header(..., description="Idempotency-Key"),
):
    # FR-ORD-001 E2: duplicate key returns the original order, no new row.
    existing = (
        await db.execute(
            select(Order).where(Order.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        response.status_code = 200
        return _submit_response(existing)

    portfolio = await db.get(Portfolio, body.portfolio_id)
    if portfolio is None:
        raise NotFound("portfolio not found")
    await _visible_or_403(db, user, portfolio)

    instrument = (
        await db.execute(
            select(Instrument).where(Instrument.symbol == body.instrument)
        )
    ).scalar_one_or_none()
    if instrument is None:
        raise NotFound(f"instrument not found: {body.instrument}")

    rejection = await validate_order(
        db,
        portfolio=portfolio,
        instrument=instrument,
        side=body.side,
        order_type=body.order_type,
        quantity=body.quantity,
        limit_price=body.limit_price,
        stop_price=body.stop_price,
        trail_amount=body.trail_amount,
        trail_pct=body.trail_pct,
        settings=request.app.state.settings,
    )
    if rejection is not None:
        order = Order(
            portfolio_id=portfolio.portfolio_id,
            instrument_id=instrument.instrument_id,
            side=body.side,
            order_type=body.order_type,
            quantity=body.quantity,
            limit_price=body.limit_price,
            stop_price=body.stop_price,
            time_in_force=body.time_in_force,
            trail_amount=body.trail_amount,
            trail_pct=body.trail_pct,
            status=OrderStatus.REJECTED,
            reject_reason=rejection.code,
            idempotency_key=idempotency_key,
            created_by=user.user_id,
        )
        db.add(order)
        await db.flush()
        await write_audit(
            db,
            actor_id=user.user_id,
            event_type=ORDER_REJECTED,
            resource_type="ORDER",
            resource_id=order.order_id,
            severity="WARN",
            payload={
                "reason": rejection.code,
                "symbol": instrument.symbol,
                "side": body.side,
                "quantity": str(body.quantity),
            },
        )
        await _notify(
            db,
            portfolio.owner_id,
            "Order rejected",
            f"{body.side} {body.quantity} {instrument.symbol}: {rejection.code}",
        )
        await db.commit()
        raise BusinessRuleViolation(
            rejection.message,
            details=[_rejection_detail(rejection)],
        )

    order = Order(
        portfolio_id=portfolio.portfolio_id,
        instrument_id=instrument.instrument_id,
        side=body.side,
        order_type=body.order_type,
        quantity=body.quantity,
        limit_price=body.limit_price,
        stop_price=body.stop_price,
        time_in_force=body.time_in_force,
        # DAY expires at the end of the simulation day (design 24 §D-24.1).
        expire_after=(
            _day_expire_after()
            if body.time_in_force == TimeInForce.DAY
            else None
        ),
        trail_amount=body.trail_amount,
        trail_pct=body.trail_pct,
        status=OrderStatus.ACCEPTED,
        idempotency_key=idempotency_key,
        created_by=user.user_id,
    )
    db.add(order)
    await db.flush()
    await write_outbox(db, "orders.accepted", {"order_id": order.order_id})
    await write_audit(
        db,
        actor_id=user.user_id,
        event_type=ORDER_SUBMITTED,
        resource_type="ORDER",
        resource_id=order.order_id,
        payload={
            "portfolio_id": portfolio.portfolio_id,
            "symbol": instrument.symbol,
            "side": body.side,
            "order_type": body.order_type,
            "quantity": str(body.quantity),
            "limit_price": str(body.limit_price) if body.limit_price else None,
            "stop_price": str(body.stop_price) if body.stop_price else None,
            "time_in_force": body.time_in_force,
            "trail_amount": (
                str(body.trail_amount) if body.trail_amount is not None else None
            ),
            "trail_pct": (
                str(body.trail_pct) if body.trail_pct is not None else None
            ),
        },
    )
    try:
        await db.commit()
    except IntegrityError:
        # Lost a race on the unique idempotency key: return the winner's row.
        await db.rollback()
        duplicate = (
            await db.execute(
                select(Order).where(Order.idempotency_key == idempotency_key)
            )
        ).scalar_one()
        response.status_code = 200
        return _submit_response(duplicate)
    return _submit_response(order)


# ---------------------------------------------------------------------------
# GET /orders, GET /orders/{order_id}
# ---------------------------------------------------------------------------


@router.get("/orders")
async def list_orders(
    portfolio_id: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    user: SessionData = Depends(require_permission("ORDER_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    if status is not None and status not in {s.value for s in OrderStatus}:
        raise ValidationError(f"invalid status: {status}")
    offset = _parse_cursor(cursor)
    perms = await get_effective_permissions(db, user.user_id)
    view_all = "PORTFOLIO_VIEW_ALL" in perms

    stmt = select(Order).join(Portfolio, Order.portfolio_id == Portfolio.portfolio_id)
    if not view_all:
        stmt = stmt.where(Portfolio.owner_id == user.user_id)
    if portfolio_id is not None:
        portfolio = await db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise NotFound("portfolio not found")
        await _visible_or_403(db, user, portfolio)
        stmt = stmt.where(Order.portfolio_id == portfolio_id)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    stmt = (
        stmt.order_by(Order.created_at.desc(), Order.order_id)
        .offset(offset)
        .limit(PAGE_SIZE + 1)
    )
    orders = list((await db.execute(stmt)).scalars().all())
    next_cursor = (
        str(offset + PAGE_SIZE) if len(orders) > PAGE_SIZE else None
    )
    orders = orders[:PAGE_SIZE]
    symbols = await _symbol_map(db, {o.instrument_id for o in orders})
    return {
        "items": [order_json(o, symbols.get(o.instrument_id, "")) for o in orders],
        "next_cursor": next_cursor,
    }


@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    user: SessionData = Depends(require_permission("ORDER_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(Order, order_id)
    if order is None:
        raise NotFound("order not found")
    portfolio = await db.get(Portfolio, order.portfolio_id)
    await _visible_or_403(db, user, portfolio)
    symbols = await _symbol_map(db, {order.instrument_id})
    return order_json(order, symbols.get(order.instrument_id, ""))


# ---------------------------------------------------------------------------
# POST /orders/{order_id}/cancel, PATCH /orders/{order_id}
# ---------------------------------------------------------------------------


async def _load_owned_order(
    db: AsyncSession, user: SessionData, order_id: str
) -> tuple[Order, Portfolio]:
    order = await db.get(Order, order_id)
    if order is None:
        raise NotFound("order not found")
    portfolio = await db.get(Portfolio, order.portfolio_id)
    if portfolio.owner_id != user.user_id:
        raise Forbidden("only the portfolio owner may modify the order")
    return order, portfolio


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    user: SessionData = Depends(require_permission("ORDER_CANCEL")),
    db: AsyncSession = Depends(get_db),
):
    order, portfolio = await _load_owned_order(db, user, order_id)
    if order.status not in (OrderStatus.OPEN, OrderStatus.ACCEPTED):
        raise StateConflict(
            f"order in status {order.status} cannot be cancelled",
            details=[{"code": "NOT_CANCELLABLE", "status": order.status}],
        )
    order.status = OrderStatus.CANCELLED
    order.updated_at = utcnow()
    await write_audit(
        db,
        actor_id=user.user_id,
        event_type=ORDER_CANCELLED,
        resource_type="ORDER",
        resource_id=order.order_id,
        payload={"previous_status": "OPEN_OR_ACCEPTED"},
    )
    await _notify(
        db, portfolio.owner_id, "Order cancelled", f"Order {order.order_id} cancelled"
    )
    await db.commit()
    symbols = await _symbol_map(db, {order.instrument_id})
    return order_json(order, symbols.get(order.instrument_id, ""))


@router.patch("/orders/{order_id}")
async def amend_order(
    order_id: str,
    body: OrderAmendment,
    request: Request,
    user: SessionData = Depends(require_permission("ORDER_CANCEL")),
    db: AsyncSession = Depends(get_db),
):
    if (
        body.quantity is None
        and body.limit_price is None
        and body.stop_price is None
        and body.trail_amount is None
        and body.trail_pct is None
    ):
        raise ValidationError(
            "nothing to amend: provide quantity, limit_price, stop_price "
            "and/or trail_amount/trail_pct"
        )
    order, portfolio = await _load_owned_order(db, user, order_id)
    if order.status != OrderStatus.OPEN:
        raise StateConflict(
            f"only OPEN orders can be amended (status: {order.status})",
            details=[{"code": "NOT_AMENDABLE", "status": order.status}],
        )
    new_quantity = body.quantity if body.quantity is not None else order.quantity
    new_limit = (
        body.limit_price if body.limit_price is not None else order.limit_price
    )
    new_stop = body.stop_price if body.stop_price is not None else order.stop_price
    # Trailing stop (design 24 §D-24.2): amending one trail param replaces
    # the trail and clears the other (exactly-one invariant); the water-mark
    # trail_reference is kept across amendments.
    new_trail_amount = order.trail_amount
    new_trail_pct = order.trail_pct
    if body.trail_amount is not None:
        new_trail_amount, new_trail_pct = body.trail_amount, None
    elif body.trail_pct is not None:
        new_trail_amount, new_trail_pct = None, body.trail_pct
    instrument = await db.get(Instrument, order.instrument_id)
    rejection = await validate_order(
        db,
        portfolio=portfolio,
        instrument=instrument,
        side=order.side,
        order_type=order.order_type,
        quantity=new_quantity,
        limit_price=new_limit,
        stop_price=new_stop,
        trail_amount=new_trail_amount,
        trail_pct=new_trail_pct,
        settings=request.app.state.settings,
    )
    if rejection is not None:
        # Consistent with submission: a failed amendment rejects the order.
        order.status = OrderStatus.REJECTED
        order.reject_reason = rejection.code
        order.updated_at = utcnow()
        await write_audit(
            db,
            actor_id=user.user_id,
            event_type=ORDER_REJECTED,
            resource_type="ORDER",
            resource_id=order.order_id,
            severity="WARN",
            payload={"reason": rejection.code, "via": "AMEND"},
        )
        await _notify(
            db,
            portfolio.owner_id,
            "Order rejected",
            f"Amendment of order {order.order_id} failed: {rejection.code}",
        )
        await db.commit()
        raise BusinessRuleViolation(
            rejection.message,
            details=[_rejection_detail(rejection)],
        )
    order.quantity = new_quantity
    order.limit_price = new_limit
    order.stop_price = new_stop
    order.trail_amount = new_trail_amount
    order.trail_pct = new_trail_pct
    order.updated_at = utcnow()
    await write_audit(
        db,
        actor_id=user.user_id,
        event_type=ORDER_AMENDED,
        resource_type="ORDER",
        resource_id=order.order_id,
        payload={
            "quantity": str(new_quantity),
            "limit_price": str(new_limit) if new_limit else None,
            "stop_price": str(new_stop) if new_stop else None,
            "trail_amount": (
                str(new_trail_amount) if new_trail_amount is not None else None
            ),
            "trail_pct": str(new_trail_pct) if new_trail_pct is not None else None,
        },
    )
    await db.commit()
    symbols = await _symbol_map(db, {order.instrument_id})
    return order_json(order, symbols.get(order.instrument_id, ""))


# ---------------------------------------------------------------------------
# GET /trades
# ---------------------------------------------------------------------------


@router.get("/trades")
async def list_trades(
    portfolio_id: str | None = None,
    cursor: str | None = None,
    user: SessionData = Depends(require_permission("TRADE_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    offset = _parse_cursor(cursor)
    perms = await get_effective_permissions(db, user.user_id)
    view_all = "PORTFOLIO_VIEW_ALL" in perms

    stmt = (
        select(Execution, Order, Portfolio, Instrument, SettlementInstruction)
        .join(Order, Execution.order_id == Order.order_id)
        .join(Portfolio, Order.portfolio_id == Portfolio.portfolio_id)
        .join(Instrument, Order.instrument_id == Instrument.instrument_id)
        # One LEFT OUTER JOIN (execution_id is unique there) so each trade
        # carries its settlement lifecycle state — NULL until the STP worker
        # books the instruction.
        .outerjoin(
            SettlementInstruction,
            SettlementInstruction.execution_id == Execution.execution_id,
        )
    )
    if not view_all:
        stmt = stmt.where(Portfolio.owner_id == user.user_id)
    if portfolio_id is not None:
        portfolio = await db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise NotFound("portfolio not found")
        await _visible_or_403(db, user, portfolio)
        stmt = stmt.where(Order.portfolio_id == portfolio_id)
    stmt = (
        stmt.order_by(Execution.executed_at.desc(), Execution.execution_id)
        .offset(offset)
        .limit(PAGE_SIZE + 1)
    )
    rows = (await db.execute(stmt)).all()
    next_cursor = str(offset + PAGE_SIZE) if len(rows) > PAGE_SIZE else None
    items = [
        {
            "execution_id": ex.execution_id,
            "order_id": ex.order_id,
            "portfolio_id": order.portfolio_id,
            "instrument_symbol": instrument.symbol,
            "side": order.side,
            "price": float(ex.price),
            "quantity": float(ex.quantity),
            "executed_at": _iso(ex.executed_at),
            "portfolio_type": portfolio.type,
            "settlement_state": (
                settlement.lifecycle_state if settlement is not None else None
            ),
        }
        for ex, order, portfolio, instrument, settlement in rows[:PAGE_SIZE]
    ]
    return {"items": items, "next_cursor": next_cursor}
