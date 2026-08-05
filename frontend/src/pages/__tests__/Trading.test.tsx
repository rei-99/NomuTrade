import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../../api/client";
import { I18nProvider } from "../../i18n";
import { Trading } from "../Trading";
import { makeInstrument, makePortfolio, makePosition } from "../../test/utils";

vi.mock("../../api/client", () => ({ api: vi.fn() }));

vi.mock("../../api/ws", () => ({
  wsClient: { subscribe: vi.fn(() => vi.fn()), onState: vi.fn(), getState: vi.fn(() => "closed") },
}));

// Children are stubbed with prop-capturing markers: this suite covers the
// workspace composition (bootstrap, defaults, wiring), not the children.
const seen: Record<string, unknown> = {};
vi.mock("../../components/TickerTape", () => ({
  TickerTape: (p: { symbol?: string }) => {
    seen.tapeSymbol = p.symbol;
    return <div>TapeStub</div>;
  },
}));
vi.mock("../../components/PriceChart", () => ({
  PriceChart: (p: { symbol?: string; timeframe: string }) => {
    seen.chartSymbol = p.symbol;
    seen.chartTf = p.timeframe;
    return <div>ChartStub</div>;
  },
}));
vi.mock("../../components/OrderPanel", () => ({
  OrderPanel: (p: { symbol?: string; portfolioId: string; cash: number | null }) => {
    seen.panelSymbol = p.symbol;
    seen.panelPortfolio = p.portfolioId;
    seen.panelCash = p.cash;
    return <div>OrderPanelStub</div>;
  },
}));
vi.mock("../../components/BondAnalyticsCard", () => ({
  BondAnalyticsCard: (p: { instrument?: { symbol: string } }) => {
    seen.bondSymbol = p.instrument?.symbol;
    return <div>BondStub</div>;
  },
}));
vi.mock("../../components/RiskPanel", () => ({ RiskPanel: () => <div>RiskStub</div> }));
vi.mock("../../components/NewsPanel", () => ({
  NewsPanel: (p: { symbol?: string }) => {
    seen.newsSymbol = p.symbol;
    return <div>NewsStub</div>;
  },
}));
vi.mock("../../components/PositionsTable", () => ({
  PositionsTable: (p: { portfolioId: string }) => {
    seen.positionsPortfolio = p.portfolioId;
    return <div>PositionsStub</div>;
  },
}));

const INSTRUMENTS = [
  makeInstrument(),
  makeInstrument({ instrument_id: "i-msft", symbol: "MSFT", name: "Microsoft", latest_price: 420 }),
  makeInstrument({ instrument_id: "i-old", symbol: "GE", tradable: false, latest_price: 100 }),
];
const PORTFOLIOS = [
  makePortfolio({ portfolio_id: "pf-client", name: "Client Book", type: "CLIENT" }),
  makePortfolio({ portfolio_id: "pf-house", name: "Alpha Book", type: "HOUSE" }),
];

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/instruments") return { items: INSTRUMENTS, next_cursor: null };
    if (path === "/portfolios") return { items: PORTFOLIOS, next_cursor: null };
    if (path === "/portfolios/pf-house/positions") {
      return { as_of: "2026-08-01T10:00:00Z", items: [makePosition()], totals: { market_value: 9525, unrealized_pnl: 525 } };
    }
    if (path === "/portfolios/pf-house/valuation") {
      return {
        ts: "2026-08-01T10:00:00Z",
        cash: 10_000,
        market_value: 9_525,
        total_value: 19_525,
        realized_pnl: 0,
        unrealized_pnl: 525,
        day_change: -12.5,
        kpis: { allocation: [], top_holdings: [], concentration_pct: 0, volatility_annualized_pct: null, var_95_1d_pct: null, es_95_1d_pct: null, sharpe_ratio: null, max_drawdown_pct: null, bond_wtd_ytm_pct: null, bond_wtd_mod_duration: null },
      };
    }
    if (path === "/instruments/AAPL/prices" || path === "/instruments/MSFT/prices") {
      return { symbol: "X", timeframe: "1D", candles: [{ ts: "2026-08-01T09:30:00", open: 189, high: 191, low: 188.5, close: 190.5, volume: 1000 }] };
    }
    throw new Error(`unexpected api call ${path}`);
  });
}

function renderTrading(route = "/") {
  for (const k of Object.keys(seen)) delete seen[k];
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/" element={<Trading />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("Trading workspace", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    stubApi();
  });

  it("composes tape, chart, order panel, bond card, positions, risk and news", async () => {
    renderTrading();
    await screen.findByText("PositionsStub");
    for (const marker of ["TapeStub", "ChartStub", "OrderPanelStub", "BondStub", "RiskStub", "NewsStub"]) {
      expect(screen.getByText(marker)).toBeInTheDocument();
    }
  });

  it("defaults to the first tradable symbol and the first HOUSE portfolio", async () => {
    renderTrading();
    await screen.findByText("PositionsStub");
    expect(seen.tapeSymbol).toBe("AAPL"); // GE (retired) skipped
    expect(seen.panelSymbol).toBe("AAPL");
    expect(seen.newsSymbol).toBe("AAPL");
    expect(seen.panelPortfolio).toBe("pf-house"); // HOUSE preferred over CLIENT
    expect(seen.panelCash).toBe(10_000);
    expect(seen.bondSymbol).toBe("AAPL");
  });

  it("honors the ?symbol= deep link", async () => {
    renderTrading("/?symbol=MSFT");
    await screen.findByText("PositionsStub");
    expect(seen.tapeSymbol).toBe("MSFT");
  });

  it("account chips show the valuation; timeframe buttons switch the chart", async () => {
    renderTrading();
    await screen.findByText("PositionsStub");
    expect(screen.getByText("$10,000.00")).toBeInTheDocument(); // cash chip
    expect(screen.getByText("-$12.50")).toBeInTheDocument(); // day chip
    expect(seen.chartTf).toBe("3M");

    fireEvent.click(screen.getByRole("button", { name: "1W" }));
    expect(seen.chartTf).toBe("1W");
  });

  it("chart-expand toggle hides positions+risk into chart-max mode and persists", async () => {
    renderTrading();
    await screen.findByText("PositionsStub");

    const page = document.querySelector(".trading-page")!;
    expect(page.classList.contains("chart-expanded")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /expand chart/i }));
    expect(page.classList.contains("chart-expanded")).toBe(true);
    expect(localStorage.getItem("stp_chart_expanded")).toBe("1");

    fireEvent.click(screen.getByRole("button", { name: /restore panels/i }));
    expect(page.classList.contains("chart-expanded")).toBe(false);
    expect(localStorage.getItem("stp_chart_expanded")).toBe("0");
  });
});
