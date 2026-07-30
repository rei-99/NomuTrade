"""Pre-trade validation rule chain (docs/design/02, FR-ORD-002/003).

Rules run in-process over the request's DB session and the in-memory
latest-price registry — no external calls (NFR-PER-001 p95 <= 500 ms).
Each failure maps to a machine-readable reason code persisted on the order
(`reject_reason`) and returned in the 422 envelope's `details`.

Design 21 additions:
- STOP / STOP_LIMIT tickets require a positive stop_price; STOP_LIMIT also a
  positive limit_price (§A3);
- compliance rules (§A4): the restricted-instrument list
  (RESTRICTED_INSTRUMENT) and a per-order notional cap
  (ORDER_MAX_NOTIONAL, 0 disables — MAX_NOTIONAL_EXCEEDED);
- bond cash math (§A2): `trade_value` values bond trades (quoted % of par,
  quantity = face value) at qty × price / 100; equities at qty × price.

Design 24 additions:
- TRAILING_STOP requires exactly one of trail_amount / trail_pct (> 0) and
  forbids stop_price/limit_price; trail params on other types are rejected
  (§D-24.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import DependencyUnavailable
from app.core.models import Instrument, Portfolio, Position, RestrictedInstrument
from app.modules.marketdata.registry import get_latest_price, warm_from_db

INSTRUMENT_NOT_TRADABLE = "INSTRUMENT_NOT_TRADABLE"
INVALID_QUANTITY = "INVALID_QUANTITY"
LIMIT_PRICE_REQUIRED = "LIMIT_PRICE_REQUIRED"
STOP_PRICE_REQUIRED = "STOP_PRICE_REQUIRED"
INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"
INSUFFICIENT_HOLDINGS = "INSUFFICIENT_HOLDINGS"
RESTRICTED_INSTRUMENT = "RESTRICTED_INSTRUMENT"
MAX_NOTIONAL_EXCEEDED = "MAX_NOTIONAL_EXCEEDED"
TRAIL_PARAM_REQUIRED = "TRAIL_PARAM_REQUIRED"
TRAIL_PARAM_CONFLICT = "TRAIL_PARAM_CONFLICT"
TRAIL_PARAM_FORBIDDEN = "TRAIL_PARAM_FORBIDDEN"
PRICE_FIELD_FORBIDDEN = "PRICE_FIELD_FORBIDDEN"


def trade_value(
    instrument: Instrument, quantity: Decimal, price: Decimal
) -> Decimal:
    """Cash amount of a trade at `price`.

    Bonds quote % of par and order quantity is face value (design 21 §A2),
    so value = qty × price / 100; equities value at qty × price. Shared by
    pre-trade validation, the STP worker's cash adjustment and the
    portfolios module's position valuation. Linear in `price`, so a price
    difference may be passed to value a P&L delta.
    """
    if instrument.asset_class == "BOND":
        return quantity * price / Decimal("100")
    return quantity * price


@dataclass
class Rejection:
    code: str
    message: str
    details: dict | None = None  # extra fields merged into the 422 details


async def validate_order(
    db: AsyncSession,
    *,
    portfolio: Portfolio,
    instrument: Instrument,
    side: str,
    order_type: str,
    quantity: Decimal,
    limit_price: Decimal | None,
    stop_price: Decimal | None = None,
    trail_amount: Decimal | None = None,
    trail_pct: Decimal | None = None,
    settings: Settings | None = None,
) -> Rejection | None:
    """Run the rule chain; return the first Rejection, or None on PASS."""
    if not instrument.tradable:
        return Rejection(
            INSTRUMENT_NOT_TRADABLE,
            f"instrument {instrument.symbol} is not tradable",
        )
    restricted = (
        await db.execute(
            select(RestrictedInstrument).where(
                RestrictedInstrument.symbol == instrument.symbol,
                RestrictedInstrument.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if restricted is not None:
        return Rejection(
            RESTRICTED_INSTRUMENT,
            f"instrument {instrument.symbol} is on the restricted list",
            details={"reason": restricted.reason},
        )
    if quantity <= 0 or quantity % instrument.lot_size != 0:
        return Rejection(
            INVALID_QUANTITY,
            f"quantity must be positive and a multiple of lot size "
            f"{instrument.lot_size}",
        )
    if order_type in ("LIMIT", "STOP_LIMIT") and (
        limit_price is None or limit_price <= 0
    ):
        return Rejection(
            LIMIT_PRICE_REQUIRED,
            f"{order_type} orders require a positive limit_price",
        )
    if order_type in ("STOP", "STOP_LIMIT") and (
        stop_price is None or stop_price <= 0
    ):
        return Rejection(
            STOP_PRICE_REQUIRED,
            f"{order_type} orders require a positive stop_price",
        )
    # Trailing stop (design 24 §D-24.2): exactly one trail param (> 0), and
    # no fixed stop/limit prices; trail params are exclusive to the type.
    if order_type == "TRAILING_STOP":
        if limit_price is not None or stop_price is not None:
            return Rejection(
                PRICE_FIELD_FORBIDDEN,
                "TRAILING_STOP orders must not set limit_price/stop_price",
            )
        amount_ok = trail_amount is not None and trail_amount > 0
        pct_ok = trail_pct is not None and trail_pct > 0
        if amount_ok and pct_ok:
            return Rejection(
                TRAIL_PARAM_CONFLICT,
                "provide exactly one of trail_amount / trail_pct, not both",
            )
        if not amount_ok and not pct_ok:
            return Rejection(
                TRAIL_PARAM_REQUIRED,
                "TRAILING_STOP orders require exactly one of trail_amount / "
                "trail_pct (> 0; trail_pct is percentage points)",
            )
    elif trail_amount is not None or trail_pct is not None:
        return Rejection(
            TRAIL_PARAM_FORBIDDEN,
            f"trail_amount/trail_pct are only valid on TRAILING_STOP orders",
        )
    price: Decimal | None = None
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
        cost = trade_value(instrument, quantity, price)
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

    # Per-order notional cap (A4). Skipped when disabled (0) or when no
    # latest price exists — the SELL side does not otherwise need one.
    max_notional = settings.ORDER_MAX_NOTIONAL if settings is not None else 0.0
    if max_notional > 0:
        if price is None:
            price = get_latest_price(instrument.instrument_id)
            if price is None:
                await warm_from_db(
                    db,
                    [instrument.instrument_id],
                    {instrument.instrument_id: instrument.symbol},
                )
                price = get_latest_price(instrument.instrument_id)
        if price is not None:
            notional = trade_value(instrument, quantity, price)
            if notional > Decimal(str(max_notional)):
                return Rejection(
                    MAX_NOTIONAL_EXCEEDED,
                    f"order notional {notional} exceeds the per-order limit "
                    f"{max_notional}",
                    details={
                        "limit": float(max_notional),
                        "notional": float(notional),
                    },
                )
    return None
