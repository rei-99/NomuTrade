import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { Grant, RestrictedInstrument, Role } from "../../api/types";
import { useAuth } from "../../auth";
import { Admin } from "../Admin";
import { renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});
vi.mock("../../auth", () => ({ useAuth: vi.fn() }));

const ROLES: Role[] = [
  { role_id: "role-1", name: "Trader", description: "Can trade", built_in: true, version: 3, permission_actions: ["ORDER_SUBMIT"] },
  { role_id: "role-2", name: "Ops", description: "Operations", built_in: false, version: 1, permission_actions: ["STP_EXCEPTION_HANDLE"] },
];

const GRANTS: Grant[] = [
  {
    grant_id: "g-1",
    user: { email: "trader@demo.nomura", display_name: "Demo Trader" },
    role: { role_id: "role-1", name: "Trader" },
    start_at: "2026-08-01T00:00:00Z",
    end_at: "2026-08-02T00:00:00Z",
    status: "ACTIVE",
  },
];

const RESTRICTED: RestrictedInstrument[] = [
  { symbol: "TSLA", reason: "Compliance hold", active: true, created_by: "secadmin@demo.nomura", created_at: "2026-08-01T09:00:00Z" },
];

const ALL_PERMS = ["ROLE_MANAGE", "ROLE_VIEW", "GRANT_VIEW", "GRANT_MANAGE", "BREAKGLASS_ELIGIBLE", "BREAKGLASS_REVIEW", "PAM_CHECKOUT"];

function stubPerms(perms: string[] = ALL_PERMS) {
  vi.mocked(useAuth).mockReturnValue({ hasPerm: (...ps: string[]) => ps.some((p) => perms.includes(p)) } as never);
}

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/roles") return ROLES;
    if (path === "/permissions") return [{ action: "ORDER_SUBMIT" }, "AUDIT_VIEW"];
    if (path === "/grants") return { items: GRANTS, next_cursor: null };
    if (path === "/break-glass/reviews") return { items: [], next_cursor: null };
    if (path === "/restricted-instruments") return { items: RESTRICTED, next_cursor: null };
    if (path.startsWith("/restricted-instruments/")) return undefined; // DELETE
    if (path.startsWith("/grants/")) return undefined; // revoke / extend
    if (path.startsWith("/roles/")) return undefined; // PATCH
    if (path.startsWith("/pam/")) return undefined;
    if (path.startsWith("/break-glass/")) return undefined;
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("Admin page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(useAuth).mockReset();
    stubPerms();
    stubApi();
    window.history.replaceState({}, "", "/admin");
  });

  it("renders the tabs the user's permissions unlock", async () => {
    renderUI(<Admin />);
    await screen.findByText("Trader");
    for (const tab of ["Roles", "Grants", "Break-glass", "PAM", "Restricted"]) {
      expect(screen.getByRole("button", { name: tab })).toBeInTheDocument();
    }
  });

  it("with only PAM_CHECKOUT the PAM tab is the whole page", async () => {
    stubPerms(["PAM_CHECKOUT"]);
    renderUI(<Admin />);
    await screen.findByText("CyberArk credential checkout");
    expect(screen.queryByRole("button", { name: "Roles" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Grants" })).not.toBeInTheDocument();
  });

  it("with no admin permissions a notice replaces the tabs", () => {
    stubPerms([]);
    renderUI(<Admin />);
    expect(screen.getByText("No admin capabilities granted to your roles.")).toBeInTheDocument();
  });

  it("roles table renders; edit opens prefilled and PATCHes the role", async () => {
    renderUI(<Admin />);
    await screen.findByText("Trader");
    expect(screen.getByText("BUILT IN")).toBeInTheDocument();
    expect(screen.getByText("Custom")).toBeInTheDocument(); // Ops row

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Edit role — Trader");
    const nameInput = dialog.querySelector('input[type="text"]')!;
    expect(nameInput).toHaveValue("Trader");
    // Permission catalog normalized from mixed shapes (object + string).
    expect(dialog).toHaveTextContent("ORDER_SUBMIT");
    expect(dialog).toHaveTextContent("AUDIT_VIEW");

    fireEvent.click(dialog.querySelectorAll(".chip")[1]!.querySelector("input")!); // add AUDIT_VIEW
    fireEvent.click(screen.getByRole("button", { name: "Save role" }));

    await screen.findByText("Role Trader updated", { selector: ".toast" });
    const patch = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "PATCH");
    expect(patch?.[0]).toBe("/roles/role-1");
    expect(patch?.[1]?.body).toMatchObject({ name: "Trader", permission_actions: ["ORDER_SUBMIT", "AUDIT_VIEW"] });
  });

  it("new role POSTs the editor body", async () => {
    renderUI(<Admin />);
    fireEvent.click(await screen.findByRole("button", { name: "New role" }));
    const dialog = screen.getByRole("dialog");

    fireEvent.change(dialog.querySelector('input[type="text"]')!, { target: { value: "DeskHead" } });
    fireEvent.click(dialog.querySelectorAll(".chip")[0]!.querySelector("input")!); // ORDER_SUBMIT
    fireEvent.click(screen.getByRole("button", { name: "Save role" }));

    await screen.findByText("Role DeskHead created", { selector: ".toast" });
    const post = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "POST" && c[0] === "/roles");
    expect(post?.[1]?.body).toEqual({ name: "DeskHead", description: "", permission_actions: ["ORDER_SUBMIT"] });
  });

  it("grants tab: revoke requires a reason and posts it", async () => {
    renderUI(<Admin />);
    await screen.findByText("Trader");
    fireEvent.click(screen.getByRole("button", { name: "Grants" }));
    await screen.findByText("trader@demo.nomura");

    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    fireEvent.click(screen.getByRole("button", { name: "Revoke grant" }));
    await screen.findByText("A reason is required to revoke a grant", { selector: ".toast" });

    fireEvent.change(screen.getByRole("dialog").querySelector("textarea")!, { target: { value: "left the desk" } });
    fireEvent.click(screen.getByRole("button", { name: "Revoke grant" }));
    await screen.findByText("Grant revoked", { selector: ".toast" });
    const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/grants/g-1/revoke");
    expect(post?.[1]?.body).toEqual({ reason: "left the desk" });
  });

  it("grants tab: extend posts additional hours", async () => {
    renderUI(<Admin />);
    await screen.findByText("Trader");
    fireEvent.click(screen.getByRole("button", { name: "Grants" }));
    await screen.findByText("trader@demo.nomura");

    fireEvent.click(screen.getByRole("button", { name: "Extend" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(dialog.querySelector('input[type="number"]')!, { target: { value: "24" } });
    // the modal's own Extend button is the second one with that name now
    const extendBtns = screen.getAllByRole("button", { name: "Extend" });
    fireEvent.click(extendBtns[extendBtns.length - 1]!);

    await screen.findByText("Grant extended", { selector: ".toast" });
    const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/grants/g-1/extend");
    expect(post?.[1]?.body).toMatchObject({ additional_hours: 24 });
  });

  it("restricted tab: add uppercases the symbol; remove DELETEs it", async () => {
    renderUI(<Admin />);
    await screen.findByText("Trader");
    fireEvent.click(screen.getByRole("button", { name: "Restricted" }));
    await screen.findByText("Compliance hold");

    fireEvent.change(screen.getByPlaceholderText("e.g. TSLA"), { target: { value: "goog" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. Compliance hold"), { target: { value: "Merger blackout" } });
    fireEvent.click(screen.getByRole("button", { name: "Restrict" }));

    const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/restricted-instruments" && c[1]?.method === "POST");
    expect(post?.[1]?.body).toEqual({ symbol: "GOOG", reason: "Merger blackout" });

    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(vi.mocked(api).mock.calls.some((c) => c[0] === "/restricted-instruments/TSLA" && c[1]?.method === "DELETE")).toBe(true);
  });

  it("PAM tab: checkout shows the credential once; check-in clears it", async () => {
    stubPerms(["PAM_CHECKOUT"]);
    vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
      if (path === "/pam/checkouts" && opts?.method === "POST") {
        return { checkout_id: "c-1", safe_name: "Infrastructure", account_id: "root", credential: "s3cr3t!", checked_out_at: "2026-08-01T10:00:00Z" };
      }
      if (String(path).endsWith("/checkin")) return undefined;
      throw new Error(`unexpected api call ${path}`);
    });
    renderUI(<Admin />);
    await screen.findByText("CyberArk credential checkout");

    fireEvent.change(screen.getByLabelText("Safe name"), { target: { value: "Infrastructure" } });
    fireEvent.change(screen.getByLabelText("Account ID"), { target: { value: "root" } });
    fireEvent.click(screen.getByRole("button", { name: "Check out" }));

    await screen.findByText("s3cr3t!");
    const post = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "POST");
    expect(post?.[1]?.body).toEqual({ safe_name: "Infrastructure", account_id: "root" });

    fireEvent.click(screen.getByRole("button", { name: "Check in" }));
    await waitFor(() => expect(screen.queryByText("s3cr3t!")).not.toBeInTheDocument());
    expect(vi.mocked(api).mock.calls.some((c) => String(c[0]).endsWith("/checkin"))).toBe(true);
  });

  it("break-glass tab: activate posts role/reason/incident", async () => {
    stubPerms(["BREAKGLASS_ELIGIBLE", "BREAKGLASS_REVIEW"]);
    vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
      if (path === "/break-glass/activate" && opts?.method === "POST") {
        return { bg_id: "bg-1", grant_id: "g-9", expires_at: "2026-08-01T14:00:00Z" };
      }
      if (path === "/break-glass/reviews") return { items: [], next_cursor: null };
      throw new Error(`unexpected api call ${path}`);
    });
    renderUI(<Admin />);
    await screen.findByText("Break-glass review queue");

    fireEvent.click(screen.getByRole("button", { name: "Activate break-glass" }));
    await screen.findByText("Emergency role, reason and incident reference are all required", { selector: ".toast" });

    fireEvent.change(screen.getByLabelText("Emergency role"), { target: { value: "EmergencyOps" } });
    fireEvent.change(screen.getByLabelText("Incident reference"), { target: { value: "INC-1" } });
    fireEvent.change(document.querySelector("textarea")!, { target: { value: "prod down" } });
    fireEvent.click(screen.getByRole("button", { name: "Activate break-glass" }));

    await screen.findByText(/Break-glass active — grant g-9/);
    const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/break-glass/activate");
    expect(post?.[1]?.body).toEqual({ emergency_role: "EmergencyOps", reason: "prod down", incident_ref: "INC-1" });
  });
});
