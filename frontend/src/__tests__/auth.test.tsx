import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, getToken, setToken } from "../api/client";
import type { MeResponse } from "../api/types";
import { wsClient } from "../api/ws";
import { AuthProvider, useAuth } from "../auth";

vi.mock("../api/client", () => ({
  api: vi.fn(),
  getToken: vi.fn(),
  setToken: vi.fn(),
}));

vi.mock("../api/ws", () => ({
  wsClient: { start: vi.fn(), stop: vi.fn() },
}));

function me(permissions: string[]): MeResponse {
  return {
    user: { upn: "trader@demo.nomura", display_name: "Demo Trader", email: "trader@demo.nomura" },
    roles: ["Trader"],
    permissions,
  };
}

/** Minimal consumer that exposes the context value to assertions. */
function Probe() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(auth.loading)}</span>
      <span data-testid="email">{auth.me?.user.email ?? "none"}</span>
      <span data-testid="persona">{auth.persona}</span>
      <span data-testid="trade">{String(auth.hasPerm("ORDER_SUBMIT"))}</span>
      <span data-testid="any">{String(auth.hasPerm("ROLE_MANAGE", "ORDER_SUBMIT"))}</span>
      <span data-testid="none">{String(auth.hasPerm("ROLE_MANAGE"))}</span>
      <span data-testid="empty">{String(auth.hasPerm())}</span>
      <button onClick={() => void auth.login("trader@demo.nomura", "demo1234")}>do-login</button>
      <button onClick={() => void auth.logout()}>do-logout</button>
    </div>
  );
}

function renderAuth() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

describe("AuthProvider", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(getToken).mockReset().mockReturnValue(null);
    vi.mocked(setToken).mockClear();
    vi.mocked(wsClient.start).mockClear();
    vi.mocked(wsClient.stop).mockClear();
  });

  it("without a stored token: not loading, no session, socket stopped", async () => {
    renderAuth();
    // loading flips to false without any /auth/me call
    await screen.findByText("none");
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
    expect(screen.getByTestId("email")).toHaveTextContent("none");
    expect(api).not.toHaveBeenCalled();
    expect(wsClient.stop).toHaveBeenCalled();
    expect(wsClient.start).not.toHaveBeenCalled();
  });

  it("with a token: fetches /auth/me and derives the persona from permissions", async () => {
    vi.mocked(getToken).mockReturnValue("tok-1");
    vi.mocked(api).mockResolvedValue(me(["ORDER_SUBMIT", "PORTFOLIO_VIEW"]) as never);
    renderAuth();

    await screen.findByText("trader@demo.nomura");
    expect(api).toHaveBeenCalledWith("/auth/me", { skipErrorToast: true });
    expect(screen.getByTestId("persona")).toHaveTextContent("TRADER");
    expect(wsClient.start).toHaveBeenCalledTimes(1);
  });

  it("drops the token when the session profile fetch fails", async () => {
    vi.mocked(getToken).mockReturnValue("stale-tok");
    vi.mocked(api).mockRejectedValue(new Error("401") as never);
    renderAuth();

    await act(async () => {});
    expect(setToken).toHaveBeenCalledWith(null);
    expect(screen.getByTestId("email")).toHaveTextContent("none");
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });

  it("hasPerm: ANY-of semantics, true with no args, false without a session", async () => {
    vi.mocked(getToken).mockReturnValue("tok-1");
    vi.mocked(api).mockResolvedValue(me(["ORDER_SUBMIT"]) as never);
    renderAuth();
    await screen.findByText("trader@demo.nomura");

    expect(screen.getByTestId("trade")).toHaveTextContent("true");
    expect(screen.getByTestId("any")).toHaveTextContent("true"); // union semantics
    expect(screen.getByTestId("none")).toHaveTextContent("false");
    expect(screen.getByTestId("empty")).toHaveTextContent("true");
  });

  it("login posts credentials, stores the token, loads the profile, resolves the persona", async () => {
    renderAuth();
    await screen.findByText("none");

    vi.mocked(api).mockImplementation(async (path: string, opts?: { body?: unknown }) => {
      if (path === "/auth/login") {
        expect(opts?.body).toEqual({ email: "trader@demo.nomura", password: "demo1234" });
        return { token: "tok-new", user: me([]).user };
      }
      if (path === "/auth/me") return me(["AUDIT_VIEW"]);
      throw new Error(`unexpected ${path}`);
    });

    await act(async () => {
      screen.getByText("do-login").click();
    });

    expect(setToken).toHaveBeenCalledWith("tok-new");
    expect(screen.getByTestId("email")).toHaveTextContent("trader@demo.nomura");
    expect(screen.getByTestId("persona")).toHaveTextContent("RISK"); // AUDIT_VIEW → RISK
    expect(wsClient.start).toHaveBeenCalled();
  });

  it("logout posts best-effort, clears token and session even when the call fails", async () => {
    vi.mocked(getToken).mockReturnValue("tok-1");
    vi.mocked(api).mockResolvedValue(me(["ORDER_SUBMIT"]) as never);
    renderAuth();
    await screen.findByText("trader@demo.nomura");

    vi.mocked(api).mockRejectedValue(new Error("network") as never);
    await act(async () => {
      screen.getByText("do-logout").click();
    });

    expect(api).toHaveBeenCalledWith("/auth/logout", { method: "POST", skipErrorToast: true });
    expect(setToken).toHaveBeenCalledWith(null);
    expect(screen.getByTestId("email")).toHaveTextContent("none");
    expect(wsClient.stop).toHaveBeenCalled();
  });

  it("useAuth throws outside the provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow("useAuth must be used within AuthProvider");
    spy.mockRestore();
  });
});
