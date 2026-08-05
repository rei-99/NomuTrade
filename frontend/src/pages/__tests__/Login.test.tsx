import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth";
import { I18nProvider } from "../../i18n";
import { Login } from "../Login";

vi.mock("../../auth", () => ({ useAuth: vi.fn() }));

const CRED = { email: "trader_3@demo.nomura", password: "demo1234" };

function stubDemoCredential(response: { ok: boolean; json?: () => Promise<unknown> }) {
  vi.stubGlobal("fetch", vi.fn(async () => response) as never);
}

function renderLogin() {
  const login = vi.fn();
  vi.mocked(useAuth).mockReturnValue({ login } as never);
  render(
    <I18nProvider>
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<div>HomeMarker</div>} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
  return { login };
}

describe("Login page", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReset();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("prefills the demo credential from the backend on a fresh session", async () => {
    stubDemoCredential({ ok: true, json: async () => CRED });
    renderLogin();

    expect(await screen.findByDisplayValue(CRED.email)).toBeInTheDocument();
    expect(screen.getByDisplayValue(CRED.password)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/v1/auth/demo-credential");
    expect(JSON.parse(sessionStorage.getItem("stp_demo_credential")!)).toEqual(CRED);
  });

  it("reuses the sessionStorage credential instead of refetching", async () => {
    sessionStorage.setItem("stp_demo_credential", JSON.stringify(CRED));
    stubDemoCredential({ ok: true, json: async () => ({ email: "other@demo.nomura", password: "x" }) });
    renderLogin();

    expect(await screen.findByDisplayValue(CRED.email)).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("leaves the form empty when the demo endpoint is unavailable", async () => {
    stubDemoCredential({ ok: false });
    renderLogin();
    await screen.findByText("NomuTrade");
    expect(screen.getByPlaceholderText("trader@demo.nomura")).toHaveValue("");
  });

  it("submits credentials and lands on the persona home", async () => {
    stubDemoCredential({ ok: false });
    const { login } = renderLogin();
    login.mockResolvedValue("TRADER");

    fireEvent.change(screen.getByPlaceholderText("trader@demo.nomura"), {
      target: { value: "  trader@demo.nomura  " },
    });
    const pw = document.querySelector('input[type="password"]')!;
    fireEvent.change(pw, { target: { value: "demo1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByText("HomeMarker");
    expect(login).toHaveBeenCalledWith("trader@demo.nomura", "demo1234"); // trimmed
  });

  it("shows the server error envelope (with trace id) on a 401", async () => {
    stubDemoCredential({ ok: false });
    const { login } = renderLogin();
    login.mockRejectedValue(new ApiError(401, { code: "UNAUTHORIZED", message: "Invalid credentials", traceId: "tr-9" }));

    fireEvent.change(screen.getByPlaceholderText("trader@demo.nomura"), { target: { value: "a@b.c" } });
    fireEvent.change(document.querySelector('input[type="password"]')!, { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByRole("alert");
    expect(screen.getByText("Invalid credentials")).toBeInTheDocument();
    expect(screen.getByText("trace tr-9")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled(); // not stuck pending
  });

  it("a lockout starts the retry countdown and re-enables the form at zero", async () => {
    vi.useFakeTimers();
    stubDemoCredential({ ok: false });
    const { login } = renderLogin();
    login.mockRejectedValue(
      new ApiError(401, {
        code: "LOCKED",
        message: "Too many failed attempts",
        details: [{ retry_after_seconds: 2 }],
      }),
    );

    fireEvent.change(screen.getByPlaceholderText("trader@demo.nomura"), { target: { value: "a@b.c" } });
    fireEvent.change(document.querySelector('input[type="password"]')!, { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await act(async () => {}); // flush the rejected login + state updates

    expect(screen.getByText("Too many attempts — retry in 2 s")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sign in/ })).toBeDisabled();

    await act(async () => {
      vi.advanceTimersByTime(1_000);
    });
    expect(screen.getByText("Too many attempts — retry in 1 s")).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(1_000);
    });
    expect(screen.queryByText(/retry in/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("show/hide toggles the password field type", async () => {
    stubDemoCredential({ ok: false });
    renderLogin();
    await screen.findByText("NomuTrade");

    const pw = document.querySelector('input[type="password"]')!;
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(document.querySelector('input[autocomplete="current-password"]')).toHaveAttribute("type", "text");
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(document.querySelector('input[autocomplete="current-password"]')).toHaveAttribute("type", "password");
    expect(pw).toBeInTheDocument();
  });
});
