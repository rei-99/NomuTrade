import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { AccessRequest, Role } from "../../api/types";
import { Access } from "../Access";
import { renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

const ROLES: Role[] = [
  { role_id: "role-1", name: "Trader", description: "Can trade", built_in: true, version: 1, permission_actions: ["ORDER_SUBMIT"] },
  { role_id: "role-2", name: "Auditor", description: "Can view audit", built_in: true, version: 1, permission_actions: ["AUDIT_VIEW"] },
];

function request(overrides: Partial<AccessRequest>): AccessRequest {
  return {
    request_id: "ar-1",
    requester: { email: "trader@demo.nomura", display_name: "Demo Trader" },
    on_behalf_of: null,
    role: { role_id: "role-2", name: "Auditor" },
    justification: "Need it for the quarterly review",
    requested_duration_hours: 8,
    status: "PENDING",
    created_at: "2026-08-01T09:00:00Z",
    decided_at: null,
    steps: [
      { step_id: "s-1", level: 1, approver: { email: "approver@demo.nomura", display_name: "A" }, decision: null, comment: null, decided_at: null },
    ],
    ...overrides,
  };
}

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
    if (path === "/roles") return ROLES;
    if (path === "/access-requests" && opts?.method === "POST") {
      return { request_id: "ar-9", status: "PENDING", current_level: 1, levels: [] };
    }
    if (path === "/access-requests") {
      return {
        items: [request({}), request({ request_id: "ar-2", status: "APPROVED", steps: [] })],
        next_cursor: null,
      };
    }
    if (path.startsWith("/access-requests/")) return undefined; // withdraw
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("Access page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    stubApi();
  });

  it("lists my requests with step summaries; withdraw only for non-terminal", async () => {
    renderUI(<Access />);
    await screen.findByText("L1: PENDING");
    expect(screen.getAllByText("Auditor", { selector: "td" }).length).toBe(2); // both requests
    expect(screen.getAllByText("8 h", { selector: "td" }).length).toBe(2);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0); // approved row: no steps
    expect(screen.getAllByRole("button", { name: "Withdraw" })).toHaveLength(1);
  });

  it("validates justification before submitting", async () => {
    renderUI(<Access />);
    await screen.findByText("Trader");
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await screen.findByText("Justification is required", { selector: ".toast" });
    expect(vi.mocked(api).mock.calls.some((c) => c[1]?.method === "POST")).toBe(false);
  });

  it("submits a request with role, duration, justification and on-behalf-of", async () => {
    renderUI(<Access />);
    await screen.findByText("Trader");

    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "role-2" } });
    fireEvent.change(screen.getByLabelText(/Justification/), { target: { value: "  audit prep  " } });
    fireEvent.change(screen.getByLabelText(/On behalf of/), { target: { value: "client@demo.nomura" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));

    const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/access-requests" && c[1]?.method === "POST");
    expect(post?.[1]?.body).toEqual({
      target_role: "role-2",
      justification: "audit prep",
      requested_duration_hours: 8,
      on_behalf_of: "client@demo.nomura",
    });
    await waitFor(() => expect(screen.getByLabelText(/Justification/)).toHaveValue("")); // cleared
  });

  it("withdraw posts and reloads", async () => {
    renderUI(<Access />);
    await screen.findByText("L1: PENDING");
    fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    expect(api).toHaveBeenCalledWith("/access-requests/ar-1/withdraw", { method: "POST" });
  });
});
