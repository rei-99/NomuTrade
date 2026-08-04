import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import { OrderTicket } from "../OrderTicket";
import { makeInstrument, makePortfolio, renderUI } from "../../test/utils";

vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

const AAPL = makeInstrument();
const UST10Y = makeInstrument({
  instrument_id: "i-ust",
  symbol: "UST10Y",
  name: "US Treasury 10Y",
  asset_class: "BOND",
  lot_size: 1000,
  latest_price: 99.25,
});

function renderTicket(prefill: Parameters<typeof OrderTicket>[0]["prefill"] = {}) {
  const onClose = vi.fn();
  const onSubmitted = vi.fn();
  renderUI(
    <OrderTicket prefill={prefill} portfolios={[makePortfolio()]} onClose={onClose} onSubmitted={onSubmitted} />,
  );
  return { onClose, onSubmitted };
}

function postBodies(): unknown[] {
  return vi
    .mocked(api)
    .mock.calls.filter((c) => c[0] === "/orders")
    .map((c) => c[1]?.body);
}

describe("OrderTicket", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({ items: [AAPL, UST10Y], next_cursor: null } as never);
  });

  it("prefills instrument, side and quantity; shows the estimated cost", async () => {
    renderTicket({ instrument: "AAPL", side: "SELL", quantity: 50, portfolioId: "pf-1" });

    await screen.findByDisplayValue(/AAPL — Apple Inc\./);
    expect(screen.getByRole("button", { name: "SELL" })).toHaveClass("active");
    expect(screen.getByPlaceholderText("0")).toHaveValue(50);
    expect(screen.getByText("$9,525.00")).toBeInTheDocument(); // 50 × 190.5
  });

  it("blocks submit with a violation when the quantity is missing", async () => {
    renderTicket({ instrument: "AAPL", quantity: 50 });
    await screen.findByDisplayValue(/AAPL — Apple Inc\./);

    fireEvent.change(screen.getByPlaceholderText("0"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit BUY" }));

    expect(screen.getByText("Quantity must be a positive number.")).toBeInTheDocument();
    expect(postBodies()).toHaveLength(0);
  });

  it("LIMIT requires a limit price; TRAIL requires exactly one of amount / %", async () => {
    renderTicket({ instrument: "AAPL", quantity: 10 });
    await screen.findByDisplayValue(/AAPL — Apple Inc\./);

    fireEvent.click(screen.getByRole("button", { name: "LIMIT" }));
    const limit = screen.getByLabelText(/Limit price/);
    expect(limit).toHaveValue(190.5); // anchored to the last price
    fireEvent.change(limit, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit BUY" }));
    expect(
      screen.getByText("Limit price is required for LIMIT / STOP-LIMIT orders."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "TRAIL" }));
    const amount = screen.getByLabelText(/Trail amount/);
    fireEvent.change(amount, { target: { value: "" } }); // clear the anchor
    fireEvent.click(screen.getByRole("button", { name: "Submit BUY" }));
    expect(
      screen.getByText("Exactly one of trail amount / trail % is required for TRAIL orders."),
    ).toBeInTheDocument();
    expect(postBodies()).toHaveLength(0);
  });

  it("warns when the quantity is not a multiple of the bond lot size", async () => {
    renderTicket({ instrument: "UST10Y", quantity: 500 });
    await screen.findByDisplayValue(/UST10Y — US Treasury 10Y/);
    expect(screen.getByText("Not a multiple of lot size 1,000")).toBeInTheDocument();
    // Bond estimate uses qty × px / 100: 500 × 99.25 / 100 = $496.25
    expect(screen.getByText("$496.25")).toBeInTheDocument();
  });

  it("submits the order with one idempotency key per ticket and closes on 201", async () => {
    const { onClose, onSubmitted } = renderTicket({ instrument: "AAPL", side: "BUY", quantity: 50 });
    await screen.findByDisplayValue(/AAPL — Apple Inc\./);

    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/orders") {
        return {
          status: 201,
          json: async () => ({ order_id: "o-9", status: "ACCEPTED", submitted_at: "2026-08-01T00:00:00Z" }),
        };
      }
      return { items: [AAPL, UST10Y], next_cursor: null };
    });

    fireEvent.click(screen.getByRole("button", { name: "Submit BUY" }));
    await screen.findByText("Order o-9 ACCEPTED", { selector: ".toast" });

    const post = vi.mocked(api).mock.calls.find((c) => c[0] === "/orders");
    expect(post?.[1]?.body).toEqual({
      portfolio_id: "pf-1",
      instrument: "AAPL",
      side: "BUY",
      order_type: "MARKET",
      quantity: 50,
      time_in_force: "GTC",
    });
    expect(post?.[1]?.headers?.["Idempotency-Key"]).toBeTruthy();
    expect(onSubmitted).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("a 200 replay toasts the duplicate notice and closes without onSubmitted", async () => {
    const { onClose, onSubmitted } = renderTicket({ instrument: "AAPL", quantity: 50 });
    await screen.findByDisplayValue(/AAPL — Apple Inc\./);

    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/orders") return { status: 200 };
      return { items: [AAPL, UST10Y], next_cursor: null };
    });

    fireEvent.click(screen.getByRole("button", { name: "Submit BUY" }));
    await screen.findByText("Duplicate submission ignored — order was already accepted.", {
      selector: ".toast",
    });
    expect(onSubmitted).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("422 details become inline violations and the ticket stays open", async () => {
    const { onClose } = renderTicket({ instrument: "AAPL", quantity: 50 });
    await screen.findByDisplayValue(/AAPL — Apple Inc\./);

    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/orders") {
        throw new ApiError(422, {
          code: "VALIDATION_FAILED",
          message: "Order validation failed",
          details: ["quantity exceeds the position limit"],
        });
      }
      return { items: [AAPL, UST10Y], next_cursor: null };
    });

    fireEvent.click(screen.getByRole("button", { name: "Submit BUY" }));
    await screen.findByText("quantity exceeds the position limit");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Cancel closes the ticket without submitting", async () => {
    const { onClose } = renderTicket({ instrument: "AAPL", quantity: 50 });
    await screen.findByDisplayValue(/AAPL — Apple Inc\./);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(postBodies()).toHaveLength(0);
  });
});
