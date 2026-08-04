import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../../api/client";
import type { Timeframe } from "../../api/types";
import { I18nProvider } from "../../i18n";
import { PortfolioDetail } from "../PortfolioDetail";
import { makePortfolio, makePosition } from "../../test/utils";

vi.mock("../../api/client", () => ({ api: vi.fn() }));

// ECharts never renders in jsdom; capture setOption at the wrapper level.
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

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string, opts?: { params?: { timeframe?: Timeframe; symbol?: string; cursor?: string } }) => {
    if (path === "/portfolios") return { items: [makePortfolio()], next_cursor: null };
    if (path === "/portfolios/pf-1/valuation") {
      return {
        ts: "2026-08-01T10:00:00Z",
        cash: 1_000,
        market_value: 9_525,
        total_value: 10_525,
        realized_pnl: 0,
        unrealized_pnl: 525,
        day_change: 125,
        kpis: {
          allocation: [{ asset_class: "EQUITY", value: 9_525, pct: 100 }],
          top_holdings: [{ instrument_symbol: "AAPL", market_value: 9_525, pct: 100 }],
          concentration_pct: 100,
          volatility_annualized_pct: 42,
          var_95_1d_pct: null,
          es_95_1d_pct: null,
          sharpe_ratio: null,
          max_drawdown_pct: null,
          bond_wtd_ytm_pct: null,
          bond_wtd_mod_duration: null,
        },
      };
    }
    if (path === "/portfolios/pf-1/positions") {
      return { as_of: "2026-08-01T10:00:00Z", items: [makePosition()], totals: { market_value: 9_525, unrealized_pnl: 525 } };
    }
    if (path === "/portfolios/pf-1/transactions") {
      return {
        items: [
          { ts: "2026-08-01T10:00:01Z", kind: "EXECUTION", instrument_symbol: "AAPL", side: "BUY", quantity: 50, price: 190.5, amount: 9_525, ref_id: "e-1" },
        ],
        next_cursor: opts?.params?.cursor ? null : "cur-2",
      };
    }
    if (path === "/portfolios/pf-1/performance") {
      return { series: [{ ts: "2026-07-01T00:00:00Z", total_value: 10_000 }, { ts: "2026-08-01T00:00:00Z", total_value: 10_525 }] };
    }
    throw new Error(`unexpected api call ${path}`);
  });
}

function renderDetail() {
  setOptionCalls.length = 0;
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={["/portfolios/pf-1"]}>
        <Routes>
          <Route path="/portfolios/:id" element={<PortfolioDetail />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("PortfolioDetail page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    stubApi();
  });

  it("renders the header, stat cards, KPI row and the positions table", async () => {
    renderDetail();
    await screen.findByText("AAPL");
    expect(screen.getByText("Alpha Book")).toBeInTheDocument();
    expect(screen.getByText("$10,525.00")).toBeInTheDocument(); // total value
    expect(screen.getByText("42.0%")).toBeInTheDocument(); // volatility KPI
    expect(screen.getByText(/AAPL 100\.0%/)).toBeInTheDocument(); // top holdings line
    expect(screen.getByText(/Totals — market value \$9,525\.00/)).toBeInTheDocument();
  });

  it("the allocation donut renders by asset class; holdings view adds the cash slice", async () => {
    renderDetail();
    await screen.findByText("AAPL");

    const pieNames = () =>
      setOptionCalls
        .map((c) => (c[0] as { series?: { type: string; data: { name: string }[] }[] }).series?.[0])
        .filter((s) => s?.type === "pie")
        .flatMap((s) => s!.data.map((d) => d.name));
    expect(pieNames()).toContain("EQUITY");

    fireEvent.click(screen.getByRole("button", { name: "Holdings" }));
    expect(pieNames()).toContain("Cash"); // cash>0 joins the holdings donut
  });

  it("transactions tab loads rows and pages via the cursor", async () => {
    renderDetail();
    await screen.findByText("AAPL");

    fireEvent.click(screen.getByRole("button", { name: "Transactions" }));
    await screen.findByText("EXECUTION");
    const txCalls = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/portfolios/pf-1/transactions");
    expect(txCalls().length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("EXECUTION");
    expect(txCalls().some((c) => (c[1]?.params as { cursor?: string })?.cursor === "cur-2")).toBe(true);
  });

  it("transaction filters feed the request params", async () => {
    renderDetail();
    await screen.findByText("AAPL");
    fireEvent.click(screen.getByRole("button", { name: "Transactions" }));
    await screen.findByText("EXECUTION");

    fireEvent.change(screen.getByPlaceholderText("e.g. AAPL"), { target: { value: "tsla" } });
    await screen.findByText("EXECUTION");
    const calls = vi.mocked(api).mock.calls.filter((c) => c[0] === "/portfolios/pf-1/transactions");
    const last = calls[calls.length - 1]!;
    expect((last[1]?.params as { symbol?: string })?.symbol).toBe("TSLA"); // uppercased
  });

  it("performance tab fetches per timeframe and plots the series", async () => {
    renderDetail();
    await screen.findByText("AAPL");

    fireEvent.click(screen.getByRole("button", { name: "Performance" }));
    await screen.findByText("Total value", { selector: "h3" });
    const perfCalls = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/portfolios/pf-1/performance");
    expect(perfCalls()[0]?.[1]?.params).toEqual({ timeframe: "1M" });

    const lineData = () =>
      setOptionCalls
        .map((c) => (c[0] as { series?: { type: string; data: number[] }[] }).series?.[0])
        .filter((s) => s?.type === "line")
        .map((s) => s!.data);
    expect(lineData()).toContainEqual([10_000, 10_525]);

    fireEvent.click(screen.getByRole("button", { name: "1Y" }));
    await screen.findByText("Total value", { selector: "h3" });
    expect(perfCalls()[perfCalls().length - 1]?.[1]?.params).toEqual({ timeframe: "1Y" });
  });
});
