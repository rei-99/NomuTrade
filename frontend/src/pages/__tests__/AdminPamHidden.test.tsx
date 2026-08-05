import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { useAuth } from "../../auth";
import { Admin } from "../Admin";
import { renderUI } from "../../test/utils";

// Default presentation flag (SHOW_PAM=false in features.ts): no PAM surface
// may appear for any user. The tab's own flows are covered in Admin.test.tsx
// with the flag mocked on.
vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});
vi.mock("../../auth", () => ({ useAuth: vi.fn() }));

const ALL_PERMS = [
  "ROLE_MANAGE",
  "ROLE_VIEW",
  "GRANT_VIEW",
  "GRANT_MANAGE",
  "BREAKGLASS_ELIGIBLE",
  "BREAKGLASS_REVIEW",
  "PAM_CHECKOUT",
];

describe("Admin — PAM hidden (default flag)", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(useAuth).mockReset();
    window.history.replaceState({}, "", "/admin");
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/roles") return [] as never;
      if (path === "/permissions") return [] as never;
      return { items: [], next_cursor: null } as never;
    });
    vi.mocked(useAuth).mockReturnValue({
      hasPerm: (...ps: string[]) => ps.some((p) => ALL_PERMS.includes(p)),
    } as never);
  });

  it("no PAM tab even with every permission", async () => {
    renderUI(<Admin />);
    await screen.findByRole("button", { name: "Roles" });
    expect(screen.queryByRole("button", { name: "PAM" })).not.toBeInTheDocument();
    expect(screen.queryByText(/CyberArk/i)).not.toBeInTheDocument();
  });

  it("a PAM_CHECKOUT-only user sees no admin tabs at all", async () => {
    vi.mocked(useAuth).mockReturnValue({
      hasPerm: (...ps: string[]) => ps.includes("PAM_CHECKOUT"),
    } as never);
    renderUI(<Admin />);
    expect(screen.queryByRole("button", { name: "PAM" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Roles" })).not.toBeInTheDocument();
  });
});
