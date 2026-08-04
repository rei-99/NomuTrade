import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { ApprovalItem } from "../../api/types";
import { Approvals } from "../Approvals";
import { renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

const ITEM: ApprovalItem = {
  step_id: "s-1",
  level: 1,
  request: {
    request_id: "ar-1",
    requester: { email: "trader@demo.nomura", display_name: "Demo Trader" },
    on_behalf_of: null,
    role: { role_id: "role-2", name: "Auditor" },
    justification: "Quarterly review access",
    requested_duration_hours: 8,
    status: "PENDING",
    created_at: "2026-08-01T09:00:00Z",
    decided_at: null,
    steps: [],
  },
};

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/approvals") return { items: [ITEM], next_cursor: null };
    if (path.startsWith("/approvals/")) return undefined; // decision POST
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("Approvals page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    stubApi();
  });

  it("renders the pending approvals inbox", async () => {
    renderUI(<Approvals />);
    await screen.findByText("Quarterly review access");
    expect(screen.getByText("Demo Trader")).toBeInTheDocument();
    expect(screen.getByText("Auditor")).toBeInTheDocument();
    expect(screen.getByText("L1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("approve opens the modal; a comment is mandatory before the decision posts", async () => {
    renderUI(<Approvals />);
    await screen.findByText("Quarterly review access");

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Approve request — Auditor for trader@demo.nomura");

    fireEvent.click(screen.getByRole("button", { name: "Confirm approved" }));
    await screen.findByText("A comment is mandatory for approval decisions", { selector: ".toast" });
    expect(vi.mocked(api).mock.calls.some((c) => c[1]?.method === "POST")).toBe(false);

    fireEvent.change(dialog.querySelector("textarea")!, { target: { value: "  looks fine  " } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm approved" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const post = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "POST");
    expect(post?.[0]).toBe("/approvals/s-1/decision");
    expect(post?.[1]?.body).toEqual({ decision: "APPROVED", comment: "looks fine" });
  });

  it("reject posts the REJECTED decision", async () => {
    renderUI(<Approvals />);
    await screen.findByText("Quarterly review access");

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Reject request — Auditor for trader@demo.nomura");
    fireEvent.change(screen.getByRole("dialog").querySelector("textarea")!, { target: { value: "no" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm rejected" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const post = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "POST");
    expect(post?.[1]?.body).toEqual({ decision: "REJECTED", comment: "no" });
  });
});
