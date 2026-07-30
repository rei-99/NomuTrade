"""Reports module: synchronous PDF/CSV report generation, MVP scope (FR-RPT-003),
plus per-user scheduled reports (docs/design/23-scheduled-reports.md — TBD-13).

Reports are generated synchronously inside the request (DESIGN 04 queues long
jobs on the bus — that is the post-MVP extension point; the completion `notify`
event is already published so the UX is unchanged when generation moves).
Files land in backend/var/reports/<report_id>.<ext>; metadata lives in Report.

Schedules (design 23): a ReportSchedule row fires on the simulation clock
(wall-clock fallback); the `report_scheduler` worker sweeps due schedules
every SCHEDULE_SWEEP_SECONDS and generates through the same `_generate_report`
path as POST /reports — identical rows, audit and notify behavior.

Latest prices are read from PriceTick directly (only the simulation clock is
imported from the marketdata registry).
"""

from __future__ import annotations

import asyncio
import csv
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
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
    Portfolio,
    Position,
    PriceTick,
    Report,
    ReportSchedule,
    ValuationSnapshot,
)
from app.core.security import (
    SessionData,
    get_effective_permissions,
    require_permission,
)
from app.core.timeutil import as_utc, utcnow
from app.modules.marketdata.registry import get_sim_now

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])

REPORT_GENERATED = "REPORT_GENERATED"

# Generated files: backend/var/reports/<report_id>.<ext> (created on demand).
REPORTS_DIR = Path(__file__).resolve().parents[3] / "var" / "reports"

MEDIA_TYPES = {"PDF": "application/pdf", "CSV": "text/csv"}

# Schedules (design 23): trailing period per frequency, sweep cadence, cap.
FREQUENCY_DELTAS = {"DAILY": timedelta(days=1), "WEEKLY": timedelta(days=7)}
MAX_ACTIVE_SCHEDULES = 10
SCHEDULE_SWEEP_SECONDS = 10.0


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


async def _generate_report(
    db: AsyncSession,
    *,
    report_type: str,
    portfolio: Portfolio,
    start: datetime,
    end: datetime,
    report_format: str,
    actor_id: str,
    schedule: ReportSchedule | None = None,
) -> Report:
    """Create the Report row, render the file, audit + notify; caller commits.

    Shared by POST /reports (schedule=None) and the report_scheduler worker
    (design 23): both paths produce identical rows, audit and notify behavior;
    scheduled runs additionally carry schedule_id in the audit payload and
    mention the schedule in the notification body.
    """
    report = Report(
        type=report_type,
        portfolio_id=portfolio.portfolio_id,
        period_start=start,
        period_end=end,
        format=report_format,
        status="REQUESTED",
        requested_by=actor_id,
    )
    db.add(report)
    await db.flush()  # materialize report_id for the filename

    title, headers, rows, summary = await _BUILDERS[report_type](
        db, portfolio, start, end
    )
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ext = "pdf" if report_format == "PDF" else "csv"
    path = REPORTS_DIR / f"{report.report_id}.{ext}"
    if report_format == "PDF":
        # reportlab is sync CPU work; keep the event loop free.
        await asyncio.to_thread(_write_pdf, path, title, headers, rows, summary)
    else:
        _write_csv(path, title, headers, rows, summary)

    report.status = "DONE"
    report.file_ref = str(path)
    audit_payload = {
        "type": report.type,
        "format": report.format,
        "portfolio_id": portfolio.portfolio_id,
        "file_ref": str(path),
    }
    if schedule is not None:
        audit_payload["schedule_id"] = schedule.schedule_id
    await write_audit(
        db,
        actor_id=actor_id,
        event_type=REPORT_GENERATED,
        resource_type="report",
        resource_id=report.report_id,
        payload=audit_payload,
        flush_only=True,
    )
    if schedule is not None:
        body = (
            f"Your scheduled {report.type.lower()} report "
            f"({schedule.frequency.lower()}) for '{portfolio.name}' "
            "is ready to download."
        )
    else:
        body = (
            f"Your {report.type.lower()} report for '{portfolio.name}' "
            "is ready to download."
        )
    await write_outbox(
        db,
        "notify",
        {
            "user_id": actor_id,
            "category": "REPORT",
            "title": f"Report ready: {report.type} ({report.format})",
            "body": body,
        },
    )
    return report


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

    report = await _generate_report(
        db,
        report_type=body.type,
        portfolio=portfolio,
        start=start,
        end=end,
        report_format=body.format,
        actor_id=session.user_id,
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


# ---------------------------------------------------------------------------
# Schedules (design 23): CRUD + report_scheduler worker
# ---------------------------------------------------------------------------


class ReportScheduleRequest(BaseModel):
    portfolio_id: str
    type: Literal["HOLDINGS", "TRANSACTIONS", "PERFORMANCE"]
    format: Literal["PDF", "CSV"]
    frequency: Literal["DAILY", "WEEKLY"]


def _schedule_json(schedule: ReportSchedule) -> dict:
    return {
        "schedule_id": schedule.schedule_id,
        "portfolio_id": schedule.portfolio_id,
        "type": schedule.type,
        "format": schedule.format,
        "frequency": schedule.frequency,
        "active": schedule.active,
        "next_run_at": as_utc(schedule.next_run_at).isoformat(),
        "last_run_at": (
            as_utc(schedule.last_run_at).isoformat() if schedule.last_run_at else None
        ),
        "created_at": as_utc(schedule.created_at).isoformat(),
    }


@router.get("/report-schedules")
async def list_report_schedules(
    session: SessionData = Depends(require_permission("REPORT_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    """My schedules, newest first (a user's schedules are few; no cursor)."""
    rows = (
        (
            await db.execute(
                select(ReportSchedule)
                .where(ReportSchedule.user_id == session.user_id)
                .order_by(
                    ReportSchedule.created_at.desc(),
                    ReportSchedule.schedule_id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_schedule_json(s) for s in rows], "next_cursor": None}


@router.post("/report-schedules", status_code=201)
async def create_report_schedule(
    body: ReportScheduleRequest,
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
    active_count = await db.scalar(
        select(func.count(ReportSchedule.schedule_id)).where(
            ReportSchedule.user_id == session.user_id,
            ReportSchedule.active.is_(True),
        )
    )
    if active_count >= MAX_ACTIVE_SCHEDULES:
        raise BusinessRuleViolation(
            f"at most {MAX_ACTIVE_SCHEDULES} active schedules per user"
        )
    # No retroactive backfill: first run one frequency boundary from creation,
    # measured on the simulation clock (wall clock when no replay runs).
    now = get_sim_now() or utcnow()
    schedule = ReportSchedule(
        user_id=session.user_id,
        portfolio_id=portfolio.portfolio_id,
        type=body.type,
        format=body.format,
        frequency=body.frequency,
        next_run_at=now + FREQUENCY_DELTAS[body.frequency],
    )
    db.add(schedule)
    await db.commit()
    return _schedule_json(schedule)


@router.delete("/report-schedules/{schedule_id}")
async def delete_report_schedule(
    schedule_id: str,
    session: SessionData = Depends(require_permission("REPORT_VIEW")),
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete (design 23); mine only — 404 hides other users' rows."""
    schedule = await db.get(ReportSchedule, schedule_id)
    if schedule is None or schedule.user_id != session.user_id:
        raise NotFound("report schedule not found")
    await db.delete(schedule)
    await db.commit()
    return {"schedule_id": schedule_id, "deleted": True}


async def _run_schedule(db: AsyncSession, schedule: ReportSchedule) -> None:
    """Generate one scheduled report and advance the schedule by one step.

    The period is the trailing frequency window ending at the due timestamp
    (e.g. a DAILY schedule due at t covers [t - 1 day, t]); next_run_at moves
    forward by exactly one frequency step, so a lapsed schedule catches up
    over successive sweeps instead of generating a backlog in one pass.
    """
    portfolio = await db.get(Portfolio, schedule.portfolio_id)
    if portfolio is None:
        # Portfolio gone: deactivate rather than fail the sweep forever.
        schedule.active = False
        await db.commit()
        return
    due_at = as_utc(schedule.next_run_at)
    delta = FREQUENCY_DELTAS[schedule.frequency]
    await _generate_report(
        db,
        report_type=schedule.type,
        portfolio=portfolio,
        start=due_at - delta,
        end=due_at,
        report_format=schedule.format,
        actor_id=schedule.user_id,
        schedule=schedule,
    )
    schedule.last_run_at = due_at
    schedule.next_run_at = due_at + delta
    await db.commit()


async def process_due_schedules(sessionmaker) -> int:
    """One generation per due schedule (active, next_run_at <= sim now).

    Returns the number of reports generated. Each schedule is its own
    transaction: a bad schedule is rolled back, logged and skipped.
    """
    async with sessionmaker() as session:
        now = get_sim_now() or utcnow()
        due = (
            (
                await session.execute(
                    select(ReportSchedule)
                    .where(
                        ReportSchedule.active.is_(True),
                        ReportSchedule.next_run_at <= now,
                    )
                    .order_by(ReportSchedule.next_run_at)
                )
            )
            .scalars()
            .all()
        )
        generated = 0
        for schedule in due:
            try:
                await _run_schedule(session, schedule)
                generated += 1
            except Exception:
                await session.rollback()
                logger.exception(
                    "report scheduler: schedule %s failed", schedule.schedule_id
                )
        return generated


async def _shielded(coro):
    """Run one DB unit-of-work shielded from task cancellation (cancelling a
    task mid-aiosqlite-call can wedge the connection and hang app shutdown)."""
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


async def report_scheduler(bus, sessionmaker) -> None:
    """Worker: sweep due report schedules every SCHEDULE_SWEEP_SECONDS.

    Sleeps before the first sweep so app startup and short-lived processes
    never see sweep DB traffic at t=0 (same idiom as the JIT expiry sweep).
    """
    while True:
        await asyncio.sleep(SCHEDULE_SWEEP_SECONDS)
        try:
            await _shielded(process_due_schedules(sessionmaker))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("report scheduler sweep failed; retrying next interval")


def get_workers(settings):
    """Worker contract: callables fn(bus, sessionmaker) -> coroutine."""
    return [report_scheduler]
