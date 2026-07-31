import type { CSSProperties } from "react";
import type { Valuation } from "../api/types";
import { fmtJpy, fmtPct } from "../format";

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
  if (!valuation) {
    return (
      <section className="panel risk-panel">
        <div className="panel-header">
          <h3>Risk exposure</h3>
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
        <h3>Risk exposure</h3>
      </div>

      <div className="donut-row">
        <Donut
          pct={concentration}
          tone={concentrationTone(concentration)}
          label="Concentration"
          display={fmtPct(concentration, 0)}
        />
        <Donut
          pct={volatility}
          tone={volatility === null ? "na" : volatilityTone(volatility)}
          label="Volatility ann."
          display={volatility === null ? "N/A" : fmtPct(volatility, 0)}
        />
      </div>

      <div className="risk-block">
        <div className="risk-row">
          <span className="muted">Top holdings</span>
        </div>
        {top3.length === 0 ? (
          <div className="muted risk-empty">No holdings.</div>
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
          <span className="muted">Invested vs cash</span>
          <span className="num">{fmtPct(investedPct)} invested</span>
        </div>
        <div className="split-bar">
          <div className="split-invested" style={{ width: `${investedPct}%` }} />
          <div className="split-cash" style={{ width: `${100 - investedPct}%` }} />
        </div>
        <div className="risk-split-labels muted num">
          <span>{fmtJpy(valuation.market_value)} invested</span>
          <span>{fmtJpy(valuation.cash)} cash</span>
        </div>
      </div>
    </section>
  );
}
