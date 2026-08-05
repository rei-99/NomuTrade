import { useCallback, useState } from "react";
import { api, downloadFile } from "../api/client";
import type {
  AdminHealth,
  GovernanceSummary,
  SettlementInstruction,
  SettlementListResponse,
  StpException,
} from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { StatCard } from "../components/StatCard";
import { useToast } from "../components/Toast";
import { SHOW_PAM } from "../features";
import { fmtJpy, fmtNum, fmtTs } from "../format";
import { useAuth } from "../auth";
import { usePoll } from "../hooks";
import { useT } from "../i18n";

// Newest instructions shown in the settlements lane (first page is 50).
const SETTLEMENTS_SHOWN = 15;

function exceptionText(e: StpException): string {
  const parts = [e.execution_id, e.lifecycle_state, e.reason].filter(
    (v): v is string => typeof v === "string",
  );
  if (typeof e.age_seconds === "number") parts.push(`age ${Math.round(e.age_seconds)}s`);
  return parts.length > 0 ? parts.join(" · ") : JSON.stringify(e);
}

export function Governance() {
  const { hasPerm } = useAuth();
  const { t } = useT();
  const { toast } = useToast();
  const [summary, setSummary] = useState<GovernanceSummary | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [settlements, setSettlements] = useState<SettlementInstruction[] | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const canRetry = hasPerm("STP_EXCEPTION_HANDLE");

  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      hasPerm("GOVERNANCE_VIEW")
        ? api<GovernanceSummary>("/admin/governance-summary")
        : Promise.resolve(null),
      hasPerm("INTEGRATION_MONITOR")
        ? api<AdminHealth>("/admin/health")
        : Promise.resolve(null),
      // Settlement lane: TRADE_VIEW-gated like the backend; rows come back
      // scoped server-side (own books unless the caller has a view-all perm).
      hasPerm("TRADE_VIEW")
        ? api<SettlementListResponse>("/settlements")
        : Promise.resolve(null),
    ]);
    const [s, h, st] = results;
    if (s.status === "fulfilled" && s.value) setSummary(s.value);
    if (h.status === "fulfilled" && h.value) setHealth(h.value);
    if (st.status === "fulfilled" && st.value) {
      setSettlements(st.value.items.slice(0, SETTLEMENTS_SHOWN));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  usePoll(
    () => {
      void load();
    },
    10_000,
    [load],
  );

  // FR-ORD-005 E1 remediation: re-publish the dropped execution event. The
  // STP worker's idempotency check makes a duplicate harmless; a 409 means
  // the exception was already remediated — the refetch drops the row.
  const retry = async (executionId: string) => {
    setRetrying(executionId);
    try {
      await api(`/settlements/exceptions/${executionId}/retry`, { method: "POST" });
      toast(t("gov.retryDone", { id: executionId }), "success");
    } catch {
      // error toast raised by the client (shows the 409 conflict message)
    } finally {
      setRetrying(null);
      void load();
    }
  };

  const accessReview = async () => {
    setDownloading(true);
    try {
      await downloadFile("/admin/access-review", { format: "csv" }, "access-review.csv");
    } catch {
      // toast raised by client
    } finally {
      setDownloading(false);
    }
  };

  // The CyberArk/PAM health tile is hidden while the presentation flag is
  // off (features.ts) — the API response itself is unchanged.
  const visibleIntegrations =
    health?.integrations.filter((i) => SHOW_PAM || i.name.toLowerCase() !== "cyberark") ?? [];

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("gov.title")}</h2>
        {hasPerm("GOVERNANCE_VIEW") && (
          <button className="btn btn-ghost btn-sm" disabled={downloading} onClick={() => void accessReview()}>
            {downloading ? t("reports.downloading") : t("gov.accessReview")}
          </button>
        )}
      </div>

      {summary && (
        <>
          <div className="stat-row">
            <StatCard label={t("gov.activeGrants")} value={fmtNum(summary.active_grants)} />
            <StatCard
              label={t("gov.pendingApprovals")}
              value={fmtNum(summary.pending_approvals)}
              sub={
                summary.oldest_age_hours !== null && summary.oldest_age_hours !== undefined
                  ? t("gov.oldestHours", { n: fmtNum(summary.oldest_age_hours) })
                  : undefined
              }
            />
            <StatCard label={t("gov.grantsExpiring")} value={fmtNum(summary.grants_expiring_24h)} />
            <StatCard
              label={t("gov.bgPending")}
              value={fmtNum(summary.break_glass_pending_review)}
              tone={summary.break_glass_pending_review > 0 ? "neg" : ""}
            />
            <StatCard
              label={t("gov.denials")}
              value={fmtNum(summary.authorization_denials_24h)}
              tone={summary.authorization_denials_24h > 0 ? "neg" : ""}
            />
          </div>

          {(summary.recent_break_glass ?? []).length > 0 && (
            <section className="panel">
              <div className="panel-header">
                <h3>{t("gov.recentBg")}</h3>
              </div>
              <DataTable
                rows={summary.recent_break_glass}
                keyFn={(r) => r.bg_id}
                columns={[
                  { header: t("gov.user"), render: (r) => r.user.email },
                  { header: t("gov.role"), render: (r) => r.emergency_role },
                  { header: t("gov.incident"), render: (r) => r.incident_ref },
                  { header: t("gov.activated"), render: (r) => <span className="num">{fmtTs(r.activated_at)}</span> },
                  { header: t("gov.review"), render: (r) => <Badge text={r.review_status} /> },
                  { header: t("gov.verdict"), render: (r) => (r.verdict ? <Badge text={r.verdict} /> : "—") },
                ]}
              />
            </section>
          )}
        </>
      )}

      {health && (
        <>
          <section className="panel">
            <div className="panel-header">
              <h3>{t("gov.health")}</h3>
              <span className="muted num">
                {t("gov.outbox", { n: fmtNum(health.outbox_unpublished) })}
              </span>
            </div>
            <div className="health-grid">
              {visibleIntegrations.length === 0 && (
                <div className="panel-empty muted">{t("gov.noIntegrations")}</div>
              )}
              {visibleIntegrations.map((i) => (
                <div key={i.name} className={`health-tile health-${i.status.toLowerCase()}`}>
                  <div className="health-name">{i.name}</div>
                  <Badge text={i.status} />
                  {i.detail?.startsWith("mock:") && (
                    <span className="chip chip-static">{t("gov.mockBadge")}</span>
                  )}
                  <div className="health-detail muted">
                    {t("gov.lastSuccess")} <span className="num">{fmtTs(i.last_success)}</span>
                  </div>
                  {i.detail && <div className="health-detail muted">{i.detail}</div>}
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h3>{t("gov.exceptions")}</h3>
            </div>
            {health.stp_exceptions.length === 0 ? (
              <div className="panel-empty muted">{t("gov.noExceptions")}</div>
            ) : (
              <ul className="exception-list">
                {health.stp_exceptions.map((e, idx) => {
                  const execId = typeof e.execution_id === "string" ? e.execution_id : null;
                  return (
                    <li key={execId ?? idx} className="mono">
                      {exceptionText(e)}
                      {canRetry && execId && (
                        <button
                          className="btn btn-ghost btn-sm"
                          style={{ marginLeft: 8 }}
                          disabled={retrying !== null}
                          title={t("gov.retryTitle")}
                          onClick={() => void retry(execId)}
                        >
                          {retrying === execId ? t("common.loading") : t("gov.retry")}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </>
      )}

      {settlements && (
        <section className="panel">
          <div className="panel-header">
            <h3>{t("gov.settlements")}</h3>
          </div>
          <DataTable<SettlementInstruction>
            rows={settlements}
            keyFn={(s) => s.settlement_id}
            empty={t("gov.noSettlements")}
            columns={[
              {
                header: t("common.symbol"),
                sortable: true,
                sortValue: (s) => s.instrument_symbol,
                render: (s) => s.instrument_symbol,
              },
              {
                header: t("common.side"),
                render: (s) => <Badge text={s.side} />,
              },
              {
                header: t("common.qty"),
                className: "num",
                sortable: true,
                sortValue: (s) => s.quantity,
                render: (s) => fmtNum(s.quantity),
              },
              {
                header: t("gov.value"),
                className: "num",
                sortable: true,
                sortValue: (s) => s.value,
                render: (s) => fmtJpy(s.value, true),
              },
              {
                header: t("common.portfolio"),
                render: (s) => <span title={s.portfolio_id}>{s.portfolio_name}</span>,
              },
              {
                header: t("common.status"),
                sortable: true,
                sortValue: (s) => s.lifecycle_state,
                render: (s) => <Badge text={s.lifecycle_state} />,
              },
              {
                header: t("gov.settledAt"),
                className: "num",
                sortable: true,
                sortValue: (s) => s.settled_at ?? "",
                render: (s) => <span className="num">{fmtTs(s.settled_at)}</span>,
              },
            ]}
          />
        </section>
      )}

      {!summary && !health && !settlements && (
        <div className="panel panel-empty muted">{t("gov.loading")}</div>
      )}
    </div>
  );
}
