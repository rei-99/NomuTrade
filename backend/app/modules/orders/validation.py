"""Pre-trade validation rule chain (docs/design/02, FR-ORD-002/003).

Rules run in-process over the request's DB session and the in-memory
latest-price registry — no external calls (NFR-PER-001 p95 <= 500 ms).
Each failure maps to a machine-readable reason code persisted on the order
(`reject_reason`) and returned in the 422 envelope's `details`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DependencyUnavailable
from app.core.models import Instrument, Portfolio, Position
from app.modules.marketdata.registry import get_latest_price, warm_from_db

INSTRUMENT_NOT_TRADABLE = "INSTRUMENT_NOT_TRADABLE"
INVALID_QUANTITY = "INVALID_QUANTITY"
LIMIT_PRICE_REQUIRED = "LIMIT_PRICE_REQUIRED"
INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"
INSUFFICIENT_HOLDINGS = "INSUFFICIENT_HOLDINGS"


@dataclass
class Rejection:
    code: str
    message: str


async def validate_order(
    db: AsyncSession,
    *,
    portfolio: Portfolio,
    instrument: Instrument,
    side: str,
    order_type: str,
    quantity: Decimal,
    limit_price: Decimal | None,
) -> Rejection | None:
    """Run the rule chain; return the first Rejection, or None on PASS."""
    if not instrument.tradable:
        return Rejection(
            INSTRUMENT_NOT_TRADABLE,
            f"instrument {instrument.symbol} is not tradable",
        )
    if quantity <= 0 or quantity % instrument.lot_size != 0:
        return Rejection(
            INVALID_QUANTITY,
            f"quantity must be positive and a multiple of lot size "
            f"{instrument.lot_size}",
        )
    if order_type == "LIMIT" and (limit_price is None or limit_price <= 0):
        return Rejection(
            LIMIT_PRICE_REQUIRED, "LIMIT orders require a positive limit_price"
        )
    if side == "BUY":
        price = get_latest_price(instrument.instrument_id)
        if price is None:
            await warm_from_db(
                db,
                [instrument.instrument_id],
                {instrument.instrument_id: instrument.symbol},
            )
            price = get_latest_price(instrument.instrument_id)
        if price is None:
            # FR-ORD-003 E1: no tick available -> feed considered stale,
            # submission suspended for this instrument.
            raise DependencyUnavailable(
                f"no market data available for {instrument.symbol}"
            )
        cost = quantity * price
        if cost > portfolio.cash_balance:
            return Rejection(
                INSUFFICIENT_BUYING_POWER,
                f"estimated cost {cost} exceeds cash balance "
                f"{portfolio.cash_balance}",
            )
    else:  # SELL
        position = await db.get(
            Position, (portfolio.portfolio_id, instrument.instrument_id)
        )
        held = position.quantity if position is not None else Decimal("0")
        if held < quantity:
            return Rejection(
                INSUFFICIENT_HOLDINGS,
                f"held quantity {held} is below order quantity {quantity}",
            )
    return None
