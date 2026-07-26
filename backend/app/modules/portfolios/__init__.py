"""Portfolios module: positions/valuation/KPI read APIs + valuation projector.

Design: docs/design/03-portfolio-management.md. Exposes:
- `router`: GET /portfolios, /portfolios/{id}/positions|valuation|
  transactions|performance
- `get_workers(settings)`: valuation projector (ValuationSnapshot writer).
"""

from app.modules.portfolios.api import router
from app.modules.portfolios.worker import valuation_projector


def get_workers(settings):
    return [valuation_projector]


__all__ = ["router", "get_workers"]
