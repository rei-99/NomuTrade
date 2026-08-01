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
import { useT } from "../i18n";

export function Paper() {
  const { toast } = useToast();
  const { t } = useT();
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
      toast(t("paper.created", { name: created.name }), "success");
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
    if (!window.confirm(t("paper.resetConfirm", { name: account.name }))) {
      return;
    }
    setBusy(true);
    try {
      await api(`/paper/accounts/${account.portfolio_id}/reset`, { method: "POST" });
      toast(t("paper.resetDone"), "success");
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
        <h2>{t("paper.title")}</h2>
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
            {t("paper.initialCash")}
            <input
              type="number"
              min="0"
              value={initialCash}
              onChange={(e) => setInitialCash(e.target.value)}
            />
          </label>
          <button className="btn btn-buy active btn-sm filter-submit" disabled={busy} onClick={() => void create()}>
            {t("paper.create")}
          </button>
          {account && (
            <button className="btn btn-danger btn-sm filter-submit" disabled={busy} onClick={() => void reset()}>
              {t("paper.reset")}
            </button>
          )}
        </div>
        <p className="muted panel-note">
          {t("paper.note")}
        </p>
      </section>

      {!account ? (
        <section className="panel panel-empty muted">
          {paperPortfolios.length === 0
            ? t("paper.none")
            : t("paper.loadingAccount")}
        </section>
      ) : (
        <>
          <div className="stat-row">
            <StatCard label={t("paper.cashBalance")} value={fmtJpy(account.cash_balance)} />
            <StatCard label={t("paper.initialBalance")} value={fmtJpy(account.initial_balance)} />
            <StatCard
              label={t("paper.pnlVsStart")}
              value={fmtSignedJpy(pnl)}
              tone={pnlClass(pnl)}
            />
            <StatCard label={t("paper.trades")} value={stats ? fmtNum(stats.trades) : "—"} />
            <StatCard
              label={t("paper.winRate")}
              value={stats ? fmtPct(stats.win_rate) : "—"}
            />
            <StatCard
              label={t("paper.avgPnl")}
              value={stats ? fmtSignedJpy(stats.avg_pnl_per_trade) : "—"}
              tone={stats ? pnlClass(stats.avg_pnl_per_trade) : ""}
            />
            <StatCard
              label={t("paper.maxDrawdown")}
              value={stats ? fmtSignedJpy(stats.max_drawdown) : "—"}
              tone={stats ? pnlClass(stats.max_drawdown) : ""}
            />
          </div>
          {stats === null && (
            <p className="muted">{t("paper.statsNote")}</p>
          )}

          <section className="panel">
            <div className="panel-header">
              <h3>{t("paper.equityCurve")}</h3>
            </div>
            {(account.equity_curve ?? []).length === 0 ? (
              <div className="panel-empty muted">{t("paper.noEquity")}</div>
            ) : (
              <EChart option={equityOption} height={300} />
            )}
          </section>
        </>
      )}
    </div>
  );
}
