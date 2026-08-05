// Persona model (design 25 §U2): a presentation-layer consolidation of RBAC
// permissions into four demo personas. Detection is permission-derived (never
// role-name-derived) so custom roles land somewhere sensible. Personas only
// ever HIDE tabs — the per-permission gates stay as the safety net.

import type { I18nKey } from "./i18n/en";

export type Persona = "TRADER" | "ADMIN" | "RISK" | "OPERATION" | "NONE";

/**
 * Precedence: Trader > Admin > Risk > Operation, then a PORTFOLIO_VIEW
 * fallback into Risk; fallback NONE. Presentation-layer only: the Approver
 * joins Admin (the approvals tab lives in Admin's list) and the Client lands
 * in Risk as the closest of the four personas (view-heavy).
 */
export function detectPersona(perms: string[]): Persona {
  const has = (p: string) => perms.includes(p);
  if (has("ORDER_SUBMIT")) return "TRADER";
  if (
    has("ROLE_MANAGE") ||
    has("GRANT_MANAGE") ||
    has("PAM_CHECKOUT") ||
    has("BREAKGLASS_ELIGIBLE") ||
    has("APPROVE_ACCESS")
  ) {
    return "ADMIN";
  }
  if (has("AUDIT_VIEW")) return "RISK";
  if (has("INTEGRATION_MONITOR") || has("STP_EXCEPTION_HANDLE")) return "OPERATION";
  // Checked last so custom ops-flavored roles still win above. (Trader matches
  // earlier via ORDER_SUBMIT; Ops holds PORTFOLIO_VIEW_ALL, not PORTFOLIO_VIEW.)
  if (has("PORTFOLIO_VIEW")) return "RISK";
  return "NONE";
}

export type TabId =
  | "trading"
  | "orders"
  | "trades"
  | "alerts"
  | "reports"
  | "paper"
  | "assistant"
  | "access"
  | "notifications"
  | "connect"
  | "approvals"
  | "admin"
  | "audit"
  | "governance"
  | "portfolios";

export interface TabDef {
  id: TabId;
  to: string;
  labelKey: I18nKey;
  perms?: string[]; // ANY of these — the safety-net gate under the persona list
}

export const TABS: Record<TabId, TabDef> = {
  trading: { id: "trading", to: "/", labelKey: "nav.trading" },
  orders: { id: "orders", to: "/orders", labelKey: "nav.orders", perms: ["ORDER_VIEW", "STP_EXCEPTION_HANDLE"] },
  trades: { id: "trades", to: "/trades", labelKey: "nav.trades", perms: ["TRADE_VIEW"] },
  alerts: { id: "alerts", to: "/alerts", labelKey: "nav.alerts" },
  reports: { id: "reports", to: "/reports", labelKey: "nav.reports", perms: ["REPORT_VIEW"] },
  paper: { id: "paper", to: "/paper", labelKey: "nav.paper", perms: ["PAPER_TRADE"] },
  assistant: { id: "assistant", to: "/assistant", labelKey: "nav.assistant", perms: ["ASSISTANT_USE"] },
  access: { id: "access", to: "/access", labelKey: "nav.access" },
  notifications: { id: "notifications", to: "/notifications", labelKey: "nav.notifications" },
  // Login-only demo page: no perms gate, in every persona list below.
  connect: { id: "connect", to: "/connect", labelKey: "nav.connect" },
  approvals: { id: "approvals", to: "/approvals", labelKey: "nav.approvals", perms: ["APPROVE_ACCESS"] },
  admin: {
    id: "admin",
    to: "/admin",
    labelKey: "nav.admin",
    perms: [
      "ROLE_MANAGE",
      "ROLE_VIEW",
      "GRANT_VIEW",
      "GOVERNANCE_VIEW",
      "PAM_CHECKOUT",
      "BREAKGLASS_ELIGIBLE",
      "BREAKGLASS_REVIEW",
    ],
  },
  audit: { id: "audit", to: "/audit", labelKey: "nav.audit", perms: ["AUDIT_VIEW"] },
  governance: {
    id: "governance",
    to: "/governance",
    labelKey: "nav.governance",
    perms: ["GOVERNANCE_VIEW", "INTEGRATION_MONITOR"],
  },
  portfolios: {
    id: "portfolios",
    to: "/portfolios",
    labelKey: "nav.portfolios",
    perms: ["PORTFOLIO_VIEW", "PORTFOLIO_VIEW_ALL"],
  },
};

/** Tabs per persona — the design 26 §R1 table, amended 2026-08-05 by owner
 * decision: the trader KEEPS Access Requests. §R1 argued "access governance
 * is not their job" — correct for governing (Approvals/Admin stay gated),
 * but requesting is self-service and starts the request→approval→grant flow;
 * the trader's Access page only ever shows their own requests. */
export const PERSONA_TABS: Record<Persona, TabId[]> = {
  TRADER: [
    "trading",
    "portfolios",
    "orders",
    "trades",
    "alerts",
    "reports",
    "paper",
    "assistant",
    "access",
    "notifications",
    "connect",
  ],
  ADMIN: ["admin", "governance", "audit", "approvals", "access", "notifications", "connect"],
  // "assistant" sits after "reports": perm-gated by ASSISTANT_USE, so risk@
  // (no ASSISTANT_USE) never sees it while client@ does.
  RISK: ["portfolios", "trades", "audit", "governance", "reports", "assistant", "access", "notifications", "connect"],
  OPERATION: ["trades", "orders", "governance", "access", "notifications", "connect"],
  NONE: ["access", "notifications", "connect"],
};

/**
 * Post-login landing route (design 26 §R1): the `to` of the FIRST tab in the
 * persona's list that passes the same per-permission filter the nav applies
 * (Layout.tsx), so users land on a page they can actually open — Approver
 * (Admin persona) → /approvals, Auditor (Risk) → /audit, Client (Risk) →
 * /portfolios. Presentation-layer only; the per-permission gates stay the
 * safety net. Falls back to /access.
 */
export function personaHome(persona: Persona, perms: string[]): string {
  const first = PERSONA_TABS[persona]
    .map((id) => TABS[id])
    .find((t) => !t.perms || t.perms.some((p) => perms.includes(p)));
  return first?.to ?? "/access";
}
