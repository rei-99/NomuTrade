import { fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { PaperAccount } from "../../api/types";
import { Paper } from "../Paper";
import { makePortfolio, renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

const setOptionCalls: unknown[][] = [];
vi.mock("echarts", () => ({
  init: vi.fn(() => ({
    setOption: vi.fn((...args: unknown[]) => setOptionCalls.push(args)),
    on: vi.fn(),
    off: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    getOption: vi.fn(),
  })),
}));

const PAPER_PF = makePortfolio({ portfolio_id: "pf-paper", name: "Paper 1", type: "PAPER" });

const ACCOUNT: PaperAccount = {
  portfolio_id: "pf-paper",
  name: "Paper 1",
  cash_balance: 10_500_000,
  initial_balance: 10_000_000,
  statistics: { trades: 7, win_rate: 57.1, avg_pnl_per_trade: 12_300, max_drawdown: -45_000 },
  equity_curve: [
    { ts: "2026-07-01T00:00:00Z", value: 10_000_000 },
    { ts: "2026-08-01T00:00:00Z", value: 10_500_000 },
  ],
};

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/portfolios") return { items: [PAPER_PF], next_cursor: null };
    if (path === "/paper/accounts/pf-paper") return ACCOUNT;
    if (path.endsWith("/reset")) return undefined;
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("Paper page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    setOptionCalls.length = 0;
    stubApi();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the empty state when no paper account exists", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/portfolios") return { items: [], next_cursor: null };
      throw new Error(`unexpected api call ${path}`);
    });
    renderUI(<Paper />);
    await screen.findByText("No paper account yet — create one above.");
  });

  it("renders the account stat cards and plots the equity curve", async () => {
    renderUI(<Paper />);
    await screen.findByText("$10,500,000.00");
    expect(screen.getByText("+$500,000.00")).toBeInTheDocument(); // P&L vs start
    expect(screen.getByText("57.1%")).toBeInTheDocument(); // win rate
    expect(screen.getByText("7")).toBeInTheDocument(); // trades

    const line = setOptionCalls
      .map((c) => (c[0] as { series?: { type: string; data: number[] }[] }).series?.[0])
      .find((s) => s?.type === "line");
    expect(line?.data).toEqual([10_000_000, 10_500_000]);
  });

  it("creates an account with the entered initial cash and selects it", async () => {
    renderUI(<Paper />);
    await screen.findByText("$10,500,000.00");

    const input = screen.getByLabelText(/Initial cash/);
    fireEvent.change(input, { target: { value: "5000000" } });
    vi.mocked(api).mockImplementation(async (path: string, opts?: { method?: string }) => {
      if (path === "/paper/accounts" && opts?.method === "POST") {
        return { portfolio_id: "pf-paper-2", name: "Paper 2", cash_balance: 5_000_000, initial_balance: 5_000_000 };
      }
      if (path === "/portfolios") return { items: [PAPER_PF], next_cursor: null };
      if (path === "/paper/accounts/pf-paper") return ACCOUNT;
      throw new Error(`unexpected api call ${path}`);
    });
    fireEvent.click(screen.getByRole("button", { name: "Create paper account" }));

    await screen.findByText("Paper account Paper 2 created", { selector: ".toast" });
    const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/paper/accounts");
    expect(post?.[1]?.body).toEqual({ initial_cash: 5_000_000 });
  });

  it("reset requires the confirm dialog and posts the reset", async () => {
    renderUI(<Paper />);
    await screen.findByText("$10,500,000.00");

    vi.stubGlobal("confirm", vi.fn(() => false));
    fireEvent.click(screen.getByRole("button", { name: "Reset account" }));
    expect(vi.mocked(api).mock.calls.some((c) => String(c[0]).includes("/reset"))).toBe(false);

    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(screen.getByRole("button", { name: "Reset account" }));
    await screen.findByText("Paper account reset", { selector: ".toast" });
    expect(api).toHaveBeenCalledWith("/paper/accounts/pf-paper/reset", { method: "POST" });
  });
});
