"""Orders module: order ticket API, trade blotter, and the STP pipeline workers.

Design: docs/design/02-order-execution-stp.md (core pipeline),
docs/design/24-advanced-orders.md (time-in-force, TRAILING_STOP). Exposes:
- `router`: POST/GET/PATCH /orders, POST /orders/{id}/cancel, GET /trades
- `get_workers(settings)`: execution engine, STP worker, settlement sweeper.
"""

from app.modules.orders.api import router
from app.modules.orders.workers import (
    build_execution_engine,
    build_settlement_sweeper,
    stp_worker,
)


def get_workers(settings):
    return [
        build_execution_engine(settings),
        stp_worker,
        build_settlement_sweeper(settings),
    ]


__all__ = ["router", "get_workers"]
