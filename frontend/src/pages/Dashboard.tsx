import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import * as echarts from "echarts";
import { api } from "../api/client";
import type {
  Instrument,
  ListResponse,
  Portfolio,
  PositionsResponse,
  Trade,
  Valuation,
} from "../api/types";
import { EChart } from "../components/EChart";
import { CHART_COLORS, tooltipBase } from "../components/chartTheme";
import { OrderTicket } from "../components/OrderTicket";
import type { TicketPrefill } from "../components/OrderTicket";
import { StatCard } from "../components/StatCard";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { fmtJpy, fmtNum, fmtSignedJpy, fmtTs, pnlClass } from "../format";
import { usePoll } from "../hooks";

const POLL_MS = 5_000;
const PIE_COLORS = ["#58a6ff", "#3fb950", "#d29922", "#f778ba", "#f85149", "#76e3ea", "#d2a8ff"];

interface Aggregates {
  totalValue: number;
  dayChange: number;
  cash: number;
  positions: number;
  allocation: { asset_class: string; value: number }[];
}

export function Dashboard() {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [valuations, setValuations] = useState<Valuation[]>([]);
  const [positionCounts, setPositionCounts] = useState<number>(0);
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [ticket, setTicket] = useState<TicketPrefill | null>(null);

  const load = useCallback(async () => {
    // Portfolios + instruments first; per-portfolio calls are isolated so one
    // failure doesn't blank the whole dashboard.
    const [pfRes, instRes] = await Promise.all([
      api<ListResponse<Portfolio>>("/portfolios"),
      api<ListResponse<Instrument>>("/instruments"),
    ]);
    const pfs = pfRes.items;
    setPortfolios(pfs);
    setInstruments(instRes.items);

    const valResults = await Promise.allSettled(
      pfs.map((p) => api<Valuation>(`/portfolios/${p.portfolio_id}/valuation`)),
    );
    setValuations(
      valResults
        .filter((r): r is PromiseFulfilledResult<Valuation> => r.status === "fulfilled")
        .map((r) => r.value),
    );

    const posResults = await Promise.allSettled(
      pfs.map((p) => api<PositionsResponse>(`/portfolios/${p.portfolio_id}/positions`)),
    );
    setPositionCounts(
      posResults
        .filter((r): r is PromiseFulfilledResult<PositionsResponse> => r.status === "fulfilled")
        .reduce((acc, r) => acc + r.value.items.length, 0),
    );

    const tradeResults = await Promise.allSettled(
      pfs.map((p) =>
        api<ListResponse<Trade>>("/trades", { params: { portfolio_id: p.portfolio_id } }),
      ),
    );
    const merged = tradeResults
      .filter((r): r is PromiseFulfilledResult<ListResponse<Trade>> => r.status === "fulfilled")
      .flatMap((r) => r.value.items)
      .sort((a, b) => b.executed_at.localeCompare(a.executed_at))
      .slice(0, 10);
    setTrades(merged);
  }, []);

  usePoll(
    () => {
      void load();
    },
    POLL_MS,
    [load],
  );

  const agg: Aggregates = useMemo(() => {
    const allocMap = new Map<string, number>();
    let totalValue = 0;
    let dayChange = 0;
    let cash = 0;
    for (const v of valuations) {
      totalValue += v.total_value;
      dayChange += v.day_change;
      cash += v.cash;
      for (const a of v.kpis.allocation) {
        allocMap.set(a.asset_class, (allocMap.get(a.asset_class) ?? 0) + a.value);
      }
    }
    return {
      totalValue,
      dayChange,
      cash,
      positions: positionCounts,
      allocation: [...allocMap.entries()]
        .map(([asset_class, value]) => ({ asset_class, value }))
        .sort((a, b) => b.value - a.value),
    };
  }, [valuations, positionCounts]);

  const pieOption: echarts.EChartsOption = useMemo(
    () => ({
      backgroundColor: "transparent",
      tooltip: { ...tooltipBase, trigger: "item", valueFormatter: (v) => fmtJpy(Number(v)) },
      legend: {
        bottom: 0,
        textStyle: { color: CHART_COLORS.text, fontSize: 11 },
        itemWidth: 10,
        itemHeight: 10,
      },
      series: [
        {
          type: "pie",
          radius: ["42%", "68%"],
          center: ["50%", "44%"],
          data: agg.allocation.map((a, i) => ({
            name: a.asset_class,
            value: a.value,
            itemStyle: { color: PIE_COLORS[i % PIE_COLORS.length] },
          })),
          label: { color: CHART_COLORS.text, fontSize: 11, formatter: "{d}%" },
        },
      ],
    }),
    [agg.allocation],
  );

  return (
    <div className="page">
      <div className="page-header">
        <h2>Dashboard</h2>
      </div>

      <div className="stat-row">
        <StatCard label="Total value" value={fmtJpy(agg.totalValue)} />
        <StatCard
          label="Day change"
          value={fmtSignedJpy(agg.dayChange)}
          tone={pnlClass(agg.dayChange)}
        />
        <StatCard label="Cash" value={fmtJpy(agg.cash)} />
        <StatCard label="Open positions" value={fmtNum(agg.positions)} />
      </div>

      <div className="grid-2">
        <section className="panel">
          <div className="panel-header">
            <h3>Allocation</h3>
            <span className="muted">across {valuations.length} portfolio(s)</span>
          </div>
          {agg.allocation.length === 0 ? (
            <div className="panel-empty muted">No holdings yet.</div>
          ) : (
            <EChart option={pieOption} height={260} />
          )}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h3>Recent executions</h3>
          </div>
          <DataTable<Trade>
            rows={trades}
            keyFn={(t) => t.execution_id}
            empty="No executions yet"
            columns={[
              { header: "Time", render: (t) => <span className="num">{fmtTs(t.executed_at)}</span> },
              { header: "Symbol", render: (t) => t.instrument_symbol },
              { header: "Side", render: (t) => <Badge text={t.side} /> },
              { header: "Qty", className: "num", render: (t) => fmtNum(t.quantity) },
              { header: "Price", className: "num", render: (t) => fmtJpy(t.price, true) },
            ]}
          />
        </section>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h3>Watchlist</h3>
          <span className="muted">click a symbol for the chart</span>
        </div>
        <DataTable<Instrument>
          rows={instruments}
          keyFn={(i) => i.instrument_id}
          empty="No instruments available"
          columns={[
            {
              header: "Symbol",
              render: (i) => <Link to={`/charts/${i.symbol}`} className="link-strong">{i.symbol}</Link>,
            },
            { header: "Name", render: (i) => i.name },
            { header: "Asset class", render: (i) => <Badge text={i.asset_class} /> },
            {
              header: "Latest",
              className: "num",
              render: (i) => fmtJpy(i.latest_price, true),
            },
            {
              header: "",
              render: (i) => (
                <span className="row-actions">
                  <button
                    className="btn btn-buy btn-sm"
                    disabled={!i.tradable || portfolios.length === 0}
                    onClick={() => setTicket({ instrument: i.symbol, side: "BUY" })}
                  >
                    BUY
                  </button>
                  <button
                    className="btn btn-sell btn-sm"
                    disabled={!i.tradable || portfolios.length === 0}
                    onClick={() => setTicket({ instrument: i.symbol, side: "SELL" })}
                  >
                    SELL
                  </button>
                </span>
              ),
            },
          ]}
        />
      </section>

      {portfolios.length === 0 && (
        <div className="panel panel-empty muted">
          No portfolios visible for this user. Trading actions are disabled.
        </div>
      )}

      {ticket && (
        <OrderTicket
          prefill={ticket}
          portfolios={portfolios}
          onClose={() => setTicket(null)}
          onSubmitted={() => void load()}
        />
      )}
    </div>
  );
}
