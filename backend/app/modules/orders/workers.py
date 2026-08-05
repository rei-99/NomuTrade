"""STP pipeline workers (docs/design/02-order-execution-stp.md).

- execution_engine: consumes `orders.accepted` + `market.ticks`; MARKET fills
  immediately at the latest price, LIMIT rests OPEN until crossed. STOP /
  STOP_LIMIT rest OPEN until a tick crosses the stop price (BUY >= stop,
  SELL <= stop, design 21 §A3): STOP then fills as MARKET at the tick,
  STOP_LIMIT converts in place to a resting LIMIT (STOP_TRIGGERED audited).
  Design 24: TRAILING_STOP rests OPEN, rolls its persisted trail_reference
  water-mark toward each new extreme and fills as MARKET when the trail is
  crossed (STOP_TRIGGERED audited); DAY orders are cancelled with an
  ORDER_EXPIRED audit when a tick beyond `expire_after` arrives; IOC orders
  that cannot execute immediately are CANCELLED (reason IOC_UNFILLED)
  instead of resting. Full fills only — partial fills are a documented MVP
  non-goal (design A1).
- stp_worker: consumes `trading.executions`; in ONE transaction upserts the
  Position, adjusts cash (bond-aware via `trade_value`, §A2), inserts the
  SettlementInstruction and writes the `stp.lifecycle` outbox event.
  Idempotent per execution.
- settlement_sweeper: advances EXECUTED -> AFFIRMED -> SETTLED after the
  configured simulated delay, publishing `stp.lifecycle` per transition.

All DB units-of-work run through `_shielded`: cancelling a task in the middle
of an aiosqlite call can wedge the connection (its worker thread dies on an
InvalidStateError), after which session.close()/engine.dispose() hang and app
shutdown exceeds the lifespan timeout. Shielding lets the unit finish cleanly
while the worker still exits promptly on CancelledError.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.core.audit import write_audit
from app.core.events import write_outbox
from app.core.models import (
    Execution,
    Instrument,
    LifecycleState,
    Order,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    SettlementInstruction,
    TimeInForce,
)
from app.core.timeutil import as_utc, utcnow
from app.modules.marketdata.registry import business_now, get_snapshot
from app.modules.orders.validation import trade_value

logger = logging.getLogger(__name__)

ORDER_FILLED = "ORDER_FILLED"
ORDER_CANCELLED = "ORDER_CANCELLED"
ORDER_EXPIRED = "ORDER_EXPIRED"
STOP_TRIGGERED = "STOP_TRIGGERED"
STP_EXCEPTION = "STP_EXCEPTION"
IOC_UNFILLED = "IOC_UNFILLED"

_CLOSED_STATUSES = (
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
)

# Order types that rest in the working book as OPEN until crossed/triggered.
_RESTING_TYPES = (
    OrderType.LIMIT,
    OrderType.STOP,
    OrderType.STOP_LIMIT,
    OrderType.TRAILING_STOP,
)


async def _shielded(coro):
    """Run one DB unit-of-work shielded from task cancellation."""
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Let the unit finish so the connection returns to the pool cleanly;
        # only then propagate the cancellation.
        try:
            await task
        except Exception:
            pass
        raise


def _marketable(order: Order, price: Decimal) -> bool:
    if order.order_type == OrderType.MARKET:
        return True
    if order.limit_price is None:
        return False
    if order.side == "BUY":
        return price <= order.limit_price
    return price >= order.limit_price


def _stop_triggered(order: Order, price: Decimal) -> bool:
    """STOP/STOP_LIMIT trigger check (design 21 §A3): BUY triggers when the
    tick is at/above the stop price, SELL when it is at/below it."""
    if order.stop_price is None:
        return False
    if order.side == "BUY":
        return price >= order.stop_price
    return price <= order.stop_price


# ---------------------------------------------------------------------------
# Trailing stop (design 24 §D-24.2)
# ---------------------------------------------------------------------------


def _update_trail_reference(order: Order, price: Decimal) -> bool:
    """Roll the trailing water-mark toward the new extreme; True if it moved.

    SELL trails the highest price seen since acceptance, BUY the lowest.
    The reference is initialized from the first tick seen after acceptance.
    """
    reference = order.trail_reference
    if reference is None:
        order.trail_reference = price
        return True
    if order.side == "SELL" and price > reference:
        order.trail_reference = price
        return True
    if order.side == "BUY" and price < reference:
        order.trail_reference = price
        return True
    return False


def _trail_trigger_price(order: Order) -> Decimal | None:
    """Trigger price implied by the current water-mark and trail params.

    SELL: reference − trail_amount, or reference × (1 − trail_pct/100).
    BUY mirror: reference + amount, or reference × (1 + pct/100).
    (trail_pct is percentage points — 5 means 5 %.)
    """
    reference = order.trail_reference
    if reference is None:
        return None
    if order.trail_amount is not None:
        if order.side == "SELL":
            return reference - order.trail_amount
        return reference + order.trail_amount
    if order.trail_pct is not None:
        hundred = Decimal("100")
        if order.side == "SELL":
            return reference * (hundred - order.trail_pct) / hundred
        return reference * (hundred + order.trail_pct) / hundred
    return None


def _trail_triggered(order: Order, price: Decimal) -> bool:
    """TRAILING_STOP trigger check: SELL fires at/below the trail trigger,
    BUY at/above it. Called after the reference roll, so a tick that sets a
    new extreme cannot trigger on itself."""
    trigger = _trail_trigger_price(order)
    if trigger is None:
        return False
    if order.side == "SELL":
        return price <= trigger
    return price >= trigger


async def _notify_owner(session, order: Order, title: str, body: str) -> None:
    portfolio = await session.get(Portfolio, order.portfolio_id)
    await write_outbox(
        session,
        "notify",
        {
            "user_id": portfolio.owner_id,
            "category": "ORDER",
            "title": title,
            "body": body,
        },
    )


async def _audit_trailing_trigger(
    session, order: Order, tick_price: Decimal
) -> None:
    """Triggered TRAILING_STOP (§D-24.2): STOP_TRIGGERED audit carrying the
    trail params + water-mark, plus an owner notify. The order then fills as
    MARKET at the tick (order_type stays TRAILING_STOP)."""
    instrument = await session.get(Instrument, order.instrument_id)
    await write_audit(
        session,
        actor_id=None,  # system actor: the matching engine
        event_type=STOP_TRIGGERED,
        resource_type="ORDER",
        resource_id=order.order_id,
        payload={
            "symbol": instrument.symbol,
            "side": order.side,
            "trail_amount": (
                str(order.trail_amount) if order.trail_amount is not None else None
            ),
            "trail_pct": (
                str(order.trail_pct) if order.trail_pct is not None else None
            ),
            "trail_reference": str(order.trail_reference),
            "tick_price": str(tick_price),
        },
    )
    trail = (
        f"amount {order.trail_amount}"
        if order.trail_amount is not None
        else f"{order.trail_pct}%"
    )
    await _notify_owner(
        session,
        order,
        "Trailing stop triggered",
        f"{order.side} {order.quantity} {instrument.symbol}: trailing stop "
        f"({trail}, reference {order.trail_reference}) triggered at "
        f"{tick_price}; filling as MARKET.",
    )


# ---------------------------------------------------------------------------
# Time-in-force (design 24 §D-24.1)
# ---------------------------------------------------------------------------


async def _cancel_ioc(session, order: Order, tick_price: Decimal) -> None:
    """IOC order that cannot execute immediately: CANCELLED with reason
    IOC_UNFILLED + audit + notify — it never rests in the book."""
    instrument = await session.get(Instrument, order.instrument_id)
    order.status = OrderStatus.CANCELLED
    order.reject_reason = IOC_UNFILLED
    order.updated_at = business_now()
    await write_audit(
        session,
        actor_id=None,  # system actor: the matching engine
        event_type=ORDER_CANCELLED,
        resource_type="ORDER",
        resource_id=order.order_id,
        payload={
            "reason": IOC_UNFILLED,
            "symbol": instrument.symbol,
            "side": order.side,
            "order_type": order.order_type,
            "tick_price": str(tick_price),
        },
    )
    await _notify_owner(
        session,
        order,
        "Order cancelled (IOC)",
        f"{order.side} {order.quantity} {instrument.symbol}: immediate-or-"
        f"cancel order could not execute at {tick_price}; cancelled.",
    )


async def _expire_day_order(session, order: Order, tick_ts) -> None:
    """DAY order whose simulation day has ended (§D-24.1): CANCELLED with an
    ORDER_EXPIRED audit + notify when a tick beyond expire_after arrives."""
    instrument = await session.get(Instrument, order.instrument_id)
    order.status = OrderStatus.CANCELLED
    order.updated_at = business_now()
    await write_audit(
        session,
        actor_id=None,  # system actor: the matching engine
        event_type=ORDER_EXPIRED,
        resource_type="ORDER",
        resource_id=order.order_id,
        payload={
            "symbol": instrument.symbol,
            "side": order.side,
            "expire_after": as_utc(order.expire_after).isoformat(),
            "tick_ts": as_utc(tick_ts).isoformat(),
        },
    )
    await _notify_owner(
        session,
        order,
        "Order expired",
        f"DAY {order.side} {order.quantity} {instrument.symbol} expired at "
        f"the end of its simulation day.",
    )


async def _convert_stop_limit(session, order: Order, tick_price: Decimal) -> None:
    """Triggered STOP_LIMIT (§A3): convert in place to a LIMIT order at
    limit_price (stop_price stays on the record); audit + notify the owner."""
    instrument = await session.get(Instrument, order.instrument_id)
    portfolio = await session.get(Portfolio, order.portfolio_id)
    order.order_type = OrderType.LIMIT
    order.updated_at = business_now()
    await write_audit(
        session,
        actor_id=None,  # system actor: the matching engine
        event_type=STOP_TRIGGERED,
        resource_type="ORDER",
        resource_id=order.order_id,
        payload={
            "symbol": instrument.symbol,
            "side": order.side,
            "stop_price": str(order.stop_price),
            "limit_price": str(order.limit_price) if order.limit_price else None,
            "tick_price": str(tick_price),
        },
    )
    await write_outbox(
        session,
        "notify",
        {
            "user_id": portfolio.owner_id,
            "category": "ORDER",
            "title": "Stop triggered",
            "body": f"{order.side} {order.quantity} {instrument.symbol}: stop "
            f"{order.stop_price} triggered at {tick_price}; now resting as "
            f"LIMIT {order.limit_price}.",
        },
    )


async def _rest_or_ioc(session, order: Order, tick_price: Decimal) -> str:
    """An order that cannot execute now rests ("working") — unless its TIF is
    IOC, which cancels instead of resting (design 24 §D-24.1)."""
    if order.time_in_force == TimeInForce.IOC:
        await _cancel_ioc(session, order, tick_price)
        await session.commit()
        return "closed"
    return "working"


async def _fill_order(sessionmaker, order_id: str) -> str:
    """Attempt to fill one order in a single transaction.

    Returns "filled" | "working" | "closed". Idempotent: an order that is
    already FILLED/CANCELLED/REJECTED (e.g. redelivered event, cancel/fill
    race) is skipped and reported "closed".
    """
    async with sessionmaker() as session:
        order = await session.get(Order, order_id)
        if order is None or order.status in _CLOSED_STATUSES:
            return "closed"
        snapshot = get_snapshot(order.instrument_id)
        if snapshot is None:
            return "working"  # feed stale: leave the order working
        if order.status == OrderStatus.OPEN or order.status == OrderStatus.ACCEPTED:
            # DAY expiry (design 24 §D-24.1): a tick beyond expire_after
            # cancels the order instead of working it (sim time, D-10).
            if (
                order.expire_after is not None
                and snapshot.ts > as_utc(order.expire_after)
            ):
                await _expire_day_order(session, order, snapshot.ts)
                await session.commit()
                return "closed"
            if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
                # Resting stop: works until a tick crosses the stop price.
                if not _stop_triggered(order, snapshot.price):
                    return await _rest_or_ioc(session, order, snapshot.price)
                if order.order_type == OrderType.STOP_LIMIT:
                    await _convert_stop_limit(session, order, snapshot.price)
                    await session.commit()
                    # Re-enter as a LIMIT order in a fresh unit-of-work: fills
                    # immediately when the limit is crossed, else rests OPEN.
                    return await _fill_order(sessionmaker, order_id)
                # STOP: triggered -> fill as MARKET at the tick price below.
            elif order.order_type == OrderType.TRAILING_STOP:
                # §D-24.2: roll the water-mark first (a tick setting a new
                # extreme cannot trigger on itself), persist it even while
                # resting, then fill as MARKET once the trail is crossed.
                moved = _update_trail_reference(order, snapshot.price)
                if not _trail_triggered(order, snapshot.price):
                    if moved and order.time_in_force != TimeInForce.IOC:
                        # Keep the water-mark across engine restarts.
                        await session.commit()
                    return await _rest_or_ioc(session, order, snapshot.price)
                await _audit_trailing_trigger(session, order, snapshot.price)
            elif not _marketable(order, snapshot.price):
                return await _rest_or_ioc(session, order, snapshot.price)
            now = business_now()  # business time = sim clock
            execution = Execution(
                order_id=order.order_id,
                price=snapshot.price,
                quantity=order.quantity,
                executed_at=now,
            )
            session.add(execution)
            await session.flush()
            order.status = OrderStatus.FILLED
            order.updated_at = now
            instrument = await session.get(Instrument, order.instrument_id)
            portfolio = await session.get(Portfolio, order.portfolio_id)
            await write_outbox(
                session,
                "trading.executions",
                {
                    "execution_id": execution.execution_id,
                    "order_id": order.order_id,
                    "portfolio_id": order.portfolio_id,
                    "portfolio_type": portfolio.type,
                    "instrument_id": order.instrument_id,
                    "symbol": instrument.symbol,
                    "side": order.side,
                    "price": float(snapshot.price),
                    "quantity": float(order.quantity),
                    "executed_at": now.isoformat(),
                },
            )
            await write_audit(
                session,
                actor_id=None,  # system actor: the matching engine
                event_type=ORDER_FILLED,
                resource_type="ORDER",
                resource_id=order.order_id,
                payload={
                    "execution_id": execution.execution_id,
                    "symbol": instrument.symbol,
                    "side": order.side,
                    "price": str(snapshot.price),
                    "quantity": str(order.quantity),
                },
            )
            await session.commit()
            return "filled"
        return "closed"


def build_execution_engine(settings):
    """Bind settings; returns the `execution_engine(bus, sessionmaker)` worker."""

    async def execution_engine(bus, sessionmaker):
        queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

        async def pump(stream: str) -> None:
            subscription = await bus.subscribe(stream)
            async for event in subscription:
                queue.put_nowait((stream, event))

        tasks = [
            asyncio.create_task(pump("orders.accepted"), name="exec-pump-accepted"),
            asyncio.create_task(pump("market.ticks"), name="exec-pump-ticks"),
        ]
        # In-memory book of working orders per instrument. Rebuilt from the DB
        # on startup (OPEN orders per contract; ACCEPTED too, so orders that
        # crashed mid-flight still get worked).
        book: dict[str, set[str]] = defaultdict(set)
        try:
            await _shielded(_rebuild_book(sessionmaker, book))
            while True:
                stream, event = await queue.get()
                try:
                    if stream == "orders.accepted":
                        await _on_accepted(sessionmaker, book, event["order_id"])
                    else:
                        await _on_tick(sessionmaker, book, event["instrument_id"])
                except Exception:
                    logger.exception("execution engine: event handling failed")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return execution_engine


async def _rebuild_book(sessionmaker, book: dict[str, set[str]]) -> None:
    async with sessionmaker() as session:
        orders = (
            (
                await session.execute(
                    select(Order).where(
                        Order.status.in_([OrderStatus.OPEN, OrderStatus.ACCEPTED])
                    )
                )
            )
            .scalars()
            .all()
        )
        changed = False
        for order in orders:
            # Orders that were ACCEPTED before a restart rest as OPEN.
            if (
                order.order_type in _RESTING_TYPES
                and order.status == OrderStatus.ACCEPTED
            ):
                order.status = OrderStatus.OPEN
                order.updated_at = business_now()
                changed = True
            book[order.instrument_id].add(order.order_id)
        if changed:
            await session.commit()


async def _park_open(sessionmaker, order_id: str) -> tuple[str | None, bool]:
    """Mark a still-working accepted resting-type order (LIMIT/STOP/
    STOP_LIMIT/TRAILING_STOP) OPEN; return (instrument_id, working?) so the
    caller can track it in the book. IOC orders never arrive here —
    `_fill_order` cancels them when they cannot execute immediately."""
    async with sessionmaker() as session:
        order = await session.get(Order, order_id)
        if order is None or order.status in _CLOSED_STATUSES:
            return None, False
        if (
            order.order_type in _RESTING_TYPES
            and order.status == OrderStatus.ACCEPTED
        ):
            order.status = OrderStatus.OPEN
            order.updated_at = business_now()
            await session.commit()
        return order.instrument_id, True


async def _on_accepted(sessionmaker, book: dict[str, set[str]], order_id: str) -> None:
    result = await _shielded(_fill_order(sessionmaker, order_id))
    if result in ("filled", "closed"):
        return
    # Still working: park resting orders as OPEN and track in the book.
    instrument_id, working = await _shielded(_park_open(sessionmaker, order_id))
    if working and instrument_id is not None:
        book[instrument_id].add(order_id)


async def _on_tick(sessionmaker, book: dict[str, set[str]], instrument_id: str) -> None:
    working = book.get(instrument_id)
    if not working:
        return
    for order_id in list(working):
        result = await _shielded(_fill_order(sessionmaker, order_id))
        if result in ("filled", "closed"):
            working.discard(order_id)


# ---------------------------------------------------------------------------
# STP worker
# ---------------------------------------------------------------------------


async def stp_worker(bus, sessionmaker):
    subscription = await bus.subscribe("trading.executions")
    async for event in subscription:
        try:
            await _shielded(_process_execution(sessionmaker, event))
        except Exception:
            logger.exception("stp worker: processing failed for %s", event)
            await _record_stp_exception(sessionmaker, event)


async def _process_execution(sessionmaker, event: dict) -> None:
    execution_id = event["execution_id"]
    async with sessionmaker() as session:
        existing = (
            await session.execute(
                select(SettlementInstruction).where(
                    SettlementInstruction.execution_id == execution_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return  # idempotent: redelivered event already processed

        execution = await session.get(Execution, execution_id)
        order = await session.get(Order, execution.order_id)
        portfolio = await session.get(Portfolio, order.portfolio_id)
        instrument = await session.get(Instrument, order.instrument_id)

        position = await session.get(
            Position, (order.portfolio_id, order.instrument_id)
        )
        if position is None:
            position = Position(
                portfolio_id=order.portfolio_id,
                instrument_id=order.instrument_id,
                quantity=Decimal("0"),
                avg_cost=Decimal("0"),
            )
            session.add(position)
            await session.flush()
        # Cash moves by the bond-aware trade value (bonds: face × price / 100,
        # §A2); avg_cost stays a per-unit price (% of par for bonds).
        cash_effect = trade_value(instrument, execution.quantity, execution.price)
        if order.side == "BUY":
            new_qty = position.quantity + execution.quantity
            position.avg_cost = (
                (position.quantity * position.avg_cost)
                + (execution.quantity * execution.price)
            ) / new_qty
            position.quantity = new_qty
            portfolio.cash_balance -= cash_effect
        else:
            # Realized P&L = (exec_price - avg_cost) * qty; computed on read
            # (see portfolios.valuation.compute_realized), no column to store.
            position.quantity -= execution.quantity
            portfolio.cash_balance += cash_effect
        position.updated_at = utcnow()

        instruction = SettlementInstruction(
            execution_id=execution_id,
            lifecycle_state=LifecycleState.EXECUTED,
            created_at=business_now(),  # display time = sim clock; the sweeper
            # keeps its own wall-clock basis (created_wall below)
        )
        session.add(instruction)
        await session.flush()
        await write_outbox(
            session,
            "stp.lifecycle",
            {
                "settlement_id": instruction.settlement_id,
                "execution_id": execution_id,
                "portfolio_id": order.portfolio_id,
                "state": LifecycleState.EXECUTED.value,
            },
        )
        await session.commit()


async def _record_stp_exception(sessionmaker, event: dict) -> None:
    """FR-ORD-005 E1: audit STP_EXCEPTION (HIGH) + notify the portfolio owner."""
    try:
        await _shielded(_write_stp_exception(sessionmaker, event))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("stp worker: failed to record STP exception")


async def _write_stp_exception(sessionmaker, event: dict) -> None:
    # No direct ops-user resolution exists in the foundation, so the SYSTEM
    # notification goes to the portfolio owner; ops visibility comes via the
    # audit/governance views built by the parallel team.
    async with sessionmaker() as session:
        owner_id = None
        execution = await session.get(Execution, event.get("execution_id", ""))
        if execution is not None:
            order = await session.get(Order, execution.order_id)
            if order is not None:
                portfolio = await session.get(Portfolio, order.portfolio_id)
                if portfolio is not None:
                    owner_id = portfolio.owner_id
        await write_audit(
            session,
            actor_id=None,
            event_type=STP_EXCEPTION,
            resource_type="EXECUTION",
            resource_id=event.get("execution_id"),
            severity="HIGH",
            payload={"event": {k: str(v) for k, v in event.items()}},
            flush_only=False,  # security/ops-critical: persist immediately
        )
        if owner_id:
            await write_outbox(
                session,
                "notify",
                {
                    "user_id": owner_id,
                    "category": "SYSTEM",
                    "title": "STP exception",
                    "body": f"Settlement failed for execution "
                    f"{event.get('execution_id')}; flagged for operations.",
                },
            )
            await session.commit()


# ---------------------------------------------------------------------------
# Settlement sweeper
# ---------------------------------------------------------------------------


def build_settlement_sweeper(settings):
    """Bind settings; returns the `settlement_sweeper(bus, sessionmaker)` worker."""

    async def settlement_sweeper(bus, sessionmaker):
        delay = settings.SETTLEMENT_DELAY_SECONDS
        # SettlementInstruction.created_at is business (sim) time, so the
        # sweep cadence tracks wall time in-process: created_wall for the
        # EXECUTED→AFFIRMED leg, affirmed_at for AFFIRMED→SETTLED; on cold
        # start a row's first sighting is the basis (restart shortens delays,
        # the accepted trade-off).
        affirmed_at: dict[str, datetime] = {}
        created_wall: dict[str, datetime] = {}
        while True:
            await asyncio.sleep(1.0)
            try:
                await _shielded(_sweep_once(sessionmaker, delay, affirmed_at, created_wall))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("settlement sweeper: sweep failed")

    return settlement_sweeper


async def _sweep_once(sessionmaker, delay: float, affirmed_at: dict, created_wall: dict) -> None:
    now = utcnow()  # sweep cadence is wall-clock by design; created_at is sim
    # (business) time — never compare the two, so created_wall tracks the
    # wall instant the instruction was first seen (restart shortens delays,
    # same accepted trade-off as affirmed_at).
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(SettlementInstruction, Execution, Order)
                .join(
                    Execution,
                    SettlementInstruction.execution_id == Execution.execution_id,
                )
                .join(Order, Execution.order_id == Order.order_id)
                .where(
                    SettlementInstruction.lifecycle_state.in_(
                        [LifecycleState.EXECUTED, LifecycleState.AFFIRMED]
                    )
                )
            )
        ).all()
        changed = False
        for instruction, _execution, order in rows:
            created_wall.setdefault(instruction.settlement_id, now)
            if instruction.lifecycle_state == LifecycleState.EXECUTED:
                age = (now - created_wall[instruction.settlement_id]).total_seconds()
                if age >= delay:
                    instruction.lifecycle_state = LifecycleState.AFFIRMED
                    affirmed_at[instruction.settlement_id] = now
                    created_wall.pop(instruction.settlement_id, None)
                    changed = True
            elif instruction.lifecycle_state == LifecycleState.AFFIRMED:
                basis = affirmed_at.get(
                    instruction.settlement_id,
                    now,
                )
                if (now - basis).total_seconds() >= delay:
                    instruction.lifecycle_state = LifecycleState.SETTLED
                    instruction.settled_at = business_now()  # display: sim clock
                    affirmed_at.pop(instruction.settlement_id, None)
                    changed = True
            else:
                continue
            await write_outbox(
                session,
                "stp.lifecycle",
                {
                    "settlement_id": instruction.settlement_id,
                    "execution_id": instruction.execution_id,
                    "portfolio_id": order.portfolio_id,
                    "state": instruction.lifecycle_state,
                },
            )
        if changed:
            await session.commit()
