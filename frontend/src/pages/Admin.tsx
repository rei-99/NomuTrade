import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  BreakGlassActivated,
  BreakGlassReview,
  Grant,
  ListResponse,
  PamCheckout,
  PermissionInfo,
  Role,
} from "../api/types";
import { useAuth } from "../auth";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";

type TabId = "roles" | "grants" | "breakglass" | "pam";

/** GET /permissions may return objects or plain strings depending on backend
 * implementation — normalize defensively. ASSUMPTION flagged for integrator. */
function normalizePermissions(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((p) => {
      if (typeof p === "string") return p;
      if (p && typeof p === "object") {
        const rec = p as Partial<PermissionInfo>;
        if (typeof rec.action === "string") return rec.action;
      }
      return null;
    })
    .filter((x): x is string => x !== null);
}

export function Admin() {
  const { hasPerm } = useAuth();
  const { toast } = useToast();

  const visibleTabs = useMemo(() => {
    const tabs: { id: TabId; label: string }[] = [];
    if (hasPerm("ROLE_MANAGE", "ROLE_VIEW")) tabs.push({ id: "roles", label: "Roles" });
    if (hasPerm("GRANT_VIEW", "GRANT_MANAGE")) tabs.push({ id: "grants", label: "Grants" });
    if (hasPerm("BREAKGLASS_ELIGIBLE", "BREAKGLASS_REVIEW"))
      tabs.push({ id: "breakglass", label: "Break-glass" });
    if (hasPerm("PAM_CHECKOUT")) tabs.push({ id: "pam", label: "PAM" });
    return tabs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [tab, setTab] = useState<TabId | null>(null);
  useEffect(() => {
    setTab((cur) => cur ?? visibleTabs[0]?.id ?? null);
  }, [visibleTabs]);

  // ---------- shared data ----------
  const [roles, setRoles] = useState<Role[]>([]);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [reviews, setReviews] = useState<BreakGlassReview[]>([]);

  // ---------- grants tab state ----------
  const [grantFilters, setGrantFilters] = useState({ user_email: "", role: "", status: "" });

  const loadRoles = useCallback(async () => {
    const [r, perms] = await Promise.all([
      api<Role[]>("/roles"),
      api<unknown>("/permissions", { skipErrorToast: true }).catch(() => []),
    ]);
    setRoles(r);
    setPermissions(normalizePermissions(perms));
  }, []);

  const loadGrants = useCallback(
    async (filters: { user_email: string; role: string; status: string }) => {
      const res = await api<ListResponse<Grant>>("/grants", {
        params: {
          user_email: filters.user_email || undefined,
          role: filters.role || undefined,
          status: filters.status || undefined,
        },
      });
      setGrants(res.items);
    },
    [],
  );

  const loadReviews = useCallback(async () => {
    const res = await api<ListResponse<BreakGlassReview>>("/break-glass/reviews");
    setReviews(res.items);
  }, []);

  usePoll(
    () => {
      if (tab === "roles") void loadRoles();
      else if (tab === "grants") void loadGrants(grantFilters);
      else if (tab === "breakglass") void loadReviews();
    },
    10_000,
    [tab, loadRoles, loadGrants, loadReviews],
  );

  // ---------- roles tab state ----------
  const [editingRole, setEditingRole] = useState<Role | "new" | null>(null);
  const [roleName, setRoleName] = useState("");
  const [roleDesc, setRoleDesc] = useState("");
  const [rolePerms, setRolePerms] = useState<Set<string>>(new Set());

  const openRoleEditor = (r: Role | "new") => {
    setEditingRole(r);
    setRoleName(r === "new" ? "" : r.name);
    setRoleDesc(r === "new" ? "" : r.description);
    setRolePerms(new Set(r === "new" ? [] : r.permissions));
  };

  const saveRole = async () => {
    if (!roleName.trim()) {
      toast("Role name is required", "error");
      return;
    }
    const body = {
      name: roleName.trim(),
      description: roleDesc.trim(),
      permission_actions: [...rolePerms],
    };
    try {
      if (editingRole === "new") {
        await api("/roles", { method: "POST", body });
        toast(`Role ${body.name} created`, "success");
      } else if (editingRole) {
        // ASSUMPTION: PATCH /roles/{id} accepts the same body shape as POST.
        await api(`/roles/${editingRole.role_id}`, { method: "PATCH", body });
        toast(`Role ${body.name} updated`, "success");
      }
      setEditingRole(null);
      void loadRoles();
    } catch {
      // toast raised by client
    }
  };

  // ---------- grants tab state (continued) ----------
  const [revoking, setRevoking] = useState<Grant | null>(null);
  const [revokeReason, setRevokeReason] = useState("");
  const [extending, setExtending] = useState<Grant | null>(null);
  const [extendHours, setExtendHours] = useState("8");
  const [extendJust, setExtendJust] = useState("");

  const revoke = async () => {
    if (!revoking) return;
    if (!revokeReason.trim()) {
      toast("A reason is required to revoke a grant", "error");
      return;
    }
    try {
      await api(`/grants/${revoking.grant_id}/revoke`, {
        method: "POST",
        body: { reason: revokeReason.trim() },
      });
      toast("Grant revoked", "success");
      setRevoking(null);
      void loadGrants(grantFilters);
    } catch {
      // toast raised by client
    }
  };

  const extend = async () => {
    if (!extending) return;
    const hours = Number(extendHours);
    if (Number.isNaN(hours) || hours <= 0) {
      toast("Additional hours must be positive", "error");
      return;
    }
    try {
      await api(`/grants/${extending.grant_id}/extend`, {
        method: "POST",
        body: { additional_hours: hours, justification: extendJust.trim() },
      });
      toast("Grant extended", "success");
      setExtending(null);
      void loadGrants(grantFilters);
    } catch {
      // toast raised by client
    }
  };

  // ---------- break-glass tab state ----------
  const [bgRole, setBgRole] = useState("");
  const [bgReason, setBgReason] = useState("");
  const [bgIncident, setBgIncident] = useState("");
  const [bgResult, setBgResult] = useState<BreakGlassActivated | null>(null);
  const [verdictTarget, setVerdictTarget] = useState<{
    review: BreakGlassReview;
    verdict: "JUSTIFIED" | "ESCALATED";
  } | null>(null);
  const [verdictComment, setVerdictComment] = useState("");

  const activateBg = async () => {
    if (!bgRole.trim() || !bgReason.trim() || !bgIncident.trim()) {
      toast("Emergency role, reason and incident reference are all required", "error");
      return;
    }
    try {
      const res = await api<BreakGlassActivated>("/break-glass/activate", {
        method: "POST",
        body: {
          emergency_role: bgRole.trim(),
          reason: bgReason.trim(),
          incident_ref: bgIncident.trim(),
        },
      });
      setBgResult(res);
      toast(`Break-glass activated until ${fmtTs(res.expires_at)}`, "success");
    } catch {
      // toast raised by client
    }
  };

  const submitVerdict = async () => {
    if (!verdictTarget) return;
    if (!verdictComment.trim()) {
      toast("A comment is required for the verdict", "error");
      return;
    }
    try {
      await api(`/break-glass/reviews/${verdictTarget.review.bg_id}/verdict`, {
        method: "POST",
        body: { verdict: verdictTarget.verdict, comment: verdictComment.trim() },
      });
      toast("Verdict recorded", "success");
      setVerdictTarget(null);
      void loadReviews();
    } catch {
      // toast raised by client
    }
  };

  // ---------- PAM tab state ----------
  const [safeName, setSafeName] = useState("");
  const [accountId, setAccountId] = useState("");
  const [checkout, setCheckout] = useState<PamCheckout | null>(null);

  const doCheckout = async () => {
    if (!safeName.trim() || !accountId.trim()) {
      toast("Safe name and account ID are required", "error");
      return;
    }
    try {
      const res = await api<PamCheckout>("/pam/checkouts", {
        method: "POST",
        body: { safe_name: safeName.trim(), account_id: accountId.trim() },
      });
      setCheckout(res);
      toast("Credential checked out — copy it now, it is shown only once", "info");
    } catch {
      // toast raised by client
    }
  };

  const doCheckin = async () => {
    if (!checkout) return;
    try {
      await api(`/pam/checkouts/${checkout.checkout_id}/checkin`, { method: "POST" });
      toast("Credential checked in", "success");
      setCheckout(null);
    } catch {
      // toast raised by client
    }
  };

  if (visibleTabs.length === 0) {
    return (
      <div className="page">
        <div className="panel panel-empty muted">No admin capabilities granted to your roles.</div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <h2>Administration</h2>
      </div>

      <div className="tabs">
        {visibleTabs.map((t) => (
          <button key={t.id} className={`tab${tab === t.id ? " active" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "roles" && (
        <section className="panel">
          <div className="panel-header">
            <h3>Roles</h3>
            {hasPerm("ROLE_MANAGE") && (
              <button className="btn btn-buy btn-sm active" onClick={() => openRoleEditor("new")}>
                New role
              </button>
            )}
          </div>
          <DataTable<Role>
            rows={roles}
            keyFn={(r) => r.role_id}
            empty="No roles"
            columns={[
              { header: "Name", render: (r) => r.name },
              { header: "Description", render: (r) => r.description },
              { header: "Type", render: (r) => (r.built_in ? <Badge text="BUILT_IN" /> : "Custom") },
              { header: "Version", className: "num", render: (r) => fmtNum(r.version) },
              {
                header: "Permissions",
                className: "num",
                render: (r) => <span title={r.permissions.join(", ")}>{fmtNum(r.permissions.length)}</span>,
              },
              {
                header: "",
                render: (r) =>
                  hasPerm("ROLE_MANAGE") ? (
                    <button className="btn btn-ghost btn-sm" onClick={() => openRoleEditor(r)}>
                      Edit
                    </button>
                  ) : null,
              },
            ]}
          />
        </section>
      )}

      {tab === "grants" && (
        <section className="panel">
          <div className="panel-header">
            <h3>Access grants</h3>
          </div>
          <div className="filter-bar">
            <label>
              User email
              <input
                type="text"
                value={grantFilters.user_email}
                onChange={(e) => setGrantFilters((f) => ({ ...f, user_email: e.target.value }))}
                placeholder="user@demo.nomura"
              />
            </label>
            <label>
              Role
              <input
                type="text"
                value={grantFilters.role}
                onChange={(e) => setGrantFilters((f) => ({ ...f, role: e.target.value }))}
                placeholder="Role name"
              />
            </label>
            <label>
              Status
              <select
                value={grantFilters.status}
                onChange={(e) => setGrantFilters((f) => ({ ...f, status: e.target.value }))}
              >
                <option value="">All</option>
                <option value="ACTIVE">ACTIVE</option>
                <option value="EXPIRED">EXPIRED</option>
                <option value="REVOKED">REVOKED</option>
              </select>
            </label>
            <button
              className="btn btn-ghost btn-sm filter-submit"
              onClick={() => void loadGrants(grantFilters)}
            >
              Apply
            </button>
          </div>
          <DataTable<Grant>
            rows={grants}
            keyFn={(g) => g.grant_id}
            empty="No grants match the filters"
            columns={[
              { header: "User", render: (g) => g.user.email },
              { header: "Role", render: (g) => g.role.name },
              { header: "Start", render: (g) => <span className="num">{fmtTs(g.start_at)}</span> },
              { header: "End", render: (g) => <span className="num">{fmtTs(g.end_at)}</span> },
              { header: "Status", render: (g) => <Badge text={g.status} /> },
              {
                header: "",
                render: (g) =>
                  g.status.toUpperCase() === "ACTIVE" ? (
                    <span className="row-actions">
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => {
                          setExtending(g);
                          setExtendHours("8");
                          setExtendJust("");
                        }}
                      >
                        Extend
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => {
                          setRevoking(g);
                          setRevokeReason("");
                        }}
                      >
                        Revoke
                      </button>
                    </span>
                  ) : null,
              },
            ]}
          />
        </section>
      )}

      {tab === "breakglass" && (
        <>
          {hasPerm("BREAKGLASS_ELIGIBLE") && (
            <section className="panel">
              <div className="panel-header">
                <h3>Activate break-glass</h3>
              </div>
              <div className="form-grid form-grid-wide">
                <label className="form-field">
                  <span>Emergency role</span>
                  <input
                    type="text"
                    value={bgRole}
                    onChange={(e) => setBgRole(e.target.value)}
                    placeholder="e.g. EmergencyOps"
                  />
                </label>
                <label className="form-field">
                  <span>Incident reference</span>
                  <input
                    type="text"
                    value={bgIncident}
                    onChange={(e) => setBgIncident(e.target.value)}
                    placeholder="INC-2026-0001"
                  />
                </label>
                <label className="form-field form-field-full">
                  <span>Reason</span>
                  <textarea rows={2} value={bgReason} onChange={(e) => setBgReason(e.target.value)} />
                </label>
              </div>
              <div className="modal-actions">
                <button className="btn btn-danger" onClick={() => void activateBg()}>
                  Activate break-glass
                </button>
              </div>
              {bgResult && (
                <div className="callout callout-warn">
                  Break-glass active — grant {bgResult.grant_id}, expires{" "}
                  <span className="num">{fmtTs(bgResult.expires_at)}</span>.
                </div>
              )}
            </section>
          )}

          {hasPerm("BREAKGLASS_REVIEW") && (
            <section className="panel">
              <div className="panel-header">
                <h3>Break-glass review queue</h3>
              </div>
              <DataTable<BreakGlassReview>
                rows={reviews}
                keyFn={(r) => r.bg_id}
                empty="No break-glass activations to review"
                columns={[
                  { header: "User", render: (r) => r.user.email },
                  { header: "Role", render: (r) => r.emergency_role },
                  { header: "Incident", render: (r) => r.incident_ref },
                  { header: "Reason", render: (r) => <span className="cell-clip" title={r.reason}>{r.reason}</span> },
                  { header: "Activated", render: (r) => <span className="num">{fmtTs(r.activated_at)}</span> },
                  { header: "Expires", render: (r) => <span className="num">{fmtTs(r.expires_at)}</span> },
                  { header: "Review", render: (r) => <Badge text={r.review_status} /> },
                  { header: "Verdict", render: (r) => (r.verdict ? <Badge text={r.verdict} /> : "—") },
                  {
                    header: "",
                    render: (r) =>
                      !r.verdict ? (
                        <span className="row-actions">
                          <button
                            className="btn btn-buy btn-sm active"
                            onClick={() => {
                              setVerdictTarget({ review: r, verdict: "JUSTIFIED" });
                              setVerdictComment("");
                            }}
                          >
                            Justified
                          </button>
                          <button
                            className="btn btn-sell btn-sm active"
                            onClick={() => {
                              setVerdictTarget({ review: r, verdict: "ESCALATED" });
                              setVerdictComment("");
                            }}
                          >
                            Escalate
                          </button>
                        </span>
                      ) : null,
                  },
                ]}
              />
            </section>
          )}
        </>
      )}

      {tab === "pam" && (
        <section className="panel">
          <div className="panel-header">
            <h3>CyberArk credential checkout</h3>
          </div>
          <div className="form-grid">
            <label className="form-field">
              <span>Safe name</span>
              <input type="text" value={safeName} onChange={(e) => setSafeName(e.target.value)} />
            </label>
            <label className="form-field">
              <span>Account ID</span>
              <input type="text" value={accountId} onChange={(e) => setAccountId(e.target.value)} />
            </label>
          </div>
          <div className="modal-actions">
            <button className="btn btn-buy active" onClick={() => void doCheckout()}>
              Check out
            </button>
          </div>
          {checkout && (
            <div className="callout callout-warn">
              <div>
                Credential for <span className="mono">{checkout.safe_name}/{checkout.account_id}</span>{" "}
                (checked out <span className="num">{fmtTs(checkout.checked_out_at)}</span>) — shown once:
              </div>
              <code className="credential">{checkout.credential}</code>
              <div>
                <button className="btn btn-danger btn-sm" onClick={() => void doCheckin()}>
                  Check in
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      {editingRole && (
        <Modal
          title={editingRole === "new" ? "Create role" : `Edit role — ${editingRole.name}`}
          onClose={() => setEditingRole(null)}
          wide
        >
          <div className="form-grid">
            <label className="form-field">
              <span>Name</span>
              <input type="text" value={roleName} onChange={(e) => setRoleName(e.target.value)} />
            </label>
            <label className="form-field">
              <span>Description</span>
              <input type="text" value={roleDesc} onChange={(e) => setRoleDesc(e.target.value)} />
            </label>
          </div>
          <div className="perm-grid">
            {permissions.length === 0 && (
              <span className="muted">Permission catalog unavailable.</span>
            )}
            {permissions.map((p) => (
              <label key={p} className={`chip${rolePerms.has(p) ? " chip-on" : ""}`}>
                <input
                  type="checkbox"
                  checked={rolePerms.has(p)}
                  onChange={() =>
                    setRolePerms((s) => {
                      const next = new Set(s);
                      if (next.has(p)) next.delete(p);
                      else next.add(p);
                      return next;
                    })
                  }
                />
                {p}
              </label>
            ))}
          </div>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setEditingRole(null)}>
              Cancel
            </button>
            <button className="btn btn-buy active" onClick={() => void saveRole()}>
              Save role
            </button>
          </div>
        </Modal>
      )}

      {revoking && (
        <Modal title={`Revoke grant — ${revoking.role.name} for ${revoking.user.email}`} onClose={() => setRevoking(null)}>
          <label className="form-field form-field-full">
            <span>Reason (required)</span>
            <textarea rows={3} value={revokeReason} onChange={(e) => setRevokeReason(e.target.value)} />
          </label>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setRevoking(null)}>
              Cancel
            </button>
            <button className="btn btn-danger" onClick={() => void revoke()}>
              Revoke grant
            </button>
          </div>
        </Modal>
      )}

      {extending && (
        <Modal title={`Extend grant — ${extending.role.name} for ${extending.user.email}`} onClose={() => setExtending(null)}>
          <div className="form-grid">
            <label className="form-field">
              <span>Additional hours</span>
              <input type="number" min="1" value={extendHours} onChange={(e) => setExtendHours(e.target.value)} />
            </label>
            <label className="form-field form-field-full">
              <span>Justification</span>
              <textarea rows={2} value={extendJust} onChange={(e) => setExtendJust(e.target.value)} />
            </label>
          </div>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setExtending(null)}>
              Cancel
            </button>
            <button className="btn btn-buy active" onClick={() => void extend()}>
              Extend
            </button>
          </div>
        </Modal>
      )}

      {verdictTarget && (
        <Modal
          title={`${verdictTarget.verdict === "JUSTIFIED" ? "Mark justified" : "Escalate"} — break-glass by ${verdictTarget.review.user.email}`}
          onClose={() => setVerdictTarget(null)}
        >
          <label className="form-field form-field-full">
            <span>Comment (required)</span>
            <textarea rows={3} value={verdictComment} onChange={(e) => setVerdictComment(e.target.value)} />
          </label>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setVerdictTarget(null)}>
              Cancel
            </button>
            <button
              className={`btn active ${verdictTarget.verdict === "JUSTIFIED" ? "btn-buy" : "btn-sell"}`}
              onClick={() => void submitVerdict()}
            >
              Record verdict
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
