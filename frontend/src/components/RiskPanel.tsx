import type { CSSProperties } from "react";
import type { Valuation } from "../api/types";
import { fmtJpy, fmtPct } from "../format";
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
 * coloring, top-holdings weight bars, invested-vs-cash slim split bar. */
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
          display={volatility === null ? "N/A" : fmtPct(volatility, 0)}
        />
      </div>

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
