import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { useAuth } from "../../auth";
import { I18nProvider } from "../../i18n";
import { ToastProvider } from "../Toast";
import { Layout } from "../Layout";

// Layout is the app shell: persona-driven nav, topbar (market dot, sim clock,
// +1d skip, symbol search), notification bell, burger drawer. Mock the seam
// modules only (client/auth/ws) — hooks and child components run for real.
vi.mock("../../api/client", () => ({
  api: vi.fn(),
  setApiErrorHandler: vi.fn(),
  getToken: vi.fn(() => "tok"),
  setToken: vi.fn(),
  clearToken: vi.fn(),
}));
vi.mock("../../auth", () => ({ useAuth: vi.fn() }));
vi.mock("../../api/ws", () => ({
  wsClient: {
    start: vi.fn(),
    stop: vi.fn(),
    subscribe: vi.fn(() => vi.fn()),
    onState: vi.fn(() => vi.fn()),
    getState: vi.fn(() => "open"),
  },
}));

const ME = {
  user: {
    user_id: "u-1",
    email: "trader@demo.nomura",
    display_name: "Demo Trader",
  },
};

function renderShell() {
  vi.mocked(useAuth).mockReturnValue({
    me: ME,
    persona: "TRADER",
    hasPerm: () => true,
    logout: vi.fn(),
  } as never);
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <I18nProvider>
        <ToastProvider>
          <Layout />
        </ToastProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

describe("Layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/instruments") {
        return {
          items: [
            {
              instrument_id: "i1",
              symbol: "AAPL",
              name: "Apple Inc.",
              asset_class: "EQUITY",
              currency: "USD",
              lot_size: 1,
              tick_size: 0.01,
              tradable: true,
              latest_price: 190.5,
            },
          ],
          next_cursor: null,
        } as never;
      }
      if (path.startsWith("/instruments/")) {
        return { symbol: "AAPL", timeframe: "1D", candles: [{ ts: "2026-08-25T10:00:00Z" }] } as never;
      }
      return { items: [], next_cursor: null } as never;
    });
  });

  it("renders the persona nav, brand and topbar blocks", async () => {
    renderShell();
    expect(screen.getByText("NomuTrade")).toBeInTheDocument();
    expect(screen.getByText("Trading")).toBeInTheDocument();
    expect(screen.getByText("Connect")).toBeInTheDocument();
    expect(screen.getByText("Demo Trader")).toBeInTheDocument();
    // market dot + sim clock + skip-day + search are present
    expect(screen.getByTitle(/Market data updating|No price updates/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Search symbol/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector(".sim-clock")?.textContent ?? "").toMatch(/\d{2}-\d{2}/),
    );
  });

  it("burger collapses the sidebar on desktop and opens the drawer on mobile", async () => {
    // Desktop (matchMedia stub: matches=false) → collapse toggle.
    const { container } = renderShell();
    const burger = screen.getByRole("button", { name: /menu/i });
    expect(container.querySelector(".shell.nav-collapsed")).toBeNull();
    fireEvent.click(burger);
    expect(container.querySelector(".shell.nav-collapsed")).not.toBeNull();
    expect(localStorage.getItem("stp_nav_collapsed")).toBe("1");
    fireEvent.click(burger);
    expect(container.querySelector(".shell.nav-collapsed")).toBeNull();
    expect(localStorage.getItem("stp_nav_collapsed")).toBe("0");

    // Mobile (matches=true) → overlay drawer + backdrop.
    localStorage.removeItem("stp_nav_collapsed");
    const mm = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
    try {
      const { container: c2 } = renderShell();
      fireEvent.click(screen.getAllByRole("button", { name: /menu/i }).pop()!);
      expect(c2.querySelector(".shell.nav-open")).not.toBeNull();
      fireEvent.click(c2.querySelector(".nav-backdrop")!);
      expect(c2.querySelector(".shell.nav-open")).toBeNull();
    } finally {
      window.matchMedia = mm;
      localStorage.removeItem("stp_nav_collapsed");
    }
  });

  it("symbol search filters and navigates on pick", async () => {
    renderShell();
    const box = screen.getByPlaceholderText(/Search symbol/i);
    fireEvent.change(box, { target: { value: "aap" } });
    const item = await screen.findByText("Apple Inc.");
    fireEvent.mouseDown(item.closest("button")!);
    // URL navigated to the workspace symbol
    await waitFor(() => expect(screen.queryByDisplayValue("aap")).not.toBeInTheDocument());
  });

  it("skip-day posts the replay skip and toasts", async () => {
    renderShell();
    fireEvent.click(screen.getByTitle(/Replay the next market day/i));
    await waitFor(() =>
      expect(vi.mocked(api)).toHaveBeenCalledWith(
        "/marketdata/replay/skip",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("filters nav tabs by persona (RISK persona has no Trading tab)", () => {
    vi.mocked(useAuth).mockReturnValue({
      me: ME,
      persona: "RISK",
      hasPerm: () => true,
      logout: vi.fn(),
    } as never);
    render(
      <MemoryRouter initialEntries={["/"]}>
        <I18nProvider>
          <ToastProvider>
            <Layout />
          </ToastProvider>
        </I18nProvider>
      </MemoryRouter>,
    );
    expect(screen.queryByRole("link", { name: "Trading" })).toBeNull();
    expect(screen.getByText("Connect")).toBeInTheDocument();
  });
});
