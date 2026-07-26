import { useCallback, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import * as echarts from "echarts";
import { api } from "../api/client";
import type {
  ListResponse,
  PerformanceSeries,
  Portfolio,
  PositionsResponse,
  Timeframe,
  Transaction,
  Valuation,
} from "../api/types";
import { TIMEFRAMES } from "../api/types";
import { EChart } from "../components/EChart";
import { categoryAxis, CHART_COLORS, tooltipBase, valueAxis } from "../components/chartTheme";
import { StatCard } from "../components/StatCard";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { fmtJpy, fmtNum, fmtPct, fmtSignedJpy, fmtTs, pnlClass } from "../format";
import { usePoll } from "../hooks";

type Tab = "positions" | "transactions" | "performance";

export function PortfolioDetail() {
  const { id = "" } = useParams();
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [valuation, setValuation] = useState<Valuation | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [perf, setPerf] = useState<PerformanceSeries | null>(null);
  const [perfTf, setPerfTf] = useState<Timeframe>("1M");
  const [tab, setTab] = useState<Tab>("positions");

  // transaction filters + pagination
  const [txFrom, setTxFrom] = useState("");
  const [txTo, setTxTo] = useState("");
  const [txSymbol, setTxSymbol] = useState("");
  const [txSide, setTxSide] = useState("");
  const [txItems, setTxItems] = useState<Transaction[]>([]);
  const [txCursor, setTxCursor] = useState<string | null>(null);

  usePoll(
    () => {
      void (async () => {
        const [pfs, val, pos] = await Promise.all([
          api<ListResponse<Portfolio>>("/portfolios"),
          api<Valuation>(`/portfolios/${id}/valuation`),
          api<PositionsResponse>(`/portfolios/${id}/positions`),
        ]);
        setPortfolio(pfs.items.find((p) => p.portfolio_id === id) ?? null);
        setValuation(val);
        setPositions(pos);
      })();
    },
    5_000,
    [id],
  );

  const loadPerformance = useCallback(async () => {
    const res = await api<PerformanceSeries>(`/portfolios/${id}/performance`, {
      params: { timeframe: perfTf },
    });
    setPerf(res);
  }, [id, perfTf]);

  usePoll(
    () => {
      if (tab === "performance") void loadPerformance();
    },
    0,
    [loadPerformance, tab],
  );

  const loadTransactions = useCallback(
    async (cursor: string | null, append: boolean) => {
      const res = await api<ListResponse<Transaction>>(`/portfolios/${id}/transactions`, {
        params: {
          from: txFrom ? new Date(txFrom).toISOString() : undefined,
          to: txTo ? new Date(txTo).toISOString() : undefined,
          symbol: txSymbol || undefined,
          side: txSide || undefined,
          cursor: cursor ?? undefined,
        },
      });
      setTxItems((prev) => (append ? [...prev, ...res.items] : res.items));
      setTxCursor(res.next_cursor);
    },
    [id, txFrom, txTo, txSymbol, txSide],
  );

  usePoll(
    () => {
      if (tab === "transactions") void loadTransactions(null, false);
    },
    0,
    [loadTransactions, tab],
  );

  const perfOption: echarts.EChartsOption = useMemo(() => {
    const series = perf?.series ?? [];
    return {
      backgroundColor: "transparent",
      tooltip: {
        ...tooltipBase,
        trigger: "axis",
        valueFormatter: (v) => fmtJpy(Number(v)),
      },
      grid: { left: 70, right: 16, top: 16, bottom: 28 },
      xAxis: {
        ...categoryAxis(),
        type: "category",
        data: series.map((p) => p.ts.slice(0, 10)),
      },
      yAxis: { ...valueAxis(), type: "value" },
      series: [
        {
          type: "line",
          data: series.map((p) => p.total_value),
          showSymbol: false,
          lineStyle: { color: CHART_COLORS.accent, width: 1.5 },
          areaStyle: { color: "rgba(88,166,255,0.12)" },
        },
      ],
    };
  }, [perf]);

  if (!portfolio && !valuation) {
    return (
      <div className="page">
        <div className="muted">Loading portfolio…</div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <h2>
          {portfolio?.name ?? "Portfolio"}{" "}
          {portfolio && <Badge text={portfolio.type} />}
        </h2>
        {positions && <span className="muted num">as of {fmtTs(positions.as_of)}</span>}
      </div>

      <div className="stat-row">
        <StatCard label="Total value" value={fmtJpy(valuation?.total_value)} />
        <StatCard
          label="Day change"
          value={fmtSignedJpy(valuation?.day_change)}
          tone={pnlClass(valuation?.day_change)}
        />
        <StatCard label="Cash" value={fmtJpy(valuation?.cash)} />
        <StatCard label="Market value" value={fmtJpy(valuation?.market_value)} />
        <StatCard
          label="Unrealized P&L"
          value={fmtSignedJpy(valuation?.unrealized_pnl)}
          tone={pnlClass(valuation?.unrealized_pnl)}
        />
        <StatCard
          label="Realized P&L"
          value={fmtSignedJpy(valuation?.realized_pnl)}
          tone={pnlClass(valuation?.realized_pnl)}
        />
      </div>

      {valuation && (
        <div className="kpi-row panel">
          <div className="kpi">
            <span className="muted">Concentration</span>
            <span className="num">{fmtPct(valuation.kpis.concentration_pct)}</span>
          </div>
          <div className="kpi">
            <span className="muted">Volatility (ann.)</span>
            <span className="num">
              {valuation.kpis.volatility_annualized_pct === null
                ? "—"
                : fmtPct(valuation.kpis.volatility_annualized_pct)}
            </span>
          </div>
          <div className="kpi kpi-wide">
            <span className="muted">Top holdings</span>
            <span className="num">
              {valuation.kpis.top_holdings.length === 0
                ? "—"
                : valuation.kpis.top_holdings
                    .map((h) => `${h.instrument_symbol} ${fmtPct(h.pct)}`)
                    .join(" · ")}
            </span>
          </div>
        </div>
      )}

      <div className="tabs">
        {(["positions", "transactions", "performance"] as Tab[]).map((t) => (
          <button
            key={t}
            className={`tab${tab === t ? " active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === "positions" && (
        <section className="panel">
          <DataTable
            rows={positions?.items ?? []}
            keyFn={(p) => p.instrument_symbol}
            empty="No open positions"
            columns={[
              { header: "Symbol", render: (p) => p.instrument_symbol },
              { header: "Name", render: (p) => p.name },
              { header: "Asset class", render: (p) => <Badge text={p.asset_class} /> },
              { header: "Qty", className: "num", render: (p) => fmtNum(p.quantity) },
              { header: "Avg cost", className: "num", render: (p) => fmtJpy(p.avg_cost, true) },
              {
                header: "Latest",
                className: "num",
                render: (p) => (
                  <span>
                    {fmtJpy(p.latest_price, true)}{" "}
                    {p.stale_price && <span className="badge badge-amber">STALE</span>}
                  </span>
                ),
              },
              { header: "Mkt value", className: "num", render: (p) => fmtJpy(p.market_value) },
              {
                header: "Unreal. P&L",
                className: "num",
                render: (p) => (
                  <span className={pnlClass(p.unrealized_pnl)}>{fmtSignedJpy(p.unrealized_pnl)}</span>
                ),
              },
            ]}
          />
          {positions && positions.items.length > 0 && (
            <div className="table-footer num">
              Totals — market value {fmtJpy(positions.totals.market_value)} · unrealized P&L{" "}
              <span className={pnlClass(positions.totals.unrealized_pnl)}>
                {fmtSignedJpy(positions.totals.unrealized_pnl)}
              </span>
            </div>
          )}
        </section>
      )}

      {tab === "transactions" && (
        <section className="panel">
          <div className="filter-bar">
            <label>
              From
              <input type="date" value={txFrom} onChange={(e) => setTxFrom(e.target.value)} />
            </label>
            <label>
              To
              <input type="date" value={txTo} onChange={(e) => setTxTo(e.target.value)} />
            </label>
            <label>
              Symbol
              <input
                type="text"
                value={txSymbol}
                onChange={(e) => setTxSymbol(e.target.value.toUpperCase())}
                placeholder="e.g. 7203"
              />
            </label>
            <label>
              Side
              <select value={txSide} onChange={(e) => setTxSide(e.target.value)}>
                <option value="">All</option>
                <option value="BUY">BUY</option>
                <option value="SELL">SELL</option>
              </select>
            </label>
          </div>
          <DataTable<Transaction>
            rows={txItems}
            keyFn={(t) => `${t.ref_id}-${t.ts}`}
            empty="No transactions in range"
            columns={[
              { header: "Time", render: (t) => <span className="num">{fmtTs(t.ts)}</span> },
              { header: "Kind", render: (t) => <Badge text={t.kind} /> },
              { header: "Symbol", render: (t) => t.instrument_symbol },
              { header: "Side", render: (t) => <Badge text={t.side} /> },
              { header: "Qty", className: "num", render: (t) => fmtNum(t.quantity) },
              { header: "Price", className: "num", render: (t) => fmtJpy(t.price, true) },
              { header: "Amount", className: "num", render: (t) => fmtJpy(t.amount) },
            ]}
          />
          {txCursor && (
            <div className="table-footer">
              <button className="btn btn-ghost btn-sm" onClick={() => void loadTransactions(txCursor, true)}>
                Load more
              </button>
            </div>
          )}
        </section>
      )}

      {tab === "performance" && (
        <section className="panel">
          <div className="panel-header">
            <h3>Total value</h3>
            <div className="tf-selector">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  className={`btn btn-ghost btn-sm${perfTf === tf ? " active" : ""}`}
                  onClick={() => setPerfTf(tf)}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>
          {(perf?.series ?? []).length === 0 ? (
            <div className="panel-empty muted">No performance data for this timeframe.</div>
          ) : (
            <EChart option={perfOption} height={340} />
          )}
        </section>
      )}
    </div>
  );
}
