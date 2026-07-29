import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AlertCondition, AlertRule, Instrument, ListResponse } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { useToast } from "../components/Toast";
import { fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";

const CONDITIONS: AlertCondition[] = ["ABOVE", "BELOW", "CROSSES_ABOVE", "CROSSES_BELOW"];

export function Alerts() {
  const { toast } = useToast();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [symbol, setSymbol] = useState("");
  const [condition, setCondition] = useState<AlertCondition>("ABOVE");
  const [threshold, setThreshold] = useState("");

  const load = useCallback(async () => {
    const res = await api<ListResponse<AlertRule>>("/analytics/alerts");
    setRules(res.items);
  }, []);

  usePoll(
    () => {
      void load();
    },
    10_000,
    [load],
  );

  // Instruments: fetch once for the create-form picker (tradable only).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api<ListResponse<Instrument>>("/instruments");
        if (!cancelled) {
          const tradable = res.items.filter((i) => i.tradable);
          setInstruments(tradable);
          setSymbol((cur) => cur || tradable[0]?.symbol || "");
        }
      } catch {
        // toast raised by client
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const create = async () => {
    const thresholdNum = Number(threshold);
    if (!symbol) {
      toast("Pick an instrument", "error");
      return;
    }
    if (Number.isNaN(thresholdNum) || thresholdNum <= 0) {
      toast("Threshold must be a positive number", "error");
      return;
    }
    try {
      await api<AlertRule>("/analytics/alerts", {
        method: "POST",
        body: { instrument: symbol, condition, threshold: thresholdNum },
      });
      toast(`Alert created for ${symbol}`, "success");
      setThreshold("");
      void load();
    } catch {
      // toast raised by client
    }
  };

  const disable = async (r: AlertRule) => {
    try {
      await api(`/analytics/alerts/${r.rule_id}`, { method: "DELETE" });
      toast(`Alert on ${r.instrument} disabled`, "success");
      void load();
    } catch {
      // toast raised by client
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>Price Alerts</h2>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h3>New alert</h3>
        </div>
        <div className="filter-bar">
          <label>
            Instrument
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {instruments.map((i) => (
                <option key={i.instrument_id} value={i.symbol}>
                  {i.symbol} — {i.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Condition
            <select
              value={condition}
              onChange={(e) => setCondition(e.target.value as AlertCondition)}
            >
              {CONDITIONS.map((c) => (
                <option key={c} value={c}>
                  {c.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
          <label>
            Threshold
            <input
              type="number"
              min="0"
              step="any"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              placeholder="e.g. 195.50"
            />
          </label>
          <button
            className="btn btn-buy active btn-sm filter-submit"
            disabled={instruments.length === 0}
            onClick={() => void create()}
          >
            Create alert
          </button>
        </div>
        <p className="muted">Triggered alerts arrive as notifications.</p>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>My alert rules</h3>
        </div>
        <DataTable<AlertRule>
          rows={rules}
          keyFn={(r) => r.rule_id}
          empty="No alert rules"
          columns={[
            { header: "Instrument", render: (r) => r.instrument },
            { header: "Condition", render: (r) => r.condition.replace(/_/g, " ") },
            { header: "Threshold", className: "num", render: (r) => fmtNum(r.threshold, 2) },
            { header: "Status", render: (r) => <Badge text={r.status} /> },
            { header: "Created", render: (r) => <span className="num">{fmtTs(r.created_at)}</span> },
            {
              header: "",
              render: (r) =>
                r.status === "ACTIVE" ? (
                  <button className="btn btn-danger btn-sm" onClick={() => void disable(r)}>
                    Disable
                  </button>
                ) : null,
            },
          ]}
        />
      </section>
    </div>
  );
}
