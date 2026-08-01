import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { AccessRequest, AccessRequestCreated, ListResponse, Role } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { useToast } from "../components/Toast";
import { fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";
import { useT } from "../i18n";

// ASSUMPTION: terminal statuses are not enumerated in the contract; withdraw
// is offered for everything not in this set.
const TERMINAL = ["APPROVED", "REJECTED", "WITHDRAWN", "EXPIRED", "CANCELLED"];

function stepSummary(r: AccessRequest): string {
  if (r.steps.length === 0) return "—";
  return r.steps
    .map((s) => `L${s.level}: ${s.decision ?? "PENDING"}`)
    .join(" · ");
}

export function Access() {
  const { toast } = useToast();
  const { t } = useT();
  const [roles, setRoles] = useState<Role[]>([]);
  const [requests, setRequests] = useState<AccessRequest[]>([]);

  const [roleId, setRoleId] = useState("");
  const [justification, setJustification] = useState("");
  const [duration, setDuration] = useState("8");
  const [onBehalfOf, setOnBehalfOf] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Roles and requests load independently: a /roles failure must not blank
  // "my requests" — it degrades to an empty picker (select shows "no roles",
  // submit stays disabled) alongside the client's error toast.
  const loadRoles = useCallback(async () => {
    try {
      const roleRes = await api<Role[]>("/roles");
      setRoles(roleRes);
      setRoleId((cur) => cur || roleRes[0]?.role_id || "");
    } catch {
      // toast raised by client
    }
  }, []);

  const loadRequests = useCallback(async () => {
    const reqRes = await api<ListResponse<AccessRequest>>("/access-requests");
    setRequests(reqRes.items);
  }, []);

  const load = useCallback(async () => {
    await Promise.all([loadRoles(), loadRequests()]);
  }, [loadRoles, loadRequests]);

  usePoll(
    () => {
      void load();
    },
    5_000,
    [load],
  );

  const submit = async () => {
    const hours = Number(duration);
    if (!roleId) {
      toast(t("access.selectRole"), "error");
      return;
    }
    if (!justification.trim()) {
      toast(t("access.justRequired"), "error");
      return;
    }
    if (Number.isNaN(hours) || hours <= 0) {
      toast(t("access.durationInvalid"), "error");
      return;
    }
    setSubmitting(true);
    try {
      const created = await api<AccessRequestCreated>("/access-requests", {
        method: "POST",
        body: {
          target_role: roleId,
          justification: justification.trim(),
          requested_duration_hours: hours,
          ...(onBehalfOf.trim() ? { on_behalf_of: onBehalfOf.trim() } : {}),
        },
      });
      toast(
        t("access.submitted", {
          id: created.request_id,
          status: created.status,
          level: created.current_level,
        }),
        "success",
      );
      setJustification("");
      void load();
    } catch {
      // toast raised by client
    } finally {
      setSubmitting(false);
    }
  };

  const withdraw = async (r: AccessRequest) => {
    try {
      await api(`/access-requests/${r.request_id}/withdraw`, { method: "POST" });
      toast(t("access.withdrawn", { id: r.request_id }), "success");
      void load();
    } catch {
      // toast raised by client
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("access.title")}</h2>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h3>{t("access.new")}</h3>
        </div>
        <div className="form-grid form-grid-wide">
          <label className="form-field">
            <span>{t("access.role")}</span>
            <select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
              {roles.length === 0 && <option value="">{t("access.noRoles")}</option>}
              {roles.map((r) => (
                <option key={r.role_id} value={r.role_id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <label className="form-field">
            <span>{t("access.duration")}</span>
            <input
              type="number"
              min="1"
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
            />
          </label>
          <label className="form-field">
            <span>{t("access.onBehalf")}</span>
            <input
              type="email"
              value={onBehalfOf}
              onChange={(e) => setOnBehalfOf(e.target.value)}
              placeholder="someone@demo.nomura"
            />
          </label>
          <label className="form-field form-field-full">
            <span>{t("access.justification")}</span>
            <textarea
              rows={3}
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              placeholder={t("access.justificationPlaceholder")}
            />
          </label>
        </div>
        <div className="modal-actions">
          <button
            className="btn btn-buy active"
            disabled={submitting || roles.length === 0}
            onClick={() => void submit()}
          >
            {submitting ? t("access.submitting") : t("access.submit")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>{t("access.my")}</h3>
        </div>
        <DataTable<AccessRequest>
          rows={requests}
          keyFn={(r) => r.request_id}
          empty={t("access.empty")}
          columns={[
            { header: t("common.created"), render: (r) => <span className="num">{fmtTs(r.created_at)}</span> },
            { header: t("access.role"), render: (r) => r.role.name },
            {
              header: t("access.duration"),
              className: "num",
              render: (r) => t("access.hours", { n: fmtNum(r.requested_duration_hours) }),
            },
            { header: t("access.onBehalf"), render: (r) => r.on_behalf_of ?? "—" },
            { header: t("common.status"), render: (r) => <Badge text={r.status} /> },
            { header: t("access.steps"), render: (r) => <span className="muted">{stepSummary(r)}</span> },
            { header: t("access.decided"), render: (r) => <span className="num">{fmtTs(r.decided_at)}</span> },
            {
              header: "",
              render: (r) =>
                !TERMINAL.includes(r.status.toUpperCase()) ? (
                  <button className="btn btn-danger btn-sm" onClick={() => void withdraw(r)}>
                    {t("access.withdraw")}
                  </button>
                ) : null,
            },
          ]}
        />
      </section>
    </div>
  );
}
