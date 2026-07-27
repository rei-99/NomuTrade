import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { PositionsResponse } from "../api/types";
import { DataTable } from "./DataTable";
import { fmtJpy, fmtNum, fmtPct, fmtSignedJpy, pnlClass } from "../format";

interface PositionsTableProps {
  portfolioId: string;
  positions: PositionsResponse | null;
}

/**
 * Live positions table. The MARK cell flashes green/red for 300 ms when the
 * latest price changes between polls. Row click navigates to the portfolio.
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
    const items = positions?.items ?? [];
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

  return (
    <DataTable
      rows={positions?.items ?? []}
      keyFn={(p) => p.instrument_symbol}
      empty="No open positions"
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
        { header: "Mkt value", className: "num", render: (p) => fmtJpy(p.market_value) },
        {
          header: "uP&L",
          className: "num",
          render: (p) => (
            <span className={pnlClass(p.unrealized_pnl)}>{fmtSignedJpy(p.unrealized_pnl)}</span>
          ),
        },
        {
          header: "uP&L %",
          className: "num",
          render: (p) => {
            const cost = p.quantity * p.avg_cost;
            const pct = cost > 0 ? (p.unrealized_pnl / cost) * 100 : null;
            return <span className={pnlClass(pct)}>{fmtPct(pct)}</span>;
          },
        },
      ]}
      onRowClick={() => navigate(`/portfolios/${portfolioId}`)}
    />
  );
}
