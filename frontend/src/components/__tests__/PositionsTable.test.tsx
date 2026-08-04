import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { useAuth } from "../../auth";
import { I18nProvider } from "../../i18n";
import type { PositionsResponse } from "../../api/types";
import { PositionsTable } from "../PositionsTable";
import type { TicketPrefill } from "../OrderTicket";
import { makePortfolio, makePosition } from "../../test/utils";

vi.mock("../../auth", () => ({ useAuth: vi.fn() }));

// The ticket is stubbed: PositionsTable only owns the prefill hand-off.
const ticketProps: { prefill?: TicketPrefill; portfolioIds?: string[] } = {};
vi.mock("../OrderTicket", () => ({
  OrderTicket: (props: { prefill: TicketPrefill; portfolios: { portfolio_id: string }[] }) => {
    ticketProps.prefill = props.prefill;
    ticketProps.portfolioIds = props.portfolios.map((p) => p.portfolio_id);
    return <div>OrderTicketStub</div>;
  },
}));

const POSITIONS: PositionsResponse = {
  as_of: "2026-08-01T10:00:00Z",
  items: [
    makePosition(), // AAPL: 50 @ 180, latest 190.5, upnl +525, day +125 (+1.33%)
    makePosition({
      instrument_symbol: "TSLA",
      name: "Tesla",
      quantity: 10,
      avg_cost: 250,
      latest_price: 230,
      market_value: 2_300,
      unrealized_pnl: -200,
      stale_price: true,
      day_change: -50,
      day_change_pct: -1.9,
    }),
    makePosition({
      instrument_symbol: "MSFT",
      name: "Microsoft",
      quantity: 5,
      avg_cost: 400,
      latest_price: 400,
      market_value: 2_000,
      unrealized_pnl: 0,
      day_change: null,
      day_change_pct: null,
    }),
  ],
  totals: { market_value: 13_825, unrealized_pnl: 325 },
};

function renderTable({
  positions = POSITIONS,
  canSubmit = true,
}: {
  positions?: PositionsResponse;
  canSubmit?: boolean } = {}) {
  vi.mocked(useAuth).mockReturnValue({
    hasPerm: () => canSubmit,
  } as never);
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route
            path="/"
            element={
              <PositionsTable portfolioId="pf-1" portfolios={[makePortfolio()]} positions={positions} />
            }
          />
          <Route path="/portfolios/:id" element={<div>PortfolioDetailMarker</div>} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("PositionsTable", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReset();
    delete ticketProps.prefill;
  });

  it("renders rows with day-change and uP&L chips plus the totals footer", () => {
    renderTable();
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("+$125.00 (+1.3%)")).toBeInTheDocument(); // day change chip
    expect(screen.getByText("-$50.00 (-1.9%)")).toBeInTheDocument();
    expect(screen.getByText("+$525.00")).toHaveClass("pnl-chip", "pos"); // uP&L chip
    expect(screen.getByText("STALE")).toBeInTheDocument(); // stale mark badge

    // Totals footer: day-change sum, market value, uP&L and the cost-weighted %.
    expect(screen.getByText("Totals")).toBeInTheDocument();
    expect(screen.getByText("+$75.00")).toBeInTheDocument();
    expect(screen.getByText("$13,825.00")).toBeInTheDocument();
    expect(screen.getByText("+$325.00")).toBeInTheDocument();
    expect(screen.getByText("+2.4%")).toBeInTheDocument(); // 325 / 13,500 cost
  });

  it("renders the empty state when there are no positions", () => {
    renderTable({ positions: { as_of: "", items: [], totals: { market_value: 0, unrealized_pnl: 0 } } });
    expect(screen.getByText("No open positions")).toBeInTheDocument();
    expect(screen.queryByText("Totals")).not.toBeInTheDocument();
  });

  it("row click navigates to the portfolio detail", () => {
    renderTable();
    fireEvent.click(screen.getByText("AAPL"));
    expect(screen.getByText("PortfolioDetailMarker")).toBeInTheDocument();
  });

  it("Close is gated on ORDER_SUBMIT and opens a prefilled SELL ticket without navigating", () => {
    renderTable({ canSubmit: true });
    const close = screen.getAllByRole("button", { name: "Close" })[0]!;
    fireEvent.click(close);

    expect(screen.getByText("OrderTicketStub")).toBeInTheDocument();
    expect(ticketProps.prefill).toEqual({
      instrument: "AAPL",
      side: "SELL",
      quantity: 50,
      portfolioId: "pf-1",
    });
    expect(screen.queryByText("PortfolioDetailMarker")).not.toBeInTheDocument();
  });

  it("hides the Close action without ORDER_SUBMIT", () => {
    renderTable({ canSubmit: false });
    expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
  });

  it("flashes the mark cell green/red when marks move between refreshes", () => {
    const tree = (positions: PositionsResponse) => (
      <I18nProvider>
        <MemoryRouter initialEntries={["/"]}>
          <Routes>
            <Route
              path="/"
              element={
                <PositionsTable portfolioId="pf-1" portfolios={[makePortfolio()]} positions={positions} />
              }
            />
          </Routes>
        </MemoryRouter>
      </I18nProvider>
    );
    vi.mocked(useAuth).mockReturnValue({ hasPerm: () => true } as never);
    const { rerender } = render(tree(POSITIONS));

    const moved: PositionsResponse = {
      ...POSITIONS,
      items: POSITIONS.items.map((p) =>
        p.instrument_symbol === "AAPL"
          ? { ...p, latest_price: 191.5, market_value: 9_575 }
          : p.instrument_symbol === "TSLA"
            ? { ...p, latest_price: 228, market_value: 2_280 }
            : p,
      ),
    };
    rerender(tree(moved));

    expect(document.querySelector(".mark-cell.flash-up")).toBeInTheDocument();
    expect(document.querySelector(".mark-cell.flash-down")).toBeInTheDocument();
  });
});
