import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { PositionsResponse } from "../api/types";
import { DataTable } from "./DataTable";
import { fmtJpy, fmtNum, fmtPct, fmtSignedJpy, pnlClass } from "../format";

interface PositionsTableProps {
  portfolioId: string;
  positions: PositionsResponse;
}

/**
 * Live positions blotter. MARK cell flashes green/red for 300 ms on price
 * changes between polls; uP&L renders as colored chips; a subtle allocation
 * bar sits under each market value; totals pinned in a footer row. Row click
 * navigates to the portfolio.
 */
export function PositionsTable({ portfolioId, positions }: PositionsTableProps) {
  const navigate = useNavigate();
  const prevMarks = useRef<Map<string, number>>(new Map());
  const [flash, setFlash] = useState<Map<string, "up" | "down">>(new Map());

  // Reset the mark memory when the portfolio changes to avoid false flashes.
  useEffect(() => {
    prevMarks.current = new Map();
    setFlash(new Map());
  }, [portfolioId]);

  useEffect(() => {
    const items = positions.items;
    const next = new Map<string, "up" | "down">();
    for (const p of items) {
      const prev = prevMarks.current.get(p.instrument_symbol);
      if (prev !== undefined && prev !== p.latest_price) {
        next.set(p.instrument_symbol, p.latest_price > prev ? "up" : "down");
      }
      prevMarks.current.set(p.instrument_symbol, p.latest_price);
    }
    if (next.size === 0) return;
    setFlash(next);
    const t = window.setTimeout(() => setFlash(new Map()), 300);
    return () => window.clearTimeout(t);
  }, [positions]);

  const totalMv = positions.totals.market_value;
  const totalCost = positions.items.reduce((a, p) => a + p.quantity * p.avg_cost, 0);
  const totalUpnlPct =
    totalCost > 0 ? (positions.totals.unrealized_pnl / totalCost) * 100 : null;
  const dayChanges = positions.items
    .map((p) => p.day_change)
    .filter((v): v is number => v !== null);
  const totalDayChange =
    dayChanges.length > 0 ? dayChanges.reduce((a, v) => a + v, 0) : null;

  return (
    <DataTable
      rows={positions.items}
      keyFn={(p) => p.instrument_symbol}
      empty="No open positions"
      onRowClick={() => navigate(`/portfolios/${portfolioId}`)}
      columns={[
        { header: "Symbol", render: (p) => p.instrument_symbol },
        { header: "Name", render: (p) => p.name },
        { header: "Qty", className: "num", render: (p) => fmtNum(p.quantity) },
        { header: "Avg cost", className: "num", render: (p) => fmtJpy(p.avg_cost, true) },
        {
          header: "Mark",
          className: "num",
          render: (p) => {
            const dir = flash.get(p.instrument_symbol);
            return (
              <span className={`mark-cell${dir ? ` flash-${dir}` : ""}`}>
                {fmtJpy(p.latest_price, true)}{" "}
                {p.stale_price && <span className="badge badge-amber">STALE</span>}
              </span>
            );
          },
        },
        {
          header: "Day chg",
          className: "num",
          render: (p) => {
            if (p.day_change === null) return <span className="muted">—</span>;
            const pct = p.day_change_pct;
            return (
              <span className={`pnl-chip num ${pnlClass(p.day_change)}`}>
                {fmtSignedJpy(p.day_change)}
                {pct !== null && ` (${pct >= 0 ? "+" : ""}${fmtNum(pct, 1)}%)`}
              </span>
            );
          },
        },
        {
          header: "Mkt value",
          className: "num",
          render: (p) => {
            const share = totalMv > 0 ? (p.market_value / totalMv) * 100 : 0;
            return (
              <span className="mv-cell">
                <span>{fmtJpy(p.market_value)}</span>
                <span className="alloc-bar" title={`${fmtPct(share)} of book`}>
                  <span
                    className="alloc-bar-fill"
                    style={{ width: `${Math.min(100, Math.max(0, share))}%`, display: "block" }}
                  />
                </span>
              </span>
            );
          },
        },
        {
          header: "uP&L",
          className: "num",
          render: (p) => (
            <span className={`pnl-chip num ${pnlClass(p.unrealized_pnl)}`}>
              {fmtSignedJpy(p.unrealized_pnl)}
            </span>
          ),
        },
        {
          header: "uP&L %",
          className: "num",
          render: (p) => {
            const cost = p.quantity * p.avg_cost;
            const pct = cost > 0 ? (p.unrealized_pnl / cost) * 100 : null;
            return (
              <span className={`pnl-chip num ${pnlClass(pct)}`}>
                {pct === null ? "—" : `${pct >= 0 ? "+" : ""}${fmtNum(pct, 1)}%`}
              </span>
            );
          },
        },
      ]}
      footer={[
        <span key="t" className="muted">
          Totals
        </span>,
        "",
        "",
        "",
        "",
        totalDayChange === null ? (
          <span key="dc" className="muted">
            —
          </span>
        ) : (
          <span key="dc" className={`pnl-chip num ${pnlClass(totalDayChange)}`}>
            {fmtSignedJpy(totalDayChange)}
          </span>
        ),
        <span key="mv" className="num">
          {fmtJpy(totalMv)}
        </span>,
        <span key="up" className={`pnl-chip num ${pnlClass(positions.totals.unrealized_pnl)}`}>
          {fmtSignedJpy(positions.totals.unrealized_pnl)}
        </span>,
        <span key="upp" className={`pnl-chip num ${pnlClass(totalUpnlPct)}`}>
          {totalUpnlPct === null ? "—" : `${totalUpnlPct >= 0 ? "+" : ""}${fmtNum(totalUpnlPct, 1)}%`}
        </span>,
      ]}
    />
  );
}
