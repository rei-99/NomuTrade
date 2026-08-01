import { useState } from "react";
import { api, downloadFile } from "../api/client";
import type { AuditEvent, ListResponse } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { useToast } from "../components/Toast";
import { fmtTs } from "../format";
import { useT } from "../i18n";

const SEVERITIES = ["", "INFO", "WARNING", "ERROR", "CRITICAL"];

function toIso(local: string): string | undefined {
  return local ? new Date(local).toISOString() : undefined;
}

export function Audit() {
  const { toast } = useToast();
  const { t } = useT();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [actor, setActor] = useState("");
  const [eventType, setEventType] = useState("");
  const [severity, setSeverity] = useState("");
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState<"csv" | "json" | null>(null);

  const buildParams = (c: string | null) => ({
    from: toIso(from),
    to: toIso(to),
    actor: actor || undefined,
    event_type: eventType || undefined,
    severity: severity || undefined,
    cursor: c ?? undefined,
  });

  const search = async (c: string | null, append: boolean) => {
    if (!from || !to) {
      toast(t("audit.required"), "error");
      return;
    }
    setLoading(true);
    try {
      const res = await api<ListResponse<AuditEvent>>("/audit-events", { params: buildParams(c) });
      setItems((prev) => (append ? [...prev, ...res.items] : res.items));
      setCursor(res.next_cursor);
      setSearched(true);
    } catch {
      // toast raised by client
    } finally {
      setLoading(false);
    }
  };

  const doExport = async (format: "csv" | "json") => {
    if (!from || !to) {
      toast(t("audit.requiredExport"), "error");
      return;
    }
    setExporting(format);
    try {
      await downloadFile(
        "/audit-events/export",
        { ...buildParams(null), format },
        `audit-events.${format}`,
      );
    } catch {
      // toast raised by client
    } finally {
      setExporting(null);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("audit.title")}</h2>
      </div>

      <section className="panel">
        <div className="filter-bar">
          <label>
            {t("audit.fromRequired")}
            <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label>
            {t("audit.toRequired")}
            <input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
          <label>
            {t("audit.actor")}
            <input
              type="text"
              value={actor}
              onChange={(e) => setActor(e.target.value)}
              placeholder="user@demo.nomura"
            />
          </label>
          <label>
            {t("audit.eventType")}
            <input
              type="text"
              value={eventType}
              onChange={(e) => setEventType(e.target.value)}
              placeholder="e.g. ORDER_SUBMIT"
            />
          </label>
          <label>
            {t("audit.severity")}
            <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s === "" ? t("common.all") : s}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn btn-buy active btn-sm filter-submit"
            disabled={loading}
            onClick={() => void search(null, false)}
          >
            {loading ? t("audit.searching") : t("audit.search")}
          </button>
          <button
            className="btn btn-ghost btn-sm filter-submit"
            disabled={exporting !== null}
            onClick={() => void doExport("csv")}
          >
            {exporting === "csv" ? t("audit.exporting") : t("audit.exportCsv")}
          </button>
          <button
            className="btn btn-ghost btn-sm filter-submit"
            disabled={exporting !== null}
            onClick={() => void doExport("json")}
          >
            {exporting === "json" ? t("audit.exporting") : t("audit.exportJson")}
          </button>
        </div>

        {!searched ? (
          <div className="panel-empty muted">
            {t("audit.hint")}
          </div>
        ) : (
          <>
            <DataTable<AuditEvent>
              rows={items}
              keyFn={(e) => e.event_id}
              empty={t("audit.empty")}
              columns={[
                { header: t("audit.time"), render: (e) => <span className="num">{fmtTs(e.ts)}</span> },
                { header: t("audit.severity"), render: (e) => <Badge text={e.severity} /> },
                { header: t("audit.actor"), render: (e) => e.actor_email },
                { header: t("audit.event"), render: (e) => e.event_type },
                { header: t("audit.resource"), render: (e) => `${e.resource_type}/${e.resource_id}` },
                { header: t("audit.sourceIp"), className: "num", render: (e) => e.source_ip },
                {
                  header: t("audit.correlation"),
                  className: "num",
                  render: (e) => <span className="cell-clip" title={e.correlation_id}>{e.correlation_id}</span>,
                },
                {
                  header: t("audit.payload"),
                  render: (e) => (
                    <span className="cell-clip mono" title={JSON.stringify(e.payload)}>
                      {JSON.stringify(e.payload)}
                    </span>
                  ),
                },
              ]}
            />
            {cursor && (
              <div className="table-footer">
                <button className="btn btn-ghost btn-sm" disabled={loading} onClick={() => void search(cursor, true)}>
                  {t("common.loadMore")}
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
