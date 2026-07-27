import { useCallback, useMemo, useState } from "react";
import * as echarts from "echarts";
import { api } from "../api/client";
import type { ListResponse, PaperAccount, PaperAccountCreated, Portfolio } from "../api/types";
import { EChart } from "../components/EChart";
import { categoryAxis, CHART_COLORS, tooltipBase, valueAxis } from "../components/chartTheme";
import { StatCard } from "../components/StatCard";
import { useToast } from "../components/Toast";
import { fmtJpy, fmtNum, fmtPct, fmtSignedJpy, pnlClass } from "../format";
import { usePoll } from "../hooks";

export function Paper() {
  const { toast } = useToast();
  const [paperPortfolios, setPaperPortfolios] = useState<Portfolio[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [initialCash, setInitialCash] = useState("10000000");
  const [busy, setBusy] = useState(false);

  const loadList = useCallback(async () => {
    const res = await api<ListResponse<Portfolio>>("/portfolios");
    const papers = res.items.filter((p) => p.type === "PAPER");
    setPaperPortfolios(papers);
    setSelectedId((cur) => cur || papers[0]?.portfolio_id || "");
  }, []);

  usePoll(
    () => {
      void loadList();
    },
    10_000,
    [loadList],
  );

  const loadAccount = useCallback(async () => {
    if (!selectedId) {
      setAccount(null);
      return;
    }
    try {
      setAccount(await api<PaperAccount>(`/paper/accounts/${selectedId}`));
    } catch {
      setAccount(null);
    }
  }, [selectedId]);

  usePoll(
    () => {
      void loadAccount();
    },
    5_000,
    [loadAccount],
  );

  const create = async () => {
    const cash = Number(initialCash);
    setBusy(true);
    try {
      const created = await api<PaperAccountCreated>("/paper/accounts", {
        method: "POST",
        body: initialCash === "" || Number.isNaN(cash) ? {} : { initial_cash: cash },
      });
      toast(`Paper account ${created.name} created`, "success");
      setSelectedId(created.portfolio_id);
      await loadList();
    } catch {
      // toast raised by client
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!account) return;
    if (!window.confirm(`Reset paper account ${account.name}? All positions and history are cleared.`)) {
      return;
    }
    setBusy(true);
    try {
      await api(`/paper/accounts/${account.portfolio_id}/reset`, { method: "POST" });
      toast("Paper account reset", "success");
      await loadAccount();
    } catch {
      // toast raised by client
    } finally {
      setBusy(false);
    }
  };

  const equityOption: echarts.EChartsOption = useMemo(() => {
    const curve = account?.equity_curve ?? [];
    return {
      backgroundColor: "transparent",
      tooltip: { ...tooltipBase, trigger: "axis", valueFormatter: (v) => fmtJpy(Number(v)) },
      grid: { left: 70, right: 16, top: 16, bottom: 28 },
      xAxis: { ...categoryAxis(), type: "category", data: curve.map((p) => p.ts.slice(0, 10)) },
      yAxis: { ...valueAxis(), type: "value" },
      series: [
        {
          type: "line",
          data: curve.map((p) => p.value),
          showSymbol: false,
          lineStyle: { color: CHART_COLORS.up, width: 1.5 },
          areaStyle: { color: "rgba(63,185,80,0.10)" },
        },
      ],
    };
  }, [account]);

  const stats = account?.statistics ?? null;
  const pnl = account !== null ? account.cash_balance - account.initial_balance : null;

  return (
    <div className="page">
      <div className="page-header">
        <h2>Paper Trading</h2>
        {paperPortfolios.length > 1 && (
          <select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
            {paperPortfolios.map((p) => (
              <option key={p.portfolio_id} value={p.portfolio_id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <section className="panel">
        <div className="filter-bar">
          <label>
            Initial cash (JPY)
            <input
              type="number"
              min="0"
              value={initialCash}
              onChange={(e) => setInitialCash(e.target.value)}
            />
          </label>
          <button className="btn btn-buy active btn-sm filter-submit" disabled={busy} onClick={() => void create()}>
            Create paper account
          </button>
          {account && (
            <button className="btn btn-danger btn-sm filter-submit" disabled={busy} onClick={() => void reset()}>
              Reset account
            </button>
          )}
        </div>
        <p className="muted panel-note">
          Paper accounts trade through the same order pipeline — use the order panel on the
          Trading workspace and pick the PAPER portfolio there.
        </p>
      </section>

      {!account ? (
        <section className="panel panel-empty muted">
          {paperPortfolios.length === 0
            ? "No paper account yet — create one above."
            : "Loading account…"}
        </section>
      ) : (
        <>
          <div className="stat-row">
            <StatCard label="Cash balance" value={fmtJpy(account.cash_balance)} />
            <StatCard label="Initial balance" value={fmtJpy(account.initial_balance)} />
            <StatCard
              label="Cash P&L vs start"
              value={fmtSignedJpy(pnl)}
              tone={pnlClass(pnl)}
            />
            <StatCard label="Trades" value={stats ? fmtNum(stats.trades) : "—"} />
            <StatCard
              label="Win rate"
              value={stats ? fmtPct(stats.win_rate) : "—"}
            />
            <StatCard
              label="Avg P&L / trade"
              value={stats ? fmtSignedJpy(stats.avg_pnl_per_trade) : "—"}
              tone={stats ? pnlClass(stats.avg_pnl_per_trade) : ""}
            />
            <StatCard
              label="Max drawdown"
              value={stats ? fmtSignedJpy(stats.max_drawdown) : "—"}
              tone={stats ? pnlClass(stats.max_drawdown) : ""}
            />
          </div>
          {stats === null && (
            <p className="muted">Statistics appear after the first closed trades.</p>
          )}

          <section className="panel">
            <div className="panel-header">
              <h3>Equity curve</h3>
            </div>
            {(account.equity_curve ?? []).length === 0 ? (
              <div className="panel-empty muted">No equity history yet.</div>
            ) : (
              <EChart option={equityOption} height={300} />
            )}
          </section>
        </>
      )}
    </div>
  );
}
