import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BondAnalytics, Instrument } from "../api/types";
import { fmtJpy, fmtNum, fmtPct } from "../format";
import { Badge } from "./Badge";

interface BondAnalyticsCardProps {
  instrument: Instrument | undefined;
}

/**
 * Bond analytics card (design 24 §D-24.3): coupon/maturity/latest price,
 * YTM and modified duration for the workspace symbol when it is a bond.
 * The yield input refetches with an implied price — debounced, and applied
 * immediately on Enter. Renders nothing for equities.
 */
export function BondAnalyticsCard({ instrument }: BondAnalyticsCardProps) {
  const symbol = instrument?.asset_class === "BOND" ? instrument.symbol : undefined;
  const [data, setData] = useState<BondAnalytics | null>(null);
  const [yieldInput, setYieldInput] = useState("");
  const [appliedYield, setAppliedYield] = useState<number | null>(null);

  // Reset when the symbol changes.
  useEffect(() => {
    setData(null);
    setYieldInput("");
    setAppliedYield(null);
  }, [symbol]);

  // Debounce the yield input; Enter applies immediately (below).
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (yieldInput.trim() === "") {
        setAppliedYield(null);
        return;
      }
      const y = Number(yieldInput);
      if (!Number.isNaN(y)) setAppliedYield(y);
    }, 500);
    return () => window.clearTimeout(timer);
  }, [yieldInput]);

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await api<BondAnalytics>(`/instruments/${symbol}/bond-analytics`, {
          params: appliedYield !== null ? { yield: appliedYield } : undefined,
          skipErrorToast: true,
        });
        if (!cancelled) setData(res);
      } catch {
        // keep last good data (e.g. transient error while the feed warms)
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [symbol, appliedYield]);

  if (!symbol) return null;

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Bond analytics — {symbol}</h3>
        <Badge text="BOND" />
      </div>
      {data === null ? (
        <div className="skeleton" style={{ height: 96 }} />
      ) : (
        <>
          <div className="confirm-grid">
            <span className="muted">Coupon</span>
            <span className="num">{fmtPct(data.coupon_rate, 2)}</span>
            <span className="muted">Maturity</span>
            <span className="num">{data.maturity_date}</span>
            <span className="muted">Years to mat.</span>
            <span className="num">{fmtNum(data.years_to_maturity, 2)}</span>
            <span className="muted">Price (% par)</span>
            <span className="num">{fmtJpy(data.latest_price, true)}</span>
            <span className="muted">YTM</span>
            <span className="num">{data.ytm !== null ? fmtPct(data.ytm, 2) : "—"}</span>
            <span className="muted">Mod. duration</span>
            <span className="num">{fmtNum(data.modified_duration, 2)}</span>
          </div>
          <label className="form-field">
            <span>Yield % → implied price</span>
            <input
              type="number"
              step="any"
              value={yieldInput}
              onChange={(e) => setYieldInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const y = Number(yieldInput);
                  setAppliedYield(yieldInput.trim() === "" || Number.isNaN(y) ? null : y);
                }
              }}
              placeholder={data.ytm !== null ? String(data.ytm) : "e.g. 4.25"}
              className="num"
            />
          </label>
          {appliedYield !== null && data.implied_price !== undefined && (
            <div className="order-cost-line">
              <span>
                at {fmtPct(appliedYield, 2)} →{" "}
                <span className="num">{fmtJpy(data.implied_price, true)}</span>
                <span className="muted"> (% par)</span>
              </span>
            </div>
          )}
        </>
      )}
    </section>
  );
}
