import type { CSSProperties } from "react";
import type { Valuation } from "../api/types";
import { fmtJpy, fmtPct, fmtSignedJpy, pnlClass } from "../format";
import { useT } from "../i18n";

type Tone = "green" | "amber" | "red";

function concentrationTone(pct: number): Tone {
  if (pct > 40) return "red";
  if (pct > 25) return "amber";
  return "green";
}

function volatilityTone(pct: number): Tone {
  if (pct > 60) return "red";
  if (pct > 40) return "amber";
  return "green";
}

/** Pure-CSS conic-gradient ring gauge with the value centered. */
function Donut({ pct, tone, label, display }: { pct: number | null; tone: Tone | "na"; label: string; display: string }) {
  const clamped = pct === null ? 0 : Math.min(100, Math.max(0, pct));
  const style = { "--p": String(clamped) } as CSSProperties;
  return (
    <div className="donut-wrap">
      <div className={`donut donut-${tone}`} style={style}>
        <span className="donut-value num">{display}</span>
      </div>
      <div className="donut-label">{label}</div>
    </div>
  );
}

interface RiskPanelProps {
  valuation: Valuation | null;
}

/** Risk exposure: donut gauges (concentration, volatility) with threshold
 * coloring; VaR/ES/Sharpe/drawdown stat tiles; conditional bond-book line
 * (weighted YTM + modified duration); asset-mix, top-holdings and
 * invested-vs-cash bars. */
export function RiskPanel({ valuation }: RiskPanelProps) {
  const { t } = useT();
  if (!valuation) {
    return (
      <section className="panel risk-panel">
        <div className="panel-header">
          <h3>{t("risk.title")}</h3>
        </div>
        <div className="skeleton" style={{ height: 96 }} />
      </section>
    );
  }

  const { kpis } = valuation;
  const concentration = kpis.concentration_pct;
  const volatility = kpis.volatility_annualized_pct;
  const total = valuation.market_value + valuation.cash;
  const investedPct = total > 0 ? (valuation.market_value / total) * 100 : 0;
  const top3 = kpis.top_holdings.slice(0, 3);

  // Extended metrics (new kpis contract): VaR, ES, Sharpe, max drawdown, day P&L.
  const varPct = kpis.var_95_1d_pct;
  const varTone = varPct === null ? "" : varPct > 10 ? "neg" : varPct > 5 ? "warn" : "";
  const esPct = kpis.es_95_1d_pct;
  const esTone = esPct === null ? "" : esPct > 12 ? "neg" : esPct > 6 ? "warn" : "";
  const sharpe = kpis.sharpe_ratio;
  const sharpeTone = sharpe === null ? "" : sharpe < 0 ? "neg" : sharpe < 0.5 ? "warn" : "pos";
  const ddPct = kpis.max_drawdown_pct;
  const ddTone = ddPct === null ? "" : ddPct > 20 ? "neg" : ddPct > 10 ? "warn" : "";
  const dayPnl = valuation.day_change;
  const dayPnlPct =
    valuation.total_value > 0 ? (dayPnl / valuation.total_value) * 100 : null;

  // Asset mix (EQUITY vs BOND), normalized within the two classes.
  const eqAlloc = kpis.allocation.find((a) => a.asset_class === "EQUITY")?.pct ?? 0;
  const bondAlloc = kpis.allocation.find((a) => a.asset_class === "BOND")?.pct ?? 0;
  const mixTotal = eqAlloc + bondAlloc;
  const eqShare = mixTotal > 0 ? (eqAlloc / mixTotal) * 100 : 0;

  return (
    <section className="panel risk-panel">
      <div className="panel-header">
        <h3>{t("risk.title")}</h3>
      </div>

      <div className="panel-scroll">
      <div className="donut-row">
        <Donut
          pct={concentration}
          tone={concentrationTone(concentration)}
          label={t("risk.concentration")}
          display={fmtPct(concentration, 0)}
        />
        <Donut
          pct={volatility}
          tone={volatility === null ? "na" : volatilityTone(volatility)}
          label={t("risk.volatility")}
          display={volatility === null ? "N/A" : fmtPct(volatility, 2)}
        />
      </div>

      <div className="risk-stats">
        <div className="risk-stat">
          <span className="muted">{t("risk.var")}</span>
          <span className={`num ${varTone}`}>{varPct === null ? "N/A" : fmtPct(varPct, 3)}</span>
          <span className="muted risk-stat-caption">
            {varPct === null ? " " : t("risk.varCaption")}
          </span>
        </div>
        <div className="risk-stat">
          <span className="muted">{t("risk.es")}</span>
          <span className={`num ${esTone}`}>{esPct === null ? "N/A" : fmtPct(esPct, 3)}</span>
          <span className="muted risk-stat-caption">
            {esPct === null ? " " : t("risk.esCaption")}
          </span>
        </div>
        <div className="risk-stat">
          <span className="muted">{t("risk.sharpe")}</span>
          <span className={`num ${sharpeTone}`}>{sharpe === null ? "N/A" : sharpe.toFixed(2)}</span>
          <span className="muted risk-stat-caption">
            {sharpe === null ? " " : t("risk.sharpeCaption")}
          </span>
        </div>
        <div className="risk-stat">
          <span className="muted">{t("paper.maxDrawdown")}</span>
          <span className={`num ${ddTone}`}>{ddPct === null ? "N/A" : fmtPct(ddPct, 2)}</span>
          <span className="muted risk-stat-caption">
            {ddPct === null ? " " : t("risk.maxDdCaption")}
          </span>
        </div>
        <div className="risk-stat">
          <span className="muted">{t("pd.dayChange")}</span>
          <span className={`num ${pnlClass(dayPnl)}`}>{fmtSignedJpy(dayPnl)}</span>
          <span className="muted risk-stat-caption">
            {dayPnlPct === null
              ? " "
              : `${dayPnlPct >= 0 ? "+" : ""}${fmtPct(dayPnlPct)}`}
          </span>
        </div>
      </div>

      {mixTotal > 0 && (
        <div className="risk-block">
          <div className="risk-row">
            <span className="muted">{t("risk.assetMix")}</span>
            <span className="num muted">
              EQUITY {fmtPct(eqAlloc)} · BOND {fmtPct(bondAlloc)}
            </span>
          </div>
          <div className="split-bar">
            <div className="split-invested" style={{ width: `${eqShare}%` }} />
            <div className="split-bond" style={{ width: `${100 - eqShare}%` }} />
          </div>
        </div>
      )}

      {kpis.bond_wtd_mod_duration !== null && kpis.bond_wtd_ytm_pct !== null && (
        <div className="risk-block">
          <div className="risk-row">
            <span className="muted">{t("risk.bondBook")}</span>
            <span className="num muted">
              {t("risk.bondBookLine", {
                ytm: fmtPct(kpis.bond_wtd_ytm_pct),
                dur: kpis.bond_wtd_mod_duration.toFixed(2),
              })}
            </span>
          </div>
        </div>
      )}

      <div className="risk-block">
        <div className="risk-row">
          <span className="muted">{t("risk.topHoldings")}</span>
        </div>
        {top3.length === 0 ? (
          <div className="muted risk-empty">{t("risk.noHoldings")}</div>
        ) : (
          top3.map((h) => (
            <div key={h.instrument_symbol} className="risk-holding">
              <span className="risk-holding-symbol mono">{h.instrument_symbol}</span>
              <div className="meter meter-slim">
                <div
                  className="meter-fill meter-blue"
                  style={{ width: `${Math.min(100, Math.max(0, h.pct))}%` }}
                />
              </div>
              <span className="num risk-holding-pct">{fmtPct(h.pct)}</span>
            </div>
          ))
        )}
      </div>

      <div className="risk-block">
        <div className="risk-row">
          <span className="muted">{t("risk.investedVsCash")}</span>
          <span className="num">{t("risk.investedPct", { pct: fmtPct(investedPct) })}</span>
        </div>
        <div className="split-bar">
          <div className="split-invested" style={{ width: `${investedPct}%` }} />
          <div className="split-cash" style={{ width: `${100 - investedPct}%` }} />
        </div>
        <div className="risk-split-labels muted num">
          <span>{t("risk.invested", { v: fmtJpy(valuation.market_value) })}</span>
          <span>{t("risk.cashLine", { v: fmtJpy(valuation.cash) })}</span>
        </div>
      </div>
      </div>
    </section>
  );
}
