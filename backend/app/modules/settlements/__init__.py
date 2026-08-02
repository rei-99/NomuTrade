"""Settlement visibility + STP exception remediation (FR-ORD-005 E1).

Design: docs/design/02-order-execution-stp.md. Router-only module (no
workers) exposing:

- GET /settlements — the settlement-instruction blotter. Gated by
  TRADE_VIEW and scoped like portfolios: callers holding any of
  PORTFOLIO_VIEW_ALL / STP_EXCEPTION_HANDLE / INTEGRATION_MONITOR see every
  portfolio's instructions; anyone else only their own (owner_id via
  Execution -> Order -> Portfolio).
- POST /settlements/exceptions/{execution_id}/retry — ops remediation for a
  dropped `trading.executions` event. The STP worker audits STP_EXCEPTION
  and drops events it cannot process; this endpoint (the first consumer of
  the STP_EXCEPTION_HANDLE permission) reconstructs the event payload from
  the DB and re-queues it through the transactional outbox. The worker's
  idempotency check (skip when a SettlementInstruction exists) makes a
  duplicate delivery harmless.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.db import get_db
from app.core.errors import Forbidden, NotFound, StateConflict, ValidationError
from app.core.events import write_outbox
from app.core.models import (
    Execution,
    Instrument,
    LifecycleState,
    Order,
    Portfolio,
    SettlementInstruction,
)
from app.core.security import (
    SessionData,
    get_effective_permissions,
    require_permission,
)
from app.core.timeutil import as_utc
from app.modules.orders.validation import trade_value

router = APIRouter(tags=["settlements"])

STP_EXCEPTION_RETRY = "STP_EXCEPTION_RETRY"

PAGE_SIZE = 50

# Any of these grants cross-portfolio visibility (ops/risk/sysadmin roles).
_VIEW_ALL_PERMS = {"PORTFOLIO_VIEW_ALL", "STP_EXCEPTION_HANDLE", "INTEGRATION_MONITOR"}


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


# ---------------------------------------------------------------------------
# GET /settlements
# ---------------------------------------------------------------------------


@router.get("/settlements")
async def list_settlements(
    portfolio_id: str | None = None,
    lifecycle_state: str | None = None,
    cursor: str | None = None,
    user: SessionData = Depends(require_permission("TRADE_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    if lifecycle_state is not None and lifecycle_state not in {
        s.value for s in LifecycleState
    }:
        raise ValidationError(f"invalid lifecycle_state: {lifecycle_state}")
    offset = _parse_cursor(cursor)
    perms = await get_effective_permissions(db, user.user_id)
    view_all = bool(perms & _VIEW_ALL_PERMS)

    stmt = (
        select(SettlementInstruction, Execution, Order, Portfolio, Instrument)
        .join(
            Execution,
            SettlementInstruction.execution_id == Execution.execution_id,
        )
        .join(Order, Execution.order_id == Order.order_id)
        .join(Portfolio, Order.portfolio_id == Portfolio.portfolio_id)
        .join(Instrument, Order.instrument_id == Instrument.instrument_id)
    )
    if not view_all:
        stmt = stmt.where(Portfolio.owner_id == user.user_id)
    if portfolio_id is not None:
        portfolio = await db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise NotFound("portfolio not found")
        if not view_all and portfolio.owner_id != user.user_id:
            raise Forbidden("portfolio is not owned by the caller")
        stmt = stmt.where(Order.portfolio_id == portfolio_id)
    if lifecycle_state is not None:
        stmt = stmt.where(SettlementInstruction.lifecycle_state == lifecycle_state)
    stmt = (
        stmt.order_by(
            SettlementInstruction.created_at.desc(),
            SettlementInstruction.settlement_id,
        )
        .offset(offset)
        .limit(PAGE_SIZE + 1)
    )
    rows = (await db.execute(stmt)).all()
    next_cursor = str(offset + PAGE_SIZE) if len(rows) > PAGE_SIZE else None
    items = [
        {
            "settlement_id": s.settlement_id,
            "execution_id": s.execution_id,
            "portfolio_id": portfolio.portfolio_id,
            "portfolio_name": portfolio.name,
            "instrument_symbol": instrument.symbol,
            "side": order.side,
            "quantity": float(ex.quantity),
            "price": float(ex.price),
            # Bond-aware cash value (bonds: face x price / 100, design 21 A2).
            "value": float(trade_value(instrument, ex.quantity, ex.price)),
            "lifecycle_state": s.lifecycle_state,
            "created_at": _iso(s.created_at),
            "settled_at": (
                _iso(s.settled_at) if s.settled_at is not None else None
            ),
        }
        for s, ex, order, portfolio, instrument in rows[:PAGE_SIZE]
    ]
    return {"items": items, "next_cursor": next_cursor}


# ---------------------------------------------------------------------------
# POST /settlements/exceptions/{execution_id}/retry
# ---------------------------------------------------------------------------


@router.post("/settlements/exceptions/{execution_id}/retry")
async def retry_stp_exception(
    execution_id: str,
    user: SessionData = Depends(require_permission("STP_EXCEPTION_HANDLE")),
    db: AsyncSession = Depends(get_db),
):
    """FR-ORD-005 E1 remediation: re-publish a dropped execution event.

    The STP worker permanently drops a `trading.executions` event whose
    processing raised (recording STP_EXCEPTION + owner notification), so the
    exception path had no remediation until now. Re-publishing the same event
    lets the worker complete the booking; its idempotency check no-ops any
    duplicate. 404 when the execution is unknown, 409 when a
    SettlementInstruction already exists (nothing to remediate).
    """
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise NotFound("execution not found")
    existing = (
        await db.execute(
            select(SettlementInstruction).where(
                SettlementInstruction.execution_id == execution_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise StateConflict(
            "settlement instruction already exists for this execution",
            details=[
                {
                    "code": "ALREADY_PROCESSED",
                    "settlement_id": existing.settlement_id,
                }
            ],
        )

    order = await db.get(Order, execution.order_id)
    portfolio = await db.get(Portfolio, order.portfolio_id)
    instrument = await db.get(Instrument, order.instrument_id)
    # Same shape + JSON-safe types as the original producer
    # (orders.workers._fill_order); price/quantity come from the persisted
    # execution (full fills: identical to the order's), executed_at is
    # re-rendered as an ISO string.
    event = {
        "execution_id": execution.execution_id,
        "order_id": order.order_id,
        "portfolio_id": order.portfolio_id,
        "portfolio_type": portfolio.type,
        "instrument_id": order.instrument_id,
        "symbol": instrument.symbol,
        "side": order.side,
        "price": float(execution.price),
        "quantity": float(execution.quantity),
        "executed_at": _iso(execution.executed_at),
    }
    await write_outbox(db, "trading.executions", event)
    await write_audit(
        db,
        actor_id=user.user_id,
        event_type=STP_EXCEPTION_RETRY,
        resource_type="EXECUTION",
        resource_id=execution_id,
        severity="WARN",
        payload={
            "order_id": order.order_id,
            "portfolio_id": order.portfolio_id,
            "symbol": instrument.symbol,
        },
        flush_only=False,  # ops-critical: persist immediately
    )
    # Same notify idiom as the exception path in orders.workers.
    await write_outbox(
        db,
        "notify",
        {
            "user_id": portfolio.owner_id,
            "category": "SYSTEM",
            "title": "STP exception retried",
            "body": f"Settlement for execution {execution_id} was re-queued "
            f"by operations.",
        },
    )
    await db.commit()
    return {"execution_id": execution_id, "republished": True}
