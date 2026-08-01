// Persona model (design 25 §U2): a presentation-layer consolidation of RBAC
// permissions into four demo personas. Detection is permission-derived (never
// role-name-derived) so custom roles land somewhere sensible. Personas only
// ever HIDE tabs — the per-permission gates stay as the safety net.

import type { I18nKey } from "./i18n/en";

export type Persona = "TRADER" | "ADMIN" | "RISK" | "OPERATION" | "NONE";

/** Precedence: Trader > Admin > Risk > Operation; fallback NONE. */
export function detectPersona(perms: string[]): Persona {
  const has = (p: string) => perms.includes(p);
  if (has("ORDER_SUBMIT")) return "TRADER";
  if (
    has("ROLE_MANAGE") ||
    has("GRANT_MANAGE") ||
    has("PAM_CHECKOUT") ||
    has("BREAKGLASS_ELIGIBLE")
  ) {
    return "ADMIN";
  }
  if (has("AUDIT_VIEW")) return "RISK";
  if (has("INTEGRATION_MONITOR") || has("STP_EXCEPTION_HANDLE")) return "OPERATION";
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
  orders: { id: "orders", to: "/orders", labelKey: "nav.orders" },
  trades: { id: "trades", to: "/trades", labelKey: "nav.trades", perms: ["TRADE_VIEW"] },
  alerts: { id: "alerts", to: "/alerts", labelKey: "nav.alerts" },
  reports: { id: "reports", to: "/reports", labelKey: "nav.reports" },
  paper: { id: "paper", to: "/paper", labelKey: "nav.paper", perms: ["PAPER_TRADE"] },
  assistant: { id: "assistant", to: "/assistant", labelKey: "nav.assistant", perms: ["ASSISTANT_USE"] },
  access: { id: "access", to: "/access", labelKey: "nav.access" },
  notifications: { id: "notifications", to: "/notifications", labelKey: "nav.notifications" },
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

/** Tabs per persona — the design 26 §R1 table, verbatim. */
export const PERSONA_TABS: Record<Persona, TabId[]> = {
  TRADER: [
    "trading",
    "orders",
    "trades",
    "alerts",
    "reports",
    "paper",
    "assistant",
    "access",
    "notifications",
  ],
  ADMIN: ["admin", "governance", "audit", "approvals", "access", "notifications"],
  RISK: ["portfolios", "trades", "audit", "governance", "reports", "access", "notifications"],
  OPERATION: ["trades", "governance", "access", "notifications"],
  NONE: ["access", "notifications"],
};

/** Post-login landing route per persona (design 26 §R1). */
export const PERSONA_HOME: Record<Persona, string> = {
  TRADER: "/",
  ADMIN: "/admin",
  RISK: "/governance",
  OPERATION: "/governance",
  NONE: "/access",
};
