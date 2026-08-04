import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { Trade } from "../../api/types";
import { Trades } from "../Trades";
import { makeInstrument, makePortfolio, renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

vi.mock("../../api/ws", () => ({
  wsClient: { subscribe: vi.fn(() => vi.fn()), onState: vi.fn(), getState: vi.fn(() => "closed") },
}));

function trade(overrides: Partial<Trade>): Trade {
  return {
    execution_id: "e-1",
    order_id: "o-1",
    portfolio_id: "pf-1",
    instrument_symbol: "AAPL",
    side: "BUY",
    price: 190.5,
    quantity: 50,
    executed_at: "2026-08-01T10:00:00Z",
    portfolio_type: "HOUSE",
    settlement_state: "SETTLED",
    ...overrides,
  };
}

const PAGE1 = [trade({}), trade({ execution_id: "e-2", instrument_symbol: "UST10Y", price: 99, quantity: 1000, side: "SELL", settlement_state: "EXECUTED" })];
const PAGE2 = [trade({ execution_id: "e-3", instrument_symbol: "MSFT", price: 420, quantity: 5, settlement_state: null })];

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string, opts?: { params?: { cursor?: string } }) => {
    if (path === "/portfolios") {
      return { items: [makePortfolio(), makePortfolio({ portfolio_id: "pf-2", name: "Client Book", type: "CLIENT" })], next_cursor: null };
    }
    if (path === "/instruments") {
      return {
        items: [makeInstrument(), makeInstrument({ instrument_id: "i-ust", symbol: "UST10Y", asset_class: "BOND" })],
        next_cursor: null,
      };
    }
    if (path === "/trades") {
      return opts?.params?.cursor === "cur-2"
        ? { items: PAGE2, next_cursor: null }
        : { items: PAGE1, next_cursor: "cur-2" };
    }
    throw new Error(`unexpected api call ${path}`);
  });
}

function tradeCalls() {
  return vi.mocked(api).mock.calls.filter((c) => c[0] === "/trades");
}

describe("Trades page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    stubApi();
  });

  it("renders the blotter; bond notional uses qty × price / 100", async () => {
    renderUI(<Trades />);
    await screen.findByText("UST10Y");
    expect(screen.getByText("$9,525.00")).toBeInTheDocument(); // 50 × 190.5 equity
    expect(screen.getByText("$990.00")).toBeInTheDocument(); // 1000 × 99 / 100 bond
    expect(screen.getByText("SETTLED")).toBeInTheDocument();
    expect(screen.getAllByText("Alpha Book", { selector: "span" }).length).toBe(2); // label resolved per row
  });

  it("the portfolio filter reloads with the portfolio_id param", async () => {
    renderUI(<Trades />);
    await screen.findByText("UST10Y");

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "pf-2" } });
    await screen.findByText("UST10Y");
    const last = tradeCalls()[tradeCalls().length - 1]!;
    expect(last[1]?.params).toMatchObject({ portfolio_id: "pf-2" });
  });

  it("load more appends the next page via the cursor", async () => {
    renderUI(<Trades />);
    await screen.findByText("UST10Y");

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("MSFT");
    expect(tradeCalls().some((c) => (c[1]?.params as { cursor?: string })?.cursor === "cur-2")).toBe(true);
    expect(screen.getByText("AAPL")).toBeInTheDocument(); // page 1 rows kept
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument(); // cursor exhausted
  });

  it("a null settlement state renders the neutral dash badge", async () => {
    renderUI(<Trades />);
    await screen.findByText("UST10Y");
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("MSFT");
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
