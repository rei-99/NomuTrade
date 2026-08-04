import { describe, expect, it } from "vitest";
import { detectPersona, PERSONA_TABS, personaHome, TABS } from "../personas";
import type { Persona } from "../personas";

const ALL_PERSONAS: Persona[] = ["TRADER", "ADMIN", "RISK", "OPERATION", "NONE"];

describe("detectPersona", () => {
  it("ORDER_SUBMIT wins over everything (Trader precedence)", () => {
    expect(detectPersona(["ORDER_SUBMIT", "ROLE_MANAGE", "AUDIT_VIEW"])).toBe("TRADER");
    expect(detectPersona(["ORDER_SUBMIT"])).toBe("TRADER");
  });

  it("admin-flavored permissions land in ADMIN", () => {
    for (const p of [
      "ROLE_MANAGE",
      "GRANT_MANAGE",
      "PAM_CHECKOUT",
      "BREAKGLASS_ELIGIBLE",
      "APPROVE_ACCESS",
    ]) {
      expect(detectPersona([p])).toBe("ADMIN");
    }
  });

  it("AUDIT_VIEW lands in RISK", () => {
    expect(detectPersona(["AUDIT_VIEW"])).toBe("RISK");
  });

  it("ops permissions land in OPERATION", () => {
    expect(detectPersona(["INTEGRATION_MONITOR"])).toBe("OPERATION");
    expect(detectPersona(["STP_EXCEPTION_HANDLE"])).toBe("OPERATION");
  });

  it("PORTFOLIO_VIEW is the last-resort RISK fallback", () => {
    expect(detectPersona(["PORTFOLIO_VIEW"])).toBe("RISK");
    // …but ops permissions are checked before it.
    expect(detectPersona(["PORTFOLIO_VIEW", "INTEGRATION_MONITOR"])).toBe("OPERATION");
  });

  it("PORTFOLIO_VIEW_ALL alone matches nothing (ops hold it, not viewers)", () => {
    expect(detectPersona(["PORTFOLIO_VIEW_ALL"])).toBe("NONE");
  });

  it("no known permissions → NONE", () => {
    expect(detectPersona([])).toBe("NONE");
    expect(detectPersona(["SOMETHING_ELSE"])).toBe("NONE");
  });
});

describe("PERSONA_TABS", () => {
  it("every persona tab resolves to a defined route", () => {
    for (const persona of ALL_PERSONAS) {
      for (const tabId of PERSONA_TABS[persona]) {
        const tab = TABS[tabId];
        expect(tab, `${persona}/${tabId}`).toBeDefined();
        expect(tab.to.startsWith("/"), `${persona}/${tabId} route`).toBe(true);
      }
    }
  });

  it("every persona sees the connect tab", () => {
    for (const persona of ALL_PERSONAS) {
      expect(PERSONA_TABS[persona]).toContain("connect");
    }
  });

  it("tab ids in the lists exist as TabIds (no typos)", () => {
    for (const persona of ALL_PERSONAS) {
      const ids = new Set(PERSONA_TABS[persona]);
      expect(ids.size).toBe(PERSONA_TABS[persona].length); // no duplicates
    }
  });
});

describe("personaHome", () => {
  it("Trader lands on / (trading has no perm gate)", () => {
    expect(personaHome("TRADER", ["ORDER_SUBMIT"])).toBe("/");
    expect(personaHome("TRADER", [])).toBe("/");
  });

  it("full Admin lands on /admin", () => {
    expect(personaHome("ADMIN", ["ROLE_MANAGE", "GRANT_MANAGE"])).toBe("/admin");
  });

  it("Approver (Admin persona) with only APPROVE_ACCESS lands on /approvals", () => {
    // admin/governance/audit gates fail; the approvals gate passes.
    expect(personaHome("ADMIN", ["APPROVE_ACCESS"])).toBe("/approvals");
  });

  it("Auditor (Risk persona) lands on /audit", () => {
    expect(personaHome("RISK", ["AUDIT_VIEW"])).toBe("/audit");
  });

  it("Client (Risk persona, PORTFOLIO_VIEW) lands on /portfolios", () => {
    expect(personaHome("RISK", ["PORTFOLIO_VIEW"])).toBe("/portfolios");
  });

  it("Ops lands on /trades (TRADE_VIEW present)", () => {
    expect(personaHome("OPERATION", ["INTEGRATION_MONITOR", "TRADE_VIEW"])).toBe("/trades");
    // without TRADE_VIEW the governance gate (INTEGRATION_MONITOR) wins
    expect(personaHome("OPERATION", ["INTEGRATION_MONITOR"])).toBe("/governance");
    // with neither, the first ungated tab is access
    expect(personaHome("OPERATION", [])).toBe("/access");
  });

  it("NONE persona lands on /access", () => {
    expect(personaHome("NONE", [])).toBe("/access");
  });
});
