import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { wsClient } from "../../api/ws";
import type { TickData, WsEnvelope, WsState } from "../../api/ws";
import { I18nProvider } from "../../i18n";
import { TickerTape } from "../TickerTape";
import { makeInstrument } from "../../test/utils";

vi.mock("../../api/ws", () => ({
  wsClient: {
    subscribe: vi.fn(),
    onState: vi.fn(),
    getState: vi.fn(),
  },
}));

const INSTRUMENTS = [
  makeInstrument(), // AAPL equity 190.5
  makeInstrument({ instrument_id: "i-msft", symbol: "MSFT", name: "Microsoft", latest_price: 420.25 }),
  makeInstrument({ instrument_id: "i-ust", symbol: "UST10Y", name: "US Treasury 10Y", asset_class: "BOND", latest_price: 99.25 }),
  makeInstrument({ instrument_id: "i-old", symbol: "GE", name: "Retired", tradable: false, latest_price: 100 }),
];

let tickHandler: ((msg: WsEnvelope) => void) | undefined;

function renderTape(wsState: WsState = "closed") {
  tickHandler = undefined;
  vi.mocked(wsClient.getState).mockReturnValue(wsState);
  vi.mocked(wsClient.onState).mockImplementation((cb) => {
    cb(wsState);
    return vi.fn();
  });
  vi.mocked(wsClient.subscribe).mockImplementation((type, handler) => {
    if (type === "tick") tickHandler = handler;
    return vi.fn();
  });
  const onSymbolChange = vi.fn();
  render(
    <I18nProvider>
      <TickerTape
        instruments={INSTRUMENTS}
        symbol="AAPL"
        onSymbolChange={onSymbolChange}
        dayChangePct={1.5}
        dayOhlc={{ open: 189, high: 191, low: 188.5 }}
      />
    </I18nProvider>,
  );
  return { onSymbolChange };
}

function tick(price: number, over: Partial<TickData> = {}): TickData {
  return {
    instrument_id: "inst-aapl",
    symbol: "AAPL",
    ts: "2026-08-01T10:00:00",
    price,
    open: 189,
    high: Math.max(192, price),
    low: 188,
    close: price,
    volume: 1_000,
    ...over,
  };
}

describe("TickerTape", () => {
  beforeEach(() => {
    vi.mocked(wsClient.subscribe).mockReset();
    vi.mocked(wsClient.onState).mockReset();
    vi.mocked(wsClient.getState).mockReset();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("hero block shows the polled price, day change and O/H/L", () => {
    renderTape();
    expect(screen.getByText("$190.50")).toBeInTheDocument();
    expect(screen.getByText("+1.50% today")).toBeInTheDocument();
    expect(screen.getByText("O 189.00")).toBeInTheDocument();
    expect(screen.getByText("H 191.00")).toBeInTheDocument();
    expect(screen.getByText("L 188.50")).toBeInTheDocument();
  });

  it("equity scope lists only tradable equities; chip click selects the symbol", () => {
    const { onSymbolChange } = renderTape();
    const chips = document.querySelectorAll(".chip-symbol");
    const labels = [...chips].map((c) => c.textContent);
    expect(labels.some((l) => l?.includes("AAPL"))).toBe(true);
    expect(labels.some((l) => l?.includes("MSFT"))).toBe(true);
    expect(labels.some((l) => l?.includes("UST10Y"))).toBe(false); // bond scoped out
    expect(labels.some((l) => l?.includes("GE"))).toBe(false); // retired never offered

    fireEvent.click(screen.getByTitle("Microsoft"));
    expect(onSymbolChange).toHaveBeenCalledWith("MSFT");
  });

  it("the Bonds scope filters the chips and persists for the session", () => {
    renderTape();
    fireEvent.click(screen.getByRole("button", { name: "Bonds" }));

    const chips = [...document.querySelectorAll(".chip-symbol")].map((c) => c.textContent);
    expect(chips.some((l) => l?.includes("UST10Y"))).toBe(true);
    expect(chips.some((l) => l?.includes("MSFT"))).toBe(false);
    expect(sessionStorage.getItem("stp_asset_scope")).toBe("BOND");

    // The active symbol stays selectable in the hero picker even out of scope.
    const picker = screen.getByRole("combobox");
    expect(picker).toHaveDisplayValue(/AAPL/);
  });

  it("the hero picker fires onSymbolChange", () => {
    const { onSymbolChange } = renderTape();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "MSFT" } });
    expect(onSymbolChange).toHaveBeenCalledWith("MSFT");
  });

  it("applies live ticks in place (price, O/H/L, day change) while the socket is open", () => {
    vi.useFakeTimers();
    renderTape("open");

    act(() => {
      tickHandler?.({ type: "tick", data: tick(191) });
    });
    act(() => {
      vi.advanceTimersByTime(500); // the ~2 Hz flush
    });

    expect(screen.getByText("$191.00")).toBeInTheDocument();
    expect(screen.getByText("H 192.00")).toBeInTheDocument();
    expect(screen.getByText("L 188.00")).toBeInTheDocument();
    // (191 − 189) / 189 = +1.06% — computed from the tick, not the prop.
    expect(screen.getByText("+1.06% today")).toBeInTheDocument();
  });

  it("ignores the overlay while the socket is down — polled props win", () => {
    vi.useFakeTimers();
    renderTape("closed");

    act(() => {
      tickHandler?.({ type: "tick", data: tick(191) });
    });
    act(() => {
      vi.advanceTimersByTime(1_000);
    });

    expect(screen.getByText("$190.50")).toBeInTheDocument();
    expect(screen.getByText("+1.50% today")).toBeInTheDocument();
  });
});
