import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import type { Instrument, Order } from "../../api/types";
import { I18nProvider } from "../../i18n";
import { ToastProvider } from "../Toast";
import { OrderPanel } from "../OrderPanel";
import { makeInstrument, makePortfolio } from "../../test/utils";

// Keep the real ApiError (instanceof matters in OrderPanel); mock only `api`.
vi.mock("../../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api/client")>();
  return { ...mod, api: vi.fn() };
});

const AAPL = makeInstrument();
const UST10Y = makeInstrument({
  instrument_id: "inst-ust",
  symbol: "UST10Y",
  name: "US Treasury 10Y",
  asset_class: "BOND",
  lot_size: 1000,
  latest_price: 99.25,
});
const MSFT_RETIRED = makeInstrument({
  instrument_id: "inst-msft",
  symbol: "MSFT",
  name: "Microsoft",
  tradable: false,
  latest_price: 420,
});
const PF = makePortfolio();

interface RenderOpts {
  symbol?: string;
  instruments?: Instrument[];
  portfolioId?: string;
  cash?: number | null;
  onOrderPlaced?: () => void;
}

function renderPanel(opts: RenderOpts = {}) {
  const onPortfolioChange = vi.fn();
  render(
    <I18nProvider>
      <ToastProvider>
        <OrderPanel
          symbol={opts.symbol ?? "AAPL"}
          instruments={opts.instruments ?? [AAPL, UST10Y, MSFT_RETIRED]}
          portfolios={[PF]}
          portfolioId={opts.portfolioId ?? "pf-1"}
          onPortfolioChange={onPortfolioChange}
          cash={opts.cash === undefined ? 10_000 : opts.cash}
          onOrderPlaced={opts.onOrderPlaced}
        />
      </ToastProvider>
    </I18nProvider>,
  );
  return { onPortfolioChange };
}

/** Accepted-then-FILLED api stub; returns the POST body for assertions. */
function stubAcceptedFilled() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/orders") {
      return {
        status: 201,
        json: async () => ({ order_id: "o-1", status: "ACCEPTED", submitted_at: "2026-08-01T00:00:00Z" }),
      };
    }
    if (path === "/orders/o-1") {
      return {
        order_id: "o-1",
        portfolio_id: "pf-1",
        instrument_symbol: "AAPL",
        side: "BUY",
        order_type: "MARKET",
        quantity: 50,
        limit_price: null,
        stop_price: null,
        time_in_force: "GTC",
        expire_after: null,
        trail_amount: null,
        trail_pct: null,
        trail_reference: null,
        status: "FILLED",
        reject_reason: null,
        created_at: "2026-08-01T00:00:00Z",
        executions: [
          { execution_id: "e-1", price: 190.5, quantity: 50, executed_at: "2026-08-01T00:00:01Z" },
        ],
      } satisfies Order;
    }
    throw new Error(`unexpected api call ${path}`);
  });
}

function orderPosts() {
  return vi.mocked(api).mock.calls.filter((c) => c[0] === "/orders");
}

function feedbackChip(): HTMLElement | null {
  return document.querySelector(".feedback-chip");
}

describe("OrderPanel", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("renders type pills, TIF selector and the default est. cost vs cash", () => {
    renderPanel();
    for (const label of ["MARKET", "LIMIT", "STOP", "STOP-LIMIT", "TRAIL"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    for (const tif of ["DAY", "GTC", "IOC"]) {
      expect(screen.getByRole("button", { name: tif })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "GTC" })).toHaveClass("active");
    // 50 × $190.50 — est. cost line and both trade buttons carry the price.
    expect(screen.getByText("$9,525.00")).toBeInTheDocument();
    expect(screen.getByText("$10,000.00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^BUY/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^SELL/ })).toBeEnabled();
  });

  it("size chips, custom input and the stepper drive the quantity", () => {
    renderPanel();
    const sizeInput = screen.getByPlaceholderText("qty");
    expect(sizeInput).toHaveValue(50);

    fireEvent.click(screen.getByRole("button", { name: "100" }));
    expect(sizeInput).toHaveValue(100);
    expect(screen.getByRole("button", { name: "100" })).toHaveClass("chip-on");

    fireEvent.click(screen.getByRole("button", { name: "Increase size" }));
    expect(sizeInput).toHaveValue(101);
    fireEvent.click(screen.getByRole("button", { name: "Decrease size" }));
    expect(sizeInput).toHaveValue(100);

    fireEvent.change(sizeInput, { target: { value: "25" } });
    expect(screen.getByText("$4,762.50")).toBeInTheDocument(); // 25 × 190.5

    fireEvent.change(sizeInput, { target: { value: "1" } });
    expect(screen.getByRole("button", { name: "Decrease size" })).toBeDisabled();
  });

  it("TRAIL swaps stop/limit for trail amount / trail % with exactly-one validation", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "TRAIL" }));

    const amount = screen.getByLabelText(/Trail amount/);
    const pct = screen.getByLabelText(/Trail %/);
    expect(screen.queryByLabelText(/Stop price/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Limit price/)).not.toBeInTheDocument();

    // Anchored to the last price (190.5); the % field is mutually exclusive.
    expect(amount).toHaveValue(190.5);
    expect(pct).toBeDisabled();

    // Clearing the amount frees % — but with neither set the order is invalid.
    fireEvent.change(amount, { target: { value: "" } });
    expect(pct).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: /BUY/ }));
    expect(screen.getByText("Exactly one of trail amount / trail % is required for TRAIL orders.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm BUY (Enter)" })).toBeDisabled();

    // Setting exactly the % heals the issue and the amount field locks.
    fireEvent.change(pct, { target: { value: "2.5" } });
    expect(amount).toBeDisabled();
    expect(screen.getByRole("button", { name: "Confirm BUY (Enter)" })).toBeEnabled();
  });

  it("STOP shows the stop price only; STOP-LIMIT shows both, anchored to the last price", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "STOP" }));
    expect(screen.getByLabelText(/Stop price/)).toHaveValue(190.5);
    expect(screen.queryByLabelText(/Limit price/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "STOP-LIMIT" }));
    expect(screen.getByLabelText(/Stop price/)).toHaveValue(190.5);
    expect(screen.getByLabelText(/Limit price/)).toHaveValue(190.5);

    fireEvent.click(screen.getByRole("button", { name: "LIMIT" }));
    expect(screen.getByLabelText(/Limit price/)).toHaveValue(190.5);
    expect(screen.queryByLabelText(/Stop price/)).not.toBeInTheDocument();
  });

  it("bond instruments: lot hint, BOND badge and qty × px / 100 estimate", () => {
    renderPanel({ symbol: "UST10Y" });
    expect(screen.getByText(/lots of 1,000/)).toBeInTheDocument();
    expect(screen.getAllByText("BOND").length).toBeGreaterThan(0);
    expect(screen.getByText(/qty × px \/ 100/)).toBeInTheDocument();
    // 50 × 99.25 / 100 = 49.625 → $49.63
    expect(screen.getByText("$49.63")).toBeInTheDocument();
  });

  it("flags est. cost over cash and blocks a BUY confirm with the cash issue", () => {
    renderPanel({ cash: 100 });
    expect(screen.getByText(/over cash/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /BUY/ }));
    expect(screen.getByText("Insufficient cash — est. cost exceeds cash.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm BUY (Enter)" })).toBeDisabled();
  });

  it("disables the trade buttons without a symbol, portfolio, or for untradable instruments", () => {
    const { unmount } = render(
      <I18nProvider>
        <ToastProvider>
          <OrderPanel
            symbol={undefined}
            instruments={[AAPL]}
            portfolios={[PF]}
            portfolioId="pf-1"
            onPortfolioChange={vi.fn()}
            cash={null}
          />
        </ToastProvider>
      </I18nProvider>,
    );
    expect(screen.getByRole("button", { name: /BUY/ })).toBeDisabled();
    unmount();

    renderPanel({ symbol: "MSFT" }); // retired instrument
    expect(screen.getByRole("button", { name: /BUY/ })).toBeDisabled();
  });

  it("BUY → confirm modal shows the ticket math; Confirm posts with an idempotency key", async () => {
    const onOrderPlaced = vi.fn();
    stubAcceptedFilled();
    renderPanel({ onOrderPlaced });

    fireEvent.click(screen.getByRole("button", { name: /^BUY/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Confirm BUY");
    expect(dialog).toHaveTextContent("MARKET");
    expect(dialog).toHaveTextContent("$9,525.00"); // est. cost
    expect(dialog).toHaveTextContent("$10,000.00"); // cash before
    expect(dialog).toHaveTextContent("$475.00"); // cash after

    fireEvent.click(screen.getByRole("button", { name: "Confirm BUY (Enter)" }));

    await screen.findByText(/filled @ \$190\.50/, { selector: ".feedback-chip" });
    const posts = orderPosts();
    expect(posts).toHaveLength(1);
    const [, opts] = posts[0]!;
    expect(opts?.method).toBe("POST");
    expect(opts?.body).toEqual({
      portfolio_id: "pf-1",
      instrument: "AAPL",
      side: "BUY",
      order_type: "MARKET",
      quantity: 50,
      time_in_force: "GTC",
    });
    expect(opts?.headers?.["Idempotency-Key"]).toBeTruthy();
    expect(opts?.raw).toBe(true);

    // MARKET accept was polled to the fill and the parent was notified.
    expect(vi.mocked(api).mock.calls.some((c) => c[0] === "/orders/o-1")).toBe(true);
    expect(feedbackChip()).toHaveClass("ok");
    expect(onOrderPlaced).toHaveBeenCalled();
  });

  it("Esc cancels the confirm modal; Enter confirms while it is open", async () => {
    stubAcceptedFilled();
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: /BUY/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(orderPosts()).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: /BUY/ }));
    fireEvent.keyDown(window, { key: "Enter" });
    await screen.findByText(/filled @/, { selector: ".feedback-chip" });
    expect(orderPosts()).toHaveLength(1);
  });

  it("Enter does not submit while issues are open", async () => {
    renderPanel();
    fireEvent.change(screen.getByPlaceholderText("qty"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /BUY/ }));
    expect(screen.getByText("Quantity must be a positive whole number.")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Enter" });
    await act(async () => {});
    expect(orderPosts()).toHaveLength(0);
    expect(screen.getByRole("dialog")).toBeInTheDocument(); // still open
  });

  it("SELL a LIMIT order: working feedback with the limit detail; IOC shows in the detail", async () => {
    const onOrderPlaced = vi.fn();
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/orders") {
        return {
          status: 201,
          json: async () => ({ order_id: "o-2", status: "OPEN", submitted_at: "2026-08-01T00:00:00Z" }),
        };
      }
      throw new Error(`unexpected api call ${path}`);
    });
    renderPanel({ onOrderPlaced });

    fireEvent.click(screen.getByRole("button", { name: "LIMIT" }));
    fireEvent.change(screen.getByLabelText(/Limit price/), { target: { value: "185" } });
    fireEvent.click(screen.getByRole("button", { name: "IOC" }));
    fireEvent.click(screen.getByRole("button", { name: /SELL/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm SELL (Enter)" }));

    await screen.findByText("SELL 50 AAPL @ $185.00 · IOC working", { selector: ".feedback-chip" });
    const [, opts] = orderPosts()[0]!;
    expect(opts?.body).toMatchObject({
      side: "SELL",
      order_type: "LIMIT",
      limit_price: 185,
      time_in_force: "IOC",
    });
    expect(onOrderPlaced).toHaveBeenCalled();
    // Resting orders are not fill-polled.
    expect(vi.mocked(api).mock.calls.some((c) => c[0] === "/orders/o-2")).toBe(false);
  });

  it("a 200 replay reports a duplicate without touching the fill watcher", async () => {
    vi.mocked(api).mockResolvedValue({ status: 200 } as never);
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: /BUY/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm BUY (Enter)" }));

    await screen.findByText("Duplicate submission ignored", { selector: ".feedback-chip" });
    expect(feedbackChip()).toHaveClass("info");
  });

  it("422 validation failures surface as the violations list", async () => {
    vi.mocked(api).mockRejectedValue(
      new ApiError(422, {
        code: "VALIDATION_FAILED",
        message: "Order validation failed",
        details: [{ rule: "MAX_NOTIONAL", reason: "exceeds the book limit" }],
      }) as never,
    );
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: /BUY/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm BUY (Enter)" }));

    await screen.findByText("Order rejected by validation:");
    expect(screen.getByText("MAX_NOTIONAL: exceeds the book limit")).toBeInTheDocument();
    expect(screen.getByText("Rejected by validation", { selector: ".feedback-chip" })).toBeInTheDocument();
  });

  it("other API errors land in the feedback chip with the server message", async () => {
    vi.mocked(api).mockRejectedValue(
      new ApiError(500, { code: "INTERNAL", message: "Server exploded", traceId: "tr-1" }) as never,
    );
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: /BUY/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm BUY (Enter)" }));

    await screen.findByText("Server exploded", { selector: ".feedback-chip" });
    expect(feedbackChip()).toHaveClass("err");
  });

  it("a terminal poll verdict (REJECTED) replaces the accepted feedback", async () => {
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path === "/orders") {
        return {
          status: 201,
          json: async () => ({ order_id: "o-3", status: "ACCEPTED", submitted_at: "2026-08-01T00:00:00Z" }),
        };
      }
      if (path === "/orders/o-3") {
        return {
          order_id: "o-3",
          status: "REJECTED",
          reject_reason: "restricted instrument",
          executions: [],
        };
      }
      throw new Error(`unexpected api call ${path}`);
    });
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: /BUY/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm BUY (Enter)" }));

    await screen.findByText("Order REJECTED: restricted instrument", { selector: ".feedback-chip" });
    expect(feedbackChip()).toHaveClass("err");
  });
});
