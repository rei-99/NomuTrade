import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  BreakGlassActivated,
  BreakGlassReview,
  Grant,
  ListResponse,
  PamCheckout,
  PermissionInfo,
  RestrictedInstrument,
  Role,
} from "../api/types";
import { useAuth } from "../auth";
import { DataTable } from "../components/DataTable";
import { Badge } from "../components/Badge";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { fmtNum, fmtTs } from "../format";
import { usePoll } from "../hooks";
import { useT } from "../i18n";
import type { I18nKey } from "../i18n/en";

type TabId = "roles" | "grants" | "breakglass" | "pam" | "restricted";

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
  const { t } = useT();

  const visibleTabs = useMemo(() => {
    const tabs: { id: TabId; labelKey: I18nKey }[] = [];
    if (hasPerm("ROLE_MANAGE", "ROLE_VIEW")) tabs.push({ id: "roles", labelKey: "admin.tab.roles" });
    if (hasPerm("GRANT_VIEW", "GRANT_MANAGE")) tabs.push({ id: "grants", labelKey: "admin.tab.grants" });
    if (hasPerm("BREAKGLASS_ELIGIBLE", "BREAKGLASS_REVIEW"))
      tabs.push({ id: "breakglass", labelKey: "admin.tab.breakglass" });
    if (hasPerm("PAM_CHECKOUT")) tabs.push({ id: "pam", labelKey: "admin.tab.pam" });
    if (hasPerm("ROLE_MANAGE")) tabs.push({ id: "restricted", labelKey: "admin.tab.restricted" });
    return tabs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Initial tab may be deep-linked via ?tab=<id>; invalid/unknown values fall
  // back to the first visible tab.
  const [tab, setTab] = useState<TabId | null>(
    () => new URLSearchParams(window.location.search).get("tab") as TabId | null,
  );
  useEffect(() => {
    setTab((cur) =>
      cur && visibleTabs.some((ty) => ty.id === cur)
        ? cur
        : visibleTabs[0]?.id ?? null,
    );
  }, [visibleTabs]);

  // ---------- shared data ----------
  const [roles, setRoles] = useState<Role[]>([]);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [reviews, setReviews] = useState<BreakGlassReview[]>([]);
  const [restricted, setRestricted] = useState<RestrictedInstrument[]>([]);

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

  const loadRestricted = useCallback(async () => {
    const res = await api<ListResponse<RestrictedInstrument>>("/restricted-instruments");
    setRestricted(res.items);
  }, []);

  usePoll(
    () => {
      if (tab === "roles") void loadRoles();
      else if (tab === "grants") void loadGrants(grantFilters);
      else if (tab === "breakglass") void loadReviews();
      else if (tab === "restricted") void loadRestricted();
    },
    10_000,
    [tab, loadRoles, loadGrants, loadReviews, loadRestricted],
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
      toast(t("admin.roles.nameRequired"), "error");
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
        toast(t("admin.roles.created", { name: body.name }), "success");
      } else if (editingRole) {
        // ASSUMPTION: PATCH /roles/{id} accepts the same body shape as POST.
        await api(`/roles/${editingRole.role_id}`, { method: "PATCH", body });
        toast(t("admin.roles.updated", { name: body.name }), "success");
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
      toast(t("admin.grants.revokeRequired"), "error");
      return;
    }
    try {
      await api(`/grants/${revoking.grant_id}/revoke`, {
        method: "POST",
        body: { reason: revokeReason.trim() },
      });
      toast(t("admin.grants.revoked"), "success");
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
      toast(t("admin.grants.hoursInvalid"), "error");
      return;
    }
    try {
      await api(`/grants/${extending.grant_id}/extend`, {
        method: "POST",
        body: { additional_hours: hours, justification: extendJust.trim() },
      });
      toast(t("admin.grants.extended"), "success");
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
      toast(t("admin.bg.required"), "error");
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
      toast(t("admin.bg.activated", { ts: fmtTs(res.expires_at) }), "success");
    } catch {
      // toast raised by client
    }
  };

  const submitVerdict = async () => {
    if (!verdictTarget) return;
    if (!verdictComment.trim()) {
      toast(t("admin.bg.commentRequired"), "error");
      return;
    }
    try {
      await api(`/break-glass/reviews/${verdictTarget.review.bg_id}/verdict`, {
        method: "POST",
        body: { verdict: verdictTarget.verdict, comment: verdictComment.trim() },
      });
      toast(t("admin.bg.verdictDone"), "success");
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
      toast(t("admin.pam.required"), "error");
      return;
    }
    try {
      const res = await api<PamCheckout>("/pam/checkouts", {
        method: "POST",
        body: { safe_name: safeName.trim(), account_id: accountId.trim() },
      });
      setCheckout(res);
      toast(t("admin.pam.checkedOut"), "info");
    } catch {
      // toast raised by client
    }
  };

  const doCheckin = async () => {
    if (!checkout) return;
    try {
      await api(`/pam/checkouts/${checkout.checkout_id}/checkin`, { method: "POST" });
      toast(t("admin.pam.checkedIn"), "success");
      setCheckout(null);
    } catch {
      // toast raised by client
    }
  };

  // ---------- restricted instruments tab state ----------
  const [restSymbol, setRestSymbol] = useState("");
  const [restReason, setRestReason] = useState("");

  const addRestriction = async () => {
    if (!restSymbol.trim()) {
      toast(t("admin.restricted.symbolRequired"), "error");
      return;
    }
    try {
      await api<RestrictedInstrument>("/restricted-instruments", {
        method: "POST",
        body: { symbol: restSymbol.trim().toUpperCase(), reason: restReason.trim() },
      });
      toast(t("admin.restricted.added", { symbol: restSymbol.trim().toUpperCase() }), "success");
      setRestSymbol("");
      setRestReason("");
      void loadRestricted();
    } catch {
      // toast raised by client
    }
  };

  const removeRestriction = async (symbol: string) => {
    try {
      await api(`/restricted-instruments/${encodeURIComponent(symbol)}`, { method: "DELETE" });
      toast(t("admin.restricted.removed", { symbol }), "success");
      void loadRestricted();
    } catch {
      // toast raised by client
    }
  };

  if (visibleTabs.length === 0) {
    return (
      <div className="page">
        <div className="panel panel-empty muted">{t("admin.noCaps")}</div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("admin.title")}</h2>
      </div>

      <div className="tabs">
        {visibleTabs.map((ty) => (
          <button key={ty.id} className={`tab${tab === ty.id ? " active" : ""}`} onClick={() => setTab(ty.id)}>
            {t(ty.labelKey)}
          </button>
        ))}
      </div>

      {tab === "roles" && (
        <section className="panel">
          <div className="panel-header">
            <h3>{t("admin.tab.roles")}</h3>
            {hasPerm("ROLE_MANAGE") && (
              <button className="btn btn-buy btn-sm active" onClick={() => openRoleEditor("new")}>
                {t("admin.roles.new")}
              </button>
            )}
          </div>
          <DataTable<Role>
            rows={roles}
            keyFn={(r) => r.role_id}
            empty={t("admin.roles.empty")}
            columns={[
              { header: t("admin.roles.name"), render: (r) => r.name },
              { header: t("admin.roles.description"), render: (r) => r.description },
              { header: t("common.type"), render: (r) => (r.built_in ? <Badge text="BUILT_IN" /> : t("admin.roles.custom")) },
              { header: t("admin.roles.version"), className: "num", render: (r) => fmtNum(r.version) },
              {
                header: t("admin.roles.permissions"),
                className: "num",
                render: (r) => <span title={r.permissions.join(", ")}>{fmtNum(r.permissions.length)}</span>,
              },
              {
                header: "",
                render: (r) =>
                  hasPerm("ROLE_MANAGE") ? (
                    <button className="btn btn-ghost btn-sm" onClick={() => openRoleEditor(r)}>
                      {t("admin.roles.editBtn")}
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
            <h3>{t("admin.grants.title")}</h3>
          </div>
          <div className="filter-bar">
            <label>
              {t("admin.grants.userEmail")}
              <input
                type="text"
                value={grantFilters.user_email}
                onChange={(e) => setGrantFilters((f) => ({ ...f, user_email: e.target.value }))}
                placeholder="user@demo.nomura"
              />
            </label>
            <label>
              {t("admin.grants.role")}
              <input
                type="text"
                value={grantFilters.role}
                onChange={(e) => setGrantFilters((f) => ({ ...f, role: e.target.value }))}
                placeholder="Role name"
              />
            </label>
            <label>
              {t("admin.grants.status")}
              <select
                value={grantFilters.status}
                onChange={(e) => setGrantFilters((f) => ({ ...f, status: e.target.value }))}
              >
                <option value="">{t("common.all")}</option>
                <option value="ACTIVE">ACTIVE</option>
                <option value="EXPIRED">EXPIRED</option>
                <option value="REVOKED">REVOKED</option>
              </select>
            </label>
            <button
              className="btn btn-ghost btn-sm filter-submit"
              onClick={() => void loadGrants(grantFilters)}
            >
              {t("common.apply")}
            </button>
          </div>
          <DataTable<Grant>
            rows={grants}
            keyFn={(g) => g.grant_id}
            empty={t("admin.grants.empty")}
            columns={[
              { header: t("admin.grants.user"), render: (g) => g.user.email },
              { header: t("admin.grants.role"), render: (g) => g.role.name },
              { header: t("admin.grants.start"), render: (g) => <span className="num">{fmtTs(g.start_at)}</span> },
              { header: t("admin.grants.end"), render: (g) => <span className="num">{fmtTs(g.end_at)}</span> },
              { header: t("admin.grants.status"), render: (g) => <Badge text={g.status} /> },
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
                        {t("admin.grants.extend")}
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => {
                          setRevoking(g);
                          setRevokeReason("");
                        }}
                      >
                        {t("admin.grants.revoke")}
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
                <h3>{t("admin.bg.activate")}</h3>
              </div>
              <div className="form-grid form-grid-wide">
                <label className="form-field">
                  <span>{t("admin.bg.role")}</span>
                  <input
                    type="text"
                    value={bgRole}
                    onChange={(e) => setBgRole(e.target.value)}
                    placeholder={t("admin.bg.rolePlaceholder")}
                  />
                </label>
                <label className="form-field">
                  <span>{t("admin.bg.incident")}</span>
                  <input
                    type="text"
                    value={bgIncident}
                    onChange={(e) => setBgIncident(e.target.value)}
                    placeholder="INC-2026-0001"
                  />
                </label>
                <label className="form-field form-field-full">
                  <span>{t("admin.bg.reason")}</span>
                  <textarea rows={2} value={bgReason} onChange={(e) => setBgReason(e.target.value)} />
                </label>
              </div>
              <div className="modal-actions">
                <button className="btn btn-danger" onClick={() => void activateBg()}>
                  {t("admin.bg.action")}
                </button>
              </div>
              {bgResult && (
                <div className="callout callout-warn">
                  {t("admin.bg.activeNote", { id: bgResult.grant_id, ts: fmtTs(bgResult.expires_at) })}
                </div>
              )}
            </section>
          )}

          {hasPerm("BREAKGLASS_REVIEW") && (
            <section className="panel">
              <div className="panel-header">
                <h3>{t("admin.bg.queue")}</h3>
              </div>
              <DataTable<BreakGlassReview>
                rows={reviews}
                keyFn={(r) => r.bg_id}
                empty={t("admin.bg.empty")}
                columns={[
                  { header: t("admin.bg.user"), render: (r) => r.user.email },
                  { header: t("admin.bg.role"), render: (r) => r.emergency_role },
                  { header: t("gov.incident"), render: (r) => r.incident_ref },
                  { header: t("admin.bg.reasonCol"), render: (r) => <span className="cell-clip" title={r.reason}>{r.reason}</span> },
                  { header: t("admin.bg.activatedCol"), render: (r) => <span className="num">{fmtTs(r.activated_at)}</span> },
                  { header: t("admin.bg.expires"), render: (r) => <span className="num">{fmtTs(r.expires_at)}</span> },
                  { header: t("admin.bg.review"), render: (r) => <Badge text={r.review_status} /> },
                  { header: t("admin.bg.verdict"), render: (r) => (r.verdict ? <Badge text={r.verdict} /> : "—") },
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
                            {t("admin.bg.justified")}
                          </button>
                          <button
                            className="btn btn-sell btn-sm active"
                            onClick={() => {
                              setVerdictTarget({ review: r, verdict: "ESCALATED" });
                              setVerdictComment("");
                            }}
                          >
                            {t("admin.bg.escalate")}
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
            <h3>{t("admin.pam.title")}</h3>
          </div>
          <div className="form-grid">
            <label className="form-field">
              <span>{t("admin.pam.safe")}</span>
              <input type="text" value={safeName} onChange={(e) => setSafeName(e.target.value)} />
            </label>
            <label className="form-field">
              <span>{t("admin.pam.account")}</span>
              <input type="text" value={accountId} onChange={(e) => setAccountId(e.target.value)} />
            </label>
          </div>
          <div className="modal-actions">
            <button className="btn btn-buy active" onClick={() => void doCheckout()}>
              {t("admin.pam.checkout")}
            </button>
          </div>
          {checkout && (
            <div className="callout callout-warn">
              <div>
                {t("admin.pam.shownOnce", {
                  path: `${checkout.safe_name}/${checkout.account_id}`,
                  ts: fmtTs(checkout.checked_out_at),
                })}
              </div>
              <code className="credential">{checkout.credential}</code>
              <div>
                <button className="btn btn-danger btn-sm" onClick={() => void doCheckin()}>
                  {t("admin.pam.checkin")}
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      {tab === "restricted" && (
        <section className="panel">
          <div className="panel-header">
            <h3>{t("admin.restricted.title")}</h3>
          </div>
          <div className="filter-bar">
            <label>
              {t("admin.restricted.symbol")}
              <input
                type="text"
                value={restSymbol}
                onChange={(e) => setRestSymbol(e.target.value.toUpperCase())}
                placeholder="e.g. TSLA"
              />
            </label>
            <label>
              {t("admin.restricted.reason")}
              <input
                type="text"
                value={restReason}
                onChange={(e) => setRestReason(e.target.value)}
                placeholder="e.g. Compliance hold"
              />
            </label>
            <button
              className="btn btn-buy active btn-sm filter-submit"
              onClick={() => void addRestriction()}
            >
              {t("admin.restricted.add")}
            </button>
          </div>
          <DataTable<RestrictedInstrument>
            rows={restricted}
            keyFn={(r) => r.symbol}
            empty={t("admin.restricted.empty")}
            columns={[
              { header: t("admin.restricted.symbol"), render: (r) => r.symbol },
              {
                header: t("admin.restricted.reason"),
                render: (r) => (
                  <span className="cell-clip" title={r.reason}>
                    {r.reason || "—"}
                  </span>
                ),
              },
              { header: t("common.status"), render: (r) => <Badge text={r.active ? "ACTIVE" : "INACTIVE"} /> },
              { header: t("admin.restricted.createdBy"), render: (r) => r.created_by },
              { header: t("common.created"), render: (r) => <span className="num">{fmtTs(r.created_at)}</span> },
              {
                header: "",
                render: (r) =>
                  r.active ? (
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={() => void removeRestriction(r.symbol)}
                    >
                      {t("admin.restricted.remove")}
                    </button>
                  ) : null,
              },
            ]}
          />
        </section>
      )}

      {editingRole && (
        <Modal
          title={editingRole === "new" ? t("admin.roles.create") : t("admin.roles.edit", { name: editingRole.name })}
          onClose={() => setEditingRole(null)}
          wide
        >
          <div className="form-grid">
            <label className="form-field">
              <span>{t("admin.roles.name")}</span>
              <input type="text" value={roleName} onChange={(e) => setRoleName(e.target.value)} />
            </label>
            <label className="form-field">
              <span>{t("admin.roles.description")}</span>
              <input type="text" value={roleDesc} onChange={(e) => setRoleDesc(e.target.value)} />
            </label>
          </div>
          <div className="perm-grid">
            {permissions.length === 0 && (
              <span className="muted">{t("admin.roles.permsUnavailable")}</span>
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
              {t("common.cancel")}
            </button>
            <button className="btn btn-buy active" onClick={() => void saveRole()}>
              {t("admin.roles.saveRole")}
            </button>
          </div>
        </Modal>
      )}

      {revoking && (
        <Modal title={t("admin.grants.revokeTitle", { role: revoking.role.name, email: revoking.user.email })} onClose={() => setRevoking(null)}>
          <label className="form-field form-field-full">
            <span>{t("admin.grants.revokeReason")}</span>
            <textarea rows={3} value={revokeReason} onChange={(e) => setRevokeReason(e.target.value)} />
          </label>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setRevoking(null)}>
              {t("common.cancel")}
            </button>
            <button className="btn btn-danger" onClick={() => void revoke()}>
              {t("admin.grants.revokeAction")}
            </button>
          </div>
        </Modal>
      )}

      {extending && (
        <Modal title={t("admin.grants.extendTitle", { role: extending.role.name, email: extending.user.email })} onClose={() => setExtending(null)}>
          <div className="form-grid">
            <label className="form-field">
              <span>{t("admin.grants.additionalHours")}</span>
              <input type="number" min="1" value={extendHours} onChange={(e) => setExtendHours(e.target.value)} />
            </label>
            <label className="form-field form-field-full">
              <span>{t("admin.grants.justification")}</span>
              <textarea rows={2} value={extendJust} onChange={(e) => setExtendJust(e.target.value)} />
            </label>
          </div>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setExtending(null)}>
              {t("common.cancel")}
            </button>
            <button className="btn btn-buy active" onClick={() => void extend()}>
              {t("admin.grants.extend")}
            </button>
          </div>
        </Modal>
      )}

      {verdictTarget && (
        <Modal
          title={t(
            verdictTarget.verdict === "JUSTIFIED"
              ? "admin.bg.verdictTitle.justified"
              : "admin.bg.verdictTitle.escalated",
            { email: verdictTarget.review.user.email },
          )}
          onClose={() => setVerdictTarget(null)}
        >
          <label className="form-field form-field-full">
            <span>{t("admin.bg.comment")}</span>
            <textarea rows={3} value={verdictComment} onChange={(e) => setVerdictComment(e.target.value)} />
          </label>
          <div className="modal-actions">
            <button className="btn btn-ghost" onClick={() => setVerdictTarget(null)}>
              {t("common.cancel")}
            </button>
            <button
              className={`btn active ${verdictTarget.verdict === "JUSTIFIED" ? "btn-buy" : "btn-sell"}`}
              onClick={() => void submitVerdict()}
            >
              {t("admin.bg.recordVerdict")}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
