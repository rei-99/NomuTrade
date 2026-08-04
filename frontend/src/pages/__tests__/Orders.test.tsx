import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { Order } from "../../api/types";
import { Orders } from "../Orders";
import { makePortfolio, renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

// The ticket modal is stubbed: Orders only owns opening it.
vi.mock("../../components/OrderTicket", () => ({
  OrderTicket: () => <div>OrderTicketStub</div>,
}));

function order(overrides: Partial<Order>): Order {
  return {
    order_id: "o-1",
    portfolio_id: "pf-1",
    instrument_symbol: "AAPL",
    side: "BUY",
    order_type: "LIMIT",
    quantity: 50,
    limit_price: 185,
    stop_price: null,
    time_in_force: "GTC",
    expire_after: null,
    trail_amount: null,
    trail_pct: null,
    trail_reference: null,
    status: "OPEN",
    reject_reason: null,
    created_at: "2026-08-01T10:00:00Z",
    executions: [],
    ...overrides,
  };
}

const ORDERS = [
  order({}),
  order({ order_id: "o-2", order_type: "TRAILING_STOP", trail_amount: 2.5, trail_reference: 190.5, status: "ACCEPTED", limit_price: null }),
  order({ order_id: "o-3", order_type: "MARKET", status: "FILLED", limit_price: null, executions: [{ execution_id: "e-1", price: 190.5, quantity: 50, executed_at: "2026-08-01T10:00:01Z" }] }),
];

function stubApi() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/portfolios") return { items: [makePortfolio()], next_cursor: null };
    if (path === "/orders") return { items: ORDERS, next_cursor: null };
    if (path.startsWith("/orders/")) return undefined; // cancel / amend
    throw new Error(`unexpected api call ${path}`);
  });
}

describe("Orders page", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    stubApi();
  });

  it("renders the blotter incl. trail details and filled quantities", async () => {
    renderUI(<Orders />);
    await screen.findAllByText("AAPL");
    expect(screen.getByText("TRAILING-STOP")).toBeInTheDocument();
    expect(screen.getByText("trail $2.50")).toBeInTheDocument();
    expect(screen.getByTitle("reference $190.50")).toBeInTheDocument();
    expect(screen.getByText("$185.00")).toBeInTheDocument(); // limit column
    // o-3 filled 50/50; the two resting orders show 0 filled
    const filledCells = screen.getAllByText("0", { selector: "td" });
    expect(filledCells.length).toBe(2);
  });

  it("the status filter reloads with the status param", async () => {
    renderUI(<Orders />);
    await screen.findAllByText("AAPL");

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "OPEN" } });
    await waitFor(() => {
      const calls = vi.mocked(api).mock.calls.filter((c) => c[0] === "/orders");
      expect(calls[calls.length - 1]?.[1]?.params).toEqual({ status: "OPEN" });
    });
  });

  it("cancel posts to the cancel endpoint and reloads; only cancellable rows offer it", async () => {
    renderUI(<Orders />);
    await screen.findAllByText("AAPL");
    // OPEN + ACCEPTED are cancellable, FILLED is not
    expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(2);

    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]!);
    await screen.findByText("Order o-1 cancelled", { selector: ".toast" });
    expect(api).toHaveBeenCalledWith("/orders/o-1/cancel", { method: "POST" });
  });

  it("amend opens prefilled and PATCHes quantity + limit for LIMIT orders", async () => {
    renderUI(<Orders />);
    await screen.findAllByText("AAPL");
    // only OPEN / PARTIALLY_FILLED are amendable
    expect(screen.getAllByRole("button", { name: "Amend" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Amend" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Amend order o-1");
    const qtyInput = dialog.querySelector('input[type="number"]')!;
    expect(qtyInput).toHaveValue(50);
    expect(screen.getByLabelText(/Limit price/)).toHaveValue(185);

    fireEvent.change(qtyInput, { target: { value: "75" } });
    fireEvent.change(screen.getByLabelText(/Limit price/), { target: { value: "180" } });
    fireEvent.click(screen.getByRole("button", { name: "Save amendment" }));

    await screen.findByText("Order o-1 amended", { selector: ".toast" });
    const patch = vi.mocked(api).mock.calls.find((c) => c[1]?.method === "PATCH");
    expect(patch?.[0]).toBe("/orders/o-1");
    expect(patch?.[1]?.body).toEqual({ quantity: 75, limit_price: 180 });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("rejects an invalid amendment quantity without PATCHing", async () => {
    renderUI(<Orders />);
    await screen.findAllByText("AAPL");
    fireEvent.click(screen.getByRole("button", { name: "Amend" }));

    const dialog = screen.getByRole("dialog");
    fireEvent.change(dialog.querySelector('input[type="number"]')!, { target: { value: "-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save amendment" }));

    await screen.findByText("Quantity must be a positive number", { selector: ".toast" });
    expect(vi.mocked(api).mock.calls.some((c) => c[1]?.method === "PATCH")).toBe(false);
  });

  it("New ticket opens the order ticket modal", async () => {
    renderUI(<Orders />);
    await screen.findAllByText("AAPL");
    fireEvent.click(screen.getByRole("button", { name: "New ticket" }));
    expect(screen.getByText("OrderTicketStub")).toBeInTheDocument();
  });
});
