import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AlertCondition, AlertRule, Instrument, ListResponse } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { useToast } from "../components/Toast";
import { fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";
import { useT } from "../i18n";

const CONDITIONS: AlertCondition[] = ["ABOVE", "BELOW", "CROSSES_ABOVE", "CROSSES_BELOW"];

export function Alerts() {
  const { toast } = useToast();
  const { t } = useT();

  const condLabel = (c: AlertCondition): string => {
    switch (c) {
      case "ABOVE":
        return t("alerts.cond.above");
      case "BELOW":
        return t("alerts.cond.below");
      case "CROSSES_ABOVE":
        return t("alerts.cond.crosses_above");
      case "CROSSES_BELOW":
        return t("alerts.cond.crosses_below");
    }
  };
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
      toast(t("alerts.pickInstrument"), "error");
      return;
    }
    if (Number.isNaN(thresholdNum) || thresholdNum <= 0) {
      toast(t("alerts.thresholdInvalid"), "error");
      return;
    }
    try {
      await api<AlertRule>("/analytics/alerts", {
        method: "POST",
        body: { instrument: symbol, condition, threshold: thresholdNum },
      });
      toast(t("alerts.created", { symbol }), "success");
      setThreshold("");
      void load();
    } catch {
      // toast raised by client
    }
  };

  const disable = async (r: AlertRule) => {
    try {
      await api(`/analytics/alerts/${r.rule_id}`, { method: "DELETE" });
      toast(t("alerts.disabled", { symbol: r.instrument }), "success");
      void load();
    } catch {
      // toast raised by client
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("alerts.title")}</h2>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h3>{t("alerts.new")}</h3>
        </div>
        <div className="filter-bar">
          <label>
            {t("common.instrument")}
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {instruments.map((i) => (
                <option key={i.instrument_id} value={i.symbol}>
                  {i.symbol} — {i.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("alerts.condition")}
            <select
              value={condition}
              onChange={(e) => setCondition(e.target.value as AlertCondition)}
            >
              {CONDITIONS.map((c) => (
                <option key={c} value={c}>
                  {condLabel(c)}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("alerts.threshold")}
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
            {t("alerts.create")}
          </button>
        </div>
        <p className="muted">{t("alerts.note")}</p>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>{t("alerts.myRules")}</h3>
        </div>
        <DataTable<AlertRule>
          rows={rules}
          keyFn={(r) => r.rule_id}
          empty={t("alerts.empty")}
          columns={[
            { header: t("common.instrument"), render: (r) => r.instrument },
            { header: t("alerts.condition"), render: (r) => condLabel(r.condition) },
            { header: t("alerts.threshold"), className: "num", render: (r) => fmtNum(r.threshold, 2) },
            { header: t("common.status"), render: (r) => <Badge text={r.status} /> },
            { header: t("common.created"), render: (r) => <span className="num">{fmtTs(r.created_at)}</span> },
            {
              header: "",
              render: (r) =>
                r.status === "ACTIVE" ? (
                  <button className="btn btn-danger btn-sm" onClick={() => void disable(r)}>
                    {t("alerts.disable")}
                  </button>
                ) : null,
            },
          ]}
        />
      </section>
    </div>
  );
}
