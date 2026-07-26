"""Market-data module: simulated tick feed, price registry, read APIs.

Design: docs/design/01-market-data.md. Replaces any live feed with generated
history replayed as a tick stream (INT-04, C-04). Exposes:
- `router`: GET /instruments, GET /instruments/{symbol}/prices
- `get_workers(settings)`: the tick replayer (history bootstrap + live ticks).
"""

from app.modules.marketdata.api import router
from app.modules.marketdata.worker import build_tick_replayer


def get_workers(settings):
    return [build_tick_replayer(settings)]


__all__ = ["router", "get_workers"]
