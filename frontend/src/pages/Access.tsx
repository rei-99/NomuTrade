import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { AccessRequest, AccessRequestCreated, ListResponse, Role } from "../api/types";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { useToast } from "../components/Toast";
import { fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";

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
  const [roles, setRoles] = useState<Role[]>([]);
  const [requests, setRequests] = useState<AccessRequest[]>([]);

  const [roleId, setRoleId] = useState("");
  const [justification, setJustification] = useState("");
  const [duration, setDuration] = useState("8");
  const [onBehalfOf, setOnBehalfOf] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    const [roleRes, reqRes] = await Promise.all([
      api<Role[]>("/roles"),
      api<ListResponse<AccessRequest>>("/access-requests"),
    ]);
    setRoles(roleRes);
    setRequests(reqRes.items);
    setRoleId((cur) => cur || roleRes[0]?.role_id || "");
  }, []);

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
      toast("Select a role", "error");
      return;
    }
    if (!justification.trim()) {
      toast("Justification is required", "error");
      return;
    }
    if (Number.isNaN(hours) || hours <= 0) {
      toast("Duration must be a positive number of hours", "error");
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
        `Request ${created.request_id} submitted (status ${created.status}, level ${created.current_level})`,
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
      toast(`Request ${r.request_id} withdrawn`, "success");
      void load();
    } catch {
      // toast raised by client
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2>Access Requests</h2>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h3>New request</h3>
        </div>
        <div className="form-grid form-grid-wide">
          <label className="form-field">
            <span>Role</span>
            <select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
              {roles.length === 0 && <option value="">No roles available</option>}
              {roles.map((r) => (
                <option key={r.role_id} value={r.role_id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <label className="form-field">
            <span>Duration (hours)</span>
            <input
              type="number"
              min="1"
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
            />
          </label>
          <label className="form-field">
            <span>On behalf of (email, optional)</span>
            <input
              type="email"
              value={onBehalfOf}
              onChange={(e) => setOnBehalfOf(e.target.value)}
              placeholder="someone@demo.nomura"
            />
          </label>
          <label className="form-field form-field-full">
            <span>Justification</span>
            <textarea
              rows={3}
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              placeholder="Why do you need this role, and for what task?"
            />
          </label>
        </div>
        <div className="modal-actions">
          <button
            className="btn btn-buy active"
            disabled={submitting || roles.length === 0}
            onClick={() => void submit()}
          >
            {submitting ? "Submitting…" : "Submit request"}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>My requests</h3>
        </div>
        <DataTable<AccessRequest>
          rows={requests}
          keyFn={(r) => r.request_id}
          empty="No access requests yet"
          columns={[
            { header: "Created", render: (r) => <span className="num">{fmtTs(r.created_at)}</span> },
            { header: "Role", render: (r) => r.role.name },
            {
              header: "Duration",
              className: "num",
              render: (r) => `${fmtNum(r.requested_duration_hours)} h`,
            },
            { header: "On behalf of", render: (r) => r.on_behalf_of ?? "—" },
            { header: "Status", render: (r) => <Badge text={r.status} /> },
            { header: "Approval steps", render: (r) => <span className="muted">{stepSummary(r)}</span> },
            { header: "Decided", render: (r) => <span className="num">{fmtTs(r.decided_at)}</span> },
            {
              header: "",
              render: (r) =>
                !TERMINAL.includes(r.status.toUpperCase()) ? (
                  <button className="btn btn-danger btn-sm" onClick={() => void withdraw(r)}>
                    Withdraw
                  </button>
                ) : null,
            },
          ]}
        />
      </section>
    </div>
  );
}
