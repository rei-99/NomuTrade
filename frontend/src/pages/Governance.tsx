import { useCallback, useState } from "react";
import { api, downloadFile } from "../api/client";
import type { AdminHealth, GovernanceSummary, StpException } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { StatCard } from "../components/StatCard";
import { fmtNum, fmtTs } from "../format";
import { useAuth } from "../auth";
import { usePoll } from "../hooks";

function exceptionText(e: StpException): string {
  const known = [e.exception_id, e.order_id, e.reason, e.status].filter(
    (v): v is string => typeof v === "string",
  );
  return known.length > 0 ? known.join(" · ") : JSON.stringify(e);
}

export function Governance() {
  const { hasPerm } = useAuth();
  const [summary, setSummary] = useState<GovernanceSummary | null>(null);
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [downloading, setDownloading] = useState(false);

  const load = useCallback(async () => {
    const results = await Promise.allSettled([
      hasPerm("GOVERNANCE_VIEW")
        ? api<GovernanceSummary>("/admin/governance-summary")
        : Promise.resolve(null),
      hasPerm("INTEGRATION_MONITOR", "GOVERNANCE_VIEW")
        ? api<AdminHealth>("/admin/health")
        : Promise.resolve(null),
    ]);
    const [s, h] = results;
    if (s.status === "fulfilled" && s.value) setSummary(s.value);
    if (h.status === "fulfilled" && h.value) setHealth(h.value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  usePoll(
    () => {
      void load();
    },
    10_000,
    [load],
  );

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

  return (
    <div className="page">
      <div className="page-header">
        <h2>Governance &amp; Health</h2>
        <button className="btn btn-ghost btn-sm" disabled={downloading} onClick={() => void accessReview()}>
          {downloading ? "Downloading…" : "Access review CSV"}
        </button>
      </div>

      {summary && (
        <>
          <div className="stat-row">
            <StatCard label="Active grants" value={fmtNum(summary.active_grants)} />
            <StatCard
              label="Pending approvals"
              value={fmtNum(summary.pending_approvals)}
              sub={
                summary.oldest_age_hours !== null && summary.oldest_age_hours !== undefined
                  ? `oldest ${fmtNum(summary.oldest_age_hours)} h`
                  : undefined
              }
            />
            <StatCard label="Grants expiring 24 h" value={fmtNum(summary.grants_expiring_24h)} />
            <StatCard
              label="Break-glass pending review"
              value={fmtNum(summary.break_glass_pending_review)}
              tone={summary.break_glass_pending_review > 0 ? "neg" : ""}
            />
            <StatCard
              label="Authz denials 24 h"
              value={fmtNum(summary.authorization_denials_24h)}
              tone={summary.authorization_denials_24h > 0 ? "neg" : ""}
            />
          </div>

          {(summary.recent_break_glass ?? []).length > 0 && (
            <section className="panel">
              <div className="panel-header">
                <h3>Recent break-glass</h3>
              </div>
              <DataTable
                rows={summary.recent_break_glass}
                keyFn={(r) => r.bg_id}
                columns={[
                  { header: "User", render: (r) => r.user.email },
                  { header: "Role", render: (r) => r.emergency_role },
                  { header: "Incident", render: (r) => r.incident_ref },
                  { header: "Activated", render: (r) => <span className="num">{fmtTs(r.activated_at)}</span> },
                  { header: "Review", render: (r) => <Badge text={r.review_status} /> },
                  { header: "Verdict", render: (r) => (r.verdict ? <Badge text={r.verdict} /> : "—") },
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
              <h3>Integration health</h3>
              <span className="muted num">
                outbox unpublished: {fmtNum(health.outbox_unpublished)}
              </span>
            </div>
            <div className="health-grid">
              {health.integrations.length === 0 && (
                <div className="panel-empty muted">No integrations reported.</div>
              )}
              {health.integrations.map((i) => (
                <div key={i.name} className={`health-tile health-${i.status.toLowerCase()}`}>
                  <div className="health-name">{i.name}</div>
                  <Badge text={i.status} />
                  <div className="health-detail muted">
                    last success <span className="num">{fmtTs(i.last_success)}</span>
                  </div>
                  {i.detail && <div className="health-detail muted">{i.detail}</div>}
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h3>STP exceptions</h3>
            </div>
            {health.stp_exceptions.length === 0 ? (
              <div className="panel-empty muted">No STP exceptions — pipeline is clean.</div>
            ) : (
              <ul className="exception-list">
                {health.stp_exceptions.map((e, idx) => (
                  <li key={typeof e.exception_id === "string" ? e.exception_id : idx} className="mono">
                    {exceptionText(e)}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      {!summary && !health && (
        <div className="panel panel-empty muted">Loading governance data…</div>
      )}
    </div>
  );
}
