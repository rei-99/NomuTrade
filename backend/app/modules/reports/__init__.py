"""Reports module: synchronous PDF/CSV report generation, MVP scope (FR-RPT-003).

Reports are generated synchronously inside the request (DESIGN 04 queues long
jobs on the bus — that is the post-MVP extension point; the completion `notify`
event is already published so the UX is unchanged when generation moves).
Files land in backend/var/reports/<report_id>.<ext>; metadata lives in Report.

Latest prices are read from PriceTick directly (the marketdata package is built
in parallel and must not be imported).
"""

from __future__ import annotations

import asyncio
import csv
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.db import get_db
from app.core.errors import Forbidden, NotFound, StateConflict, ValidationError
from app.core.events import write_outbox
from app.core.models import (
    Execution,
    Instrument,
    Order,
    Portfolio,
    Position,
    PriceTick,
    Report,
    ValuationSnapshot,
)
from app.core.security import (
    SessionData,
    get_effective_permissions,
    require_permission,
)
from app.core.timeutil import as_utc, utcnow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])

REPORT_GENERATED = "REPORT_GENERATED"

# Generated files: backend/var/reports/<report_id>.<ext> (created on demand).
REPORTS_DIR = Path(__file__).resolve().parents[3] / "var" / "reports"

MEDIA_TYPES = {"PDF": "application/pdf", "CSV": "text/csv"}


def _num(value: Decimal | None) -> str:
    """Compact decimal rendering for CSV/PDF cells (no thousands separators)."""
    if value is None:
        return "n/a"
    return format(value.quantize(Decimal("0.01")), "f")


# ---------------------------------------------------------------------------
# Data builders -> (title, headers, rows, summary) shared by CSV and PDF
# ---------------------------------------------------------------------------


async def _latest_prices(
    db: AsyncSession, instrument_ids: list[str]
) -> dict[str, Decimal]:
    """Latest close per instrument from PriceTick (demo volumes: per-id query)."""
    prices: dict[str, Decimal] = {}
    for instrument_id in instrument_ids:
        tick = (
            await db.execute(
                select(PriceTick)
                .where(PriceTick.instrument_id == instrument_id)
                .order_by(PriceTick.ts.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if tick is not None:
            prices[instrument_id] = tick.close
    return prices


async def _holdings_rows(db: AsyncSession, portfolio: Portfolio) -> tuple[list, list]:
    positions = (
        (
            await db.execute(
                select(Position, Instrument)
                .join(Instrument, Position.instrument_id == Instrument.instrument_id)
                .where(Position.portfolio_id == portfolio.portfolio_id)
                .order_by(Instrument.symbol)
            )
        )
        .all()
    )
    prices = await _latest_prices(db, [p.Position.instrument_id for p in positions])
    rows = []
    positions_value = Decimal("0")
    for position, instrument in positions:
        last = prices.get(position.instrument_id)
        market_value = (
            position.quantity * last if last is not None else None
        )
        if market_value is not None:
            positions_value += market_value
        rows.append(
            [
                instrument.symbol,
                instrument.name,
                _num(position.quantity),
                _num(position.avg_cost),
                _num(last),
                _num(market_value),
            ]
        )
    cash = portfolio.cash_balance
    summary = [
        ("positions_value", _num(positions_value)),
        ("cash", _num(cash)),
        ("total_value", _num(positions_value + cash)),
        ("currency", await _portfolio_currency(db, portfolio)),
    ]
    return rows, summary


async def _portfolio_currency(db: AsyncSession, portfolio: Portfolio) -> str:
    """Currency of the portfolio's first held instrument; USD when empty
    (the dataset universe is USD-denominated, D-16)."""
    return (
        await db.scalar(
            select(Instrument.currency)
            .join(Position, Position.instrument_id == Instrument.instrument_id)
            .where(Position.portfolio_id == portfolio.portfolio_id)
            .limit(1)
        )
    ) or "USD"


async def _build_holdings(db: AsyncSession, portfolio: Portfolio, start, end):
    rows, summary = await _holdings_rows(db, portfolio)
    headers = ["symbol", "name", "quantity", "avg_cost", "last_price", "market_value"]
    title = f"Holdings report — {portfolio.name}"
    return title, headers, rows, summary


async def _build_transactions(db: AsyncSession, portfolio: Portfolio, start, end):
    executions = (
        (
            await db.execute(
                select(Execution, Order, Instrument)
                .join(Order, Execution.order_id == Order.order_id)
                .join(Instrument, Order.instrument_id == Instrument.instrument_id)
                .where(
                    Order.portfolio_id == portfolio.portfolio_id,
                    Execution.executed_at >= start,
                    Execution.executed_at <= end,
                )
                .order_by(Execution.executed_at)
            )
        )
        .all()
    )
    rows = []
    total_value = Decimal("0")
    for execution, order, instrument in executions:
        value = execution.quantity * execution.price
        total_value += value
        rows.append(
            [
                as_utc(execution.executed_at).isoformat(),
                instrument.symbol,
                order.side,
                _num(execution.quantity),
                _num(execution.price),
                _num(value),
                order.order_id,
                execution.execution_id,
            ]
        )
    summary = [
        ("executions", str(len(rows))),
        ("gross_value", _num(total_value)),
        ("currency", executions[0][2].currency if executions else "USD"),
    ]
    headers = [
        "executed_at",
        "symbol",
        "side",
        "quantity",
        "price",
        "value",
        "order_id",
        "execution_id",
    ]
    title = f"Transactions report — {portfolio.name}"
    return title, headers, rows, summary


async def _build_performance(db: AsyncSession, portfolio: Portfolio, start, end):
    snapshots = (
        (
            await db.execute(
                select(ValuationSnapshot)
                .where(
                    ValuationSnapshot.portfolio_id == portfolio.portfolio_id,
                    ValuationSnapshot.ts >= start,
                    ValuationSnapshot.ts <= end,
                )
                .order_by(ValuationSnapshot.ts)
            )
        )
        .scalars()
        .all()
    )
    rows = []
    for snap in snapshots:
        total = snap.market_value + snap.cash
        rows.append(
            [
                as_utc(snap.ts).isoformat(),
                _num(snap.market_value),
                _num(snap.cash),
                _num(total),
                _num(snap.realized_pnl),
                _num(snap.unrealized_pnl),
            ]
        )
    if not rows:
        # No snapshots yet: fall back to a single current-valuation row.
        holdings_rows, holdings_summary = await _holdings_rows(db, portfolio)
        positions_value = Decimal(holdings_summary[0][1])
        cash = portfolio.cash_balance
        rows.append(
            [
                utcnow().isoformat(),
                _num(positions_value),
                _num(cash),
                _num(positions_value + cash),
                _num(Decimal("0")),
                _num(Decimal("0")),
            ]
        )
    totals = [Decimal(r[3]) for r in rows]
    start_total, end_total = totals[0], totals[-1]
    change = end_total - start_total
    change_pct = (change / start_total * 100) if start_total else Decimal("0")
    summary = [
        ("data_points", str(len(rows))),
        ("start_total_value", _num(start_total)),
        ("end_total_value", _num(end_total)),
        ("change", _num(change)),
        ("change_pct", _num(change_pct)),
        ("currency", await _portfolio_currency(db, portfolio)),
    ]
    headers = ["ts", "market_value", "cash", "total_value", "realized_pnl", "unrealized_pnl"]
    title = f"Performance report — {portfolio.name}"
    return title, headers, rows, summary


_BUILDERS = {
    "HOLDINGS": _build_holdings,
    "TRANSACTIONS": _build_transactions,
    "PERFORMANCE": _build_performance,
}


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, title: str, headers: list, rows: list, summary: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([title])
        writer.writerow(headers)
        writer.writerows(rows)
        writer.writerow([])
        for label, value in summary:
            writer.writerow([label, value])


def _write_pdf(path: Path, title: str, headers: list, rows: list, summary: list) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    table = Table([headers, *rows])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3864")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 12))
    for label, value in summary:
        story.append(Paragraph(f"{label}: {value}", styles["Normal"]))
    SimpleDocTemplate(str(path), pagesize=A4, title=title).build(story)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class ReportRequest(BaseModel):
    type: Literal["HOLDINGS", "TRANSACTIONS", "PERFORMANCE"]
    portfolio_id: str
    period_start: datetime
    period_end: datetime
    format: Literal["PDF", "CSV"]


def _report_json(report: Report) -> dict:
    return {
        "report_id": report.report_id,
        "type": report.type,
        "portfolio_id": report.portfolio_id,
        "period_start": as_utc(report.period_start).isoformat(),
        "period_end": as_utc(report.period_end).isoformat(),
        "format": report.format,
        "status": report.status,
        "created_at": as_utc(report.created_at).isoformat(),
        "download_url": f"/api/v1/reports/{report.report_id}/download",
    }


async def _can_view_all(db: AsyncSession, user_id: str) -> bool:
    return "PORTFOLIO_VIEW_ALL" in await get_effective_permissions(db, user_id)


@router.post("/reports", status_code=201)
async def create_report(
    body: ReportRequest,
    session: SessionData = Depends(require_permission("REPORT_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await db.get(Portfolio, body.portfolio_id)
    if portfolio is None:
        raise NotFound("portfolio not found")
    if portfolio.owner_id != session.user_id and not await _can_view_all(
        db, session.user_id
    ):
        raise Forbidden("you do not have access to this portfolio")
    start, end = as_utc(body.period_start), as_utc(body.period_end)
    if start >= end:
        raise ValidationError("period_start must be before period_end")

    report = Report(
        type=body.type,
        portfolio_id=portfolio.portfolio_id,
        period_start=start,
        period_end=end,
        format=body.format,
        status="REQUESTED",
        requested_by=session.user_id,
    )
    db.add(report)
    await db.flush()  # materialize report_id for the filename

    title, headers, rows, summary = await _BUILDERS[body.type](
        db, portfolio, start, end
    )
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ext = "pdf" if body.format == "PDF" else "csv"
    path = REPORTS_DIR / f"{report.report_id}.{ext}"
    if body.format == "PDF":
        # reportlab is sync CPU work; keep the event loop free.
        await asyncio.to_thread(_write_pdf, path, title, headers, rows, summary)
    else:
        _write_csv(path, title, headers, rows, summary)

    report.status = "DONE"
    report.file_ref = str(path)
    await write_audit(
        db,
        actor_id=session.user_id,
        event_type=REPORT_GENERATED,
        resource_type="report",
        resource_id=report.report_id,
        payload={
            "type": report.type,
            "format": report.format,
            "portfolio_id": portfolio.portfolio_id,
            "file_ref": str(path),
        },
        flush_only=True,
    )
    await write_outbox(
        db,
        "notify",
        {
            "user_id": session.user_id,
            "category": "REPORT",
            "title": f"Report ready: {report.type} ({report.format})",
            "body": f"Your {report.type.lower()} report for '{portfolio.name}' is ready to download.",
        },
    )
    await db.commit()
    return {
        "report_id": report.report_id,
        "status": report.status,
        "download_url": f"/api/v1/reports/{report.report_id}/download",
    }


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    ts_raw, sep, rid = cursor.partition("|")
    if not sep or not rid:
        raise ValidationError("invalid cursor")
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        raise ValidationError("invalid cursor")
    return ts, rid


@router.get("/reports")
async def list_reports(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    session: SessionData = Depends(require_permission("REPORT_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    """My reports (all reports with PORTFOLIO_VIEW_ALL), newest first."""
    stmt = select(Report)
    if not await _can_view_all(db, session.user_id):
        stmt = stmt.where(Report.requested_by == session.user_id)
    if cursor:
        ts, rid = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                Report.created_at < ts,
                and_(Report.created_at == ts, Report.report_id < rid),
            )
        )
    stmt = stmt.order_by(Report.created_at.desc(), Report.report_id.desc()).limit(
        limit + 1
    )
    rows = (await db.execute(stmt)).scalars().all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = f"{as_utc(last.created_at).isoformat()}|{last.report_id}"
    return {"items": [_report_json(r) for r in page], "next_cursor": next_cursor}


async def _get_report_for_user(
    db: AsyncSession, report_id: str, session: SessionData
) -> Report:
    report = await db.get(Report, report_id)
    if report is None:
        raise NotFound("report not found")
    if report.requested_by != session.user_id and not await _can_view_all(
        db, session.user_id
    ):
        raise Forbidden("you do not have access to this report")
    return report


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    session: SessionData = Depends(require_permission("REPORT_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report_for_user(db, report_id, session)
    return _report_json(report)


@router.get("/reports/{report_id}/download")
async def download_report(
    report_id: str,
    session: SessionData = Depends(require_permission("REPORT_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    report = await _get_report_for_user(db, report_id, session)
    if report.status != "DONE":
        raise StateConflict(f"report is not ready (status {report.status})")
    if not report.file_ref or not Path(report.file_ref).is_file():
        raise NotFound("report file is missing")
    ext = "pdf" if report.format == "PDF" else "csv"
    return FileResponse(
        report.file_ref,
        media_type=MEDIA_TYPES[report.format],
        filename=f"{report.type.lower()}_{report.report_id}.{ext}",
    )
