import { useCallback, useState } from "react";
import { api, downloadFile } from "../api/client";
import type {
  ListResponse,
  Portfolio,
  Report,
  ReportCreated,
  ReportFormat,
  ReportFrequency,
  ReportSchedule,
  ReportType,
} from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { useToast } from "../components/Toast";
import { fmtDate, fmtTs } from "../format";
import { usePoll } from "../hooks";
import { useT } from "../i18n";

const REPORT_TYPES: ReportType[] = ["HOLDINGS", "TRANSACTIONS", "PERFORMANCE"];
const FORMATS: ReportFormat[] = ["PDF", "CSV"];
const FREQUENCIES: ReportFrequency[] = ["DAILY", "WEEKLY"];
// Backend report statuses are REQUESTED → DONE | FAILED (see ReportStatus in
// api/types.ts); only DONE reports have a file to download. The list keeps a
// few defensive aliases for rows written before the contract settled.
const DOWNLOADABLE = ["READY", "COMPLETED", "DONE", "SUCCESS"];

export function Reports() {
  const { toast } = useToast();
  const { t } = useT();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [schedules, setSchedules] = useState<ReportSchedule[]>([]);

  const [type, setType] = useState<ReportType>("HOLDINGS");
  const [portfolioId, setPortfolioId] = useState("");
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [format, setFormat] = useState<ReportFormat>("PDF");
  const [submitting, setSubmitting] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);

  const [schedType, setSchedType] = useState<ReportType>("HOLDINGS");
  const [schedPortfolioId, setSchedPortfolioId] = useState("");
  const [schedFormat, setSchedFormat] = useState<ReportFormat>("PDF");
  const [schedFrequency, setSchedFrequency] = useState<ReportFrequency>("DAILY");
  const [schedSubmitting, setSchedSubmitting] = useState(false);
  const [deletingSchedule, setDeletingSchedule] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [pfs, reps] = await Promise.all([
      api<ListResponse<Portfolio>>("/portfolios"),
      api<ListResponse<Report>>("/reports"),
    ]);
    setPortfolios(pfs.items);
    setReports(reps.items);
    setPortfolioId((cur) => cur || pfs.items[0]?.portfolio_id || "");
    setSchedPortfolioId((cur) => cur || pfs.items[0]?.portfolio_id || "");
  }, []);

  const loadSchedules = useCallback(async () => {
    const res = await api<ListResponse<ReportSchedule>>("/report-schedules");
    setSchedules(res.items);
  }, []);

  usePoll(
    () => {
      void load();
    },
    5_000,
    [load],
  );

  usePoll(
    () => {
      void loadSchedules();
    },
    15_000,
    [loadSchedules],
  );

  const submit = async () => {
    if (!portfolioId || !periodStart || !periodEnd) {
      toast(t("reports.required"), "error");
      return;
    }
    setSubmitting(true);
    try {
      const created = await api<ReportCreated>("/reports", {
        method: "POST",
        body: {
          type,
          portfolio_id: portfolioId,
          period_start: periodStart,
          period_end: periodEnd,
          format,
        },
      });
      toast(t("reports.requested", { id: created.report_id, status: created.status }), "success");
      void load();
    } catch {
      // toast raised by client
    } finally {
      setSubmitting(false);
    }
  };

  const download = async (r: Report) => {
    setDownloading(r.report_id);
    try {
      await downloadFile(
        `/reports/${r.report_id}/download`,
        {},
        `report-${r.report_id}.${r.format.toLowerCase()}`,
      );
    } catch {
      // toast raised by client
    } finally {
      setDownloading(null);
    }
  };

  const createSchedule = async () => {
    if (!schedPortfolioId) {
      toast(t("reports.portfolioRequired"), "error");
      return;
    }
    setSchedSubmitting(true);
    try {
      await api<ReportSchedule>("/report-schedules", {
        method: "POST",
        body: {
          portfolio_id: schedPortfolioId,
          type: schedType,
          format: schedFormat,
          frequency: schedFrequency,
        },
      });
      toast(t("reports.scheduleCreated"), "success");
      void loadSchedules();
    } catch {
      // toast raised by client
    } finally {
      setSchedSubmitting(false);
    }
  };

  const deleteSchedule = async (s: ReportSchedule) => {
    setDeletingSchedule(s.schedule_id);
    try {
      await api(`/report-schedules/${s.schedule_id}`, { method: "DELETE" });
      toast(t("reports.scheduleDeleted"), "success");
      void loadSchedules();
    } catch {
      // toast raised by client
    } finally {
      setDeletingSchedule(null);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("reports.title")}</h2>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h3>{t("reports.request")}</h3>
        </div>
        <div className="filter-bar">
          <label>
            {t("reports.type")}
            <select value={type} onChange={(e) => setType(e.target.value as ReportType)}>
              {REPORT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("common.portfolio")}
            <select value={portfolioId} onChange={(e) => setPortfolioId(e.target.value)}>
              {portfolios.length === 0 && <option value="">{t("portfolios.empty")}</option>}
              {portfolios.map((p) => (
                <option key={p.portfolio_id} value={p.portfolio_id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("reports.periodStart")}
            <input
              type="date"
              value={periodStart}
              onChange={(e) => setPeriodStart(e.target.value)}
            />
          </label>
          <label>
            {t("reports.periodEnd")}
            <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
          </label>
          <label>
            {t("reports.format")}
            <select value={format} onChange={(e) => setFormat(e.target.value as ReportFormat)}>
              {FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn btn-buy active btn-sm filter-submit"
            disabled={submitting || portfolios.length === 0}
            onClick={() => void submit()}
          >
            {submitting ? t("reports.submitting") : t("reports.submit")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>{t("reports.schedules")}</h3>
        </div>
        <p className="muted">{t("reports.schedNote")}</p>
        <div className="filter-bar">
          <label>
            {t("common.portfolio")}
            <select
              value={schedPortfolioId}
              onChange={(e) => setSchedPortfolioId(e.target.value)}
            >
              {portfolios.length === 0 && <option value="">{t("portfolios.empty")}</option>}
              {portfolios.map((p) => (
                <option key={p.portfolio_id} value={p.portfolio_id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("reports.type")}
            <select
              value={schedType}
              onChange={(e) => setSchedType(e.target.value as ReportType)}
            >
              {REPORT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("reports.format")}
            <select
              value={schedFormat}
              onChange={(e) => setSchedFormat(e.target.value as ReportFormat)}
            >
              {FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("reports.frequency")}
            <select
              value={schedFrequency}
              onChange={(e) => setSchedFrequency(e.target.value as ReportFrequency)}
            >
              {FREQUENCIES.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn btn-buy active btn-sm filter-submit"
            disabled={schedSubmitting || portfolios.length === 0}
            onClick={() => void createSchedule()}
          >
            {schedSubmitting ? t("reports.creating") : t("reports.createSchedule")}
          </button>
        </div>
        <DataTable<ReportSchedule>
          rows={schedules}
          keyFn={(s) => s.schedule_id}
          empty={t("reports.noSchedules")}
          columns={[
            { header: t("reports.type"), render: (s) => s.type },
            {
              header: t("common.portfolio"),
              render: (s) =>
                portfolios.find((p) => p.portfolio_id === s.portfolio_id)?.name ??
                s.portfolio_id,
            },
            { header: t("reports.format"), render: (s) => s.format },
            { header: t("reports.frequency"), render: (s) => s.frequency },
            {
              header: t("reports.nextRun"),
              render: (s) => <span className="num">{fmtTs(s.next_run_at)}</span>,
            },
            {
              header: t("common.created"),
              render: (s) => <span className="num">{fmtTs(s.created_at)}</span>,
            },
            {
              header: "",
              render: (s) => (
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={deletingSchedule === s.schedule_id}
                  onClick={() => void deleteSchedule(s)}
                >
                  {deletingSchedule === s.schedule_id ? t("reports.deleting") : t("reports.delete")}
                </button>
              ),
            },
          ]}
        />
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>{t("reports.my")}</h3>
        </div>
        <DataTable<Report>
          rows={reports}
          keyFn={(r) => r.report_id}
          empty={t("reports.empty")}
          columns={[
            { header: t("common.created"), render: (r) => <span className="num">{fmtTs(r.created_at)}</span> },
            { header: t("reports.type"), render: (r) => r.type },
            {
              header: t("common.portfolio"),
              render: (r) =>
                portfolios.find((p) => p.portfolio_id === r.portfolio_id)?.name ?? r.portfolio_id,
            },
            {
              header: t("reports.period"),
              render: (r) => (
                <span className="num">
                  {fmtDate(r.period_start)} → {fmtDate(r.period_end)}
                </span>
              ),
            },
            { header: t("reports.format"), render: (r) => r.format },
            { header: t("common.status"), render: (r) => <Badge text={r.status} /> },
            {
              header: "",
              render: (r) => (
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={
                    !DOWNLOADABLE.includes(r.status.toUpperCase()) || downloading === r.report_id
                  }
                  onClick={() => void download(r)}
                >
                  {downloading === r.report_id ? t("reports.downloading") : t("common.download")}
                </button>
              ),
            },
          ]}
        />
      </section>
    </div>
  );
}
