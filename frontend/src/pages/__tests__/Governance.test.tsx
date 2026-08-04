import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, downloadFile } from "../../api/client";
import { useAuth } from "../../auth";
import { Governance } from "../Governance";
import { renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn(), downloadFile: vi.fn() };
});
vi.mock("../../auth", () => ({ useAuth: vi.fn() }));

const SUMMARY = {
  active_grants: 12,
  pending_approvals: 3,
  oldest_age_hours: 5,
  grants_expiring_24h: 1,
  break_glass_pending_review: 2,
  authorization_denials_24h: 0,
  recent_break_glass: [
    {
      bg_id: "bg-1",
      user: { email: "ops@demo.nomura" },
      emergency_role: "EmergencyOps",
      reason: "prod down",
      incident_ref: "INC-1",
      activated_at: "2026-08-01T10:00:00Z",
      expires_at: "2026-08-01T14:00:00Z",
      review_status: "PENDING_REVIEW",
      verdict: null,
    },
  ],
};

const HEALTH = {
  integrations: [
    { name: "CyberArk", status: "UP", last_success: "2026-08-01T09:00:00Z", detail: "mock: cyberark" },
    { name: "SMTP", status: "DOWN", last_success: null, detail: "unreachable" },
  ],
  outbox_unpublished: 0,
  stp_exceptions: [{ execution_id: "ex-1", lifecycle_state: "EXECUTED", age_seconds: 95, reason: "dropped event" }],
};

const SETTLEMENTS = {
  items: [
    {
      settlement_id: "si-1",
      execution_id: "ex-1",
      portfolio_id: "pf-1",
      portfolio_name: "Alpha Book",
      instrument_symbol: "AAPL",
      side: "BUY",
      quantity: 50,
      price: 190.5,
      value: 9_525,
      lifecycle_state: "SETTLED",
      created_at: "2026-08-01T10:00:00Z",
      settled_at: "2026-08-01T10:05:00Z",
    },
  ],
  next_cursor: null,
};

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/admin/governance-summary") return SUMMARY;
    if (path === "/admin/health") return HEALTH;
    if (path === "/settlements") return SETTLEMENTS;
    if (path.startsWith("/settlements/exceptions/")) return undefined; // retry
    throw new Error(`unexpected api call ${path}`);
  });
}

function stubPerms(perms: string[]) {
  vi.mocked(useAuth).mockReturnValue({ hasPerm: (...ps: string[]) => ps.some((p) => perms.includes(p)) } as never);
}

describe("Governance page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(useAuth).mockReset();
    vi.mocked(downloadFile).mockReset();
    stubApi();
  });

  it("renders summary cards, health tiles, exceptions and the settlement lane", async () => {
    stubPerms(["GOVERNANCE_VIEW", "INTEGRATION_MONITOR", "TRADE_VIEW", "STP_EXCEPTION_HANDLE"]);
    renderUI(<Governance />);

    await screen.findByText("CyberArk");
    expect(screen.getByText("12")).toBeInTheDocument(); // active grants
    expect(screen.getByText("oldest 5 h")).toBeInTheDocument();
    expect(screen.getByText("ops@demo.nomura")).toBeInTheDocument(); // recent break-glass
    expect(screen.getByText("mock")).toBeInTheDocument(); // mock integration badge
    expect(screen.getByText(/ex-1 · EXECUTED · dropped event · age 95s/)).toBeInTheDocument();
    expect(screen.getByText("$9,525.00")).toBeInTheDocument(); // settlement value
    expect(screen.getByText("Alpha Book")).toBeInTheDocument();
  });

  it("retry re-publishes the dropped execution event", async () => {
    stubPerms(["GOVERNANCE_VIEW", "INTEGRATION_MONITOR", "TRADE_VIEW", "STP_EXCEPTION_HANDLE"]);
    renderUI(<Governance />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByText("Execution ex-1 re-queued for settlement", { selector: ".toast" });
    expect(api).toHaveBeenCalledWith("/settlements/exceptions/ex-1/retry", { method: "POST" });
  });

  it("hides retry without STP_EXCEPTION_HANDLE", async () => {
    stubPerms(["INTEGRATION_MONITOR"]);
    renderUI(<Governance />);
    await screen.findByText("CyberArk");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    // sections stay perm-scoped: no summary without GOVERNANCE_VIEW
    expect(screen.queryByText("Active grants")).not.toBeInTheDocument();
  });

  it("access review downloads the CSV", async () => {
    stubPerms(["GOVERNANCE_VIEW"]);
    renderUI(<Governance />);
    fireEvent.click(await screen.findByRole("button", { name: "Access review CSV" }));
    expect(downloadFile).toHaveBeenCalledWith("/admin/access-review", { format: "csv" }, "access-review.csv");
  });
});
