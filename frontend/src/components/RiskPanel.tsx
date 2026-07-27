import type { Valuation } from "../api/types";
import { fmtJpy, fmtPct } from "../format";

type MeterTone = "green" | "amber" | "red";

function concentrationTone(pct: number): MeterTone {
  if (pct > 40) return "red";
  if (pct > 25) return "amber";
  return "green";
}

function volatilityTone(pct: number): MeterTone {
  if (pct > 60) return "red";
  if (pct > 40) return "amber";
  return "green";
}

function Meter({ pct, tone }: { pct: number; tone: MeterTone }) {
  return (
    <div className="meter">
      <div
        className={`meter-fill meter-${tone}`}
        style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
      />
    </div>
  );
}

interface RiskPanelProps {
  valuation: Valuation | null;
}

/** Risk exposure: concentration / volatility gauges, top-holding weight bars,
 * invested-vs-cash split. All from the valuation endpoint (5 s parent poll). */
export function RiskPanel({ valuation }: RiskPanelProps) {
  if (!valuation) {
    return (
      <section className="panel">
        <div className="panel-header">
          <h3>Risk exposure</h3>
        </div>
        <div className="panel-empty muted">No valuation data.</div>
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
    <section className="panel">
      <div className="panel-header">
        <h3>Risk exposure</h3>
      </div>

      <div className="risk-block">
        <div className="risk-row">
          <span className="muted">Concentration</span>
          <span className="num">{fmtPct(concentration)}</span>
        </div>
        <Meter pct={concentration} tone={concentrationTone(concentration)} />
      </div>

      <div className="risk-block">
        <div className="risk-row">
          <span className="muted">Volatility (ann.)</span>
          <span className="num">{volatility === null ? "N/A" : fmtPct(volatility)}</span>
        </div>
        {volatility === null ? (
          <div className="meter meter-empty" />
        ) : (
          <Meter pct={volatility} tone={volatilityTone(volatility)} />
        )}
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
