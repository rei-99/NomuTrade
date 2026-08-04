import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAnchoredPriceFields, usePoll, useWsMessage, useWsState } from "../hooks";
import { wsClient } from "../api/ws";
import type { WsEnvelope } from "../api/ws";

vi.mock("../api/ws", () => ({
  wsClient: {
    subscribe: vi.fn(),
    onState: vi.fn(),
    getState: vi.fn(),
  },
}));

describe("usePoll", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("runs immediately on mount and then on the interval", () => {
    const fn = vi.fn();
    renderHook(() => usePoll(fn, 1000));
    expect(fn).toHaveBeenCalledTimes(1);

    act(() => vi.advanceTimersByTime(1000));
    expect(fn).toHaveBeenCalledTimes(2);
    act(() => vi.advanceTimersByTime(3000));
    expect(fn).toHaveBeenCalledTimes(5);
  });

  it("intervalMs <= 0 disables polling (single run)", () => {
    const fn = vi.fn();
    renderHook(() => usePoll(fn, 0));
    expect(fn).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(10_000));
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("re-runs (immediately) when deps change", () => {
    const fn = vi.fn();
    const { rerender } = renderHook(({ dep }) => usePoll(fn, 1000, [dep]), {
      initialProps: { dep: 1 },
    });
    expect(fn).toHaveBeenCalledTimes(1);

    rerender({ dep: 2 });
    expect(fn).toHaveBeenCalledTimes(2); // immediate run on deps change

    // …and the old interval was torn down (no double-firing)
    act(() => vi.advanceTimersByTime(1000));
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("stops firing after unmount (cleanup)", () => {
    const fn = vi.fn();
    const { unmount } = renderHook(() => usePoll(fn, 1000));
    expect(fn).toHaveBeenCalledTimes(1);

    unmount();
    act(() => vi.advanceTimersByTime(5000));
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("always calls the latest closure", () => {
    let value = "first";
    const fn = vi.fn(() => value);
    const { rerender } = renderHook(() => usePoll(fn, 1000));
    value = "second";
    rerender();
    act(() => vi.advanceTimersByTime(1000));
    expect(fn).toHaveLastReturnedWith("second");
  });
});

describe("useAnchoredPriceFields", () => {
  it("starts empty while no price is available", () => {
    const { result } = renderHook(({ symbol, last }) => useAnchoredPriceFields(symbol, last), {
      initialProps: { symbol: "AAPL" as string | undefined, last: null as number | null },
    });
    expect(result.current.limit).toBe("");
    expect(result.current.stop).toBe("");
    expect(result.current.trailAmount).toBe("");
  });

  it("anchors all price fields once the last price first arrives", () => {
    const { result, rerender } = renderHook(
      ({ symbol, last }) => useAnchoredPriceFields(symbol, last),
      { initialProps: { symbol: "AAPL" as string | undefined, last: null as number | null } },
    );
    rerender({ symbol: "AAPL", last: 190.5 });
    expect(result.current.limit).toBe("190.5");
    expect(result.current.stop).toBe("190.5");
    expect(result.current.trailAmount).toBe("190.5");
  });

  it("does not re-anchor on subsequent ticks for the same symbol", () => {
    const { result, rerender } = renderHook(
      ({ symbol, last }) => useAnchoredPriceFields(symbol, last),
      { initialProps: { symbol: "AAPL" as string | undefined, last: null as number | null } },
    );
    rerender({ symbol: "AAPL", last: 190.5 });
    rerender({ symbol: "AAPL", last: 191 });
    expect(result.current.limit).toBe("190.5"); // anchored once, then stable
  });

  it("a typed value is never clobbered mid-symbol", () => {
    const { result, rerender } = renderHook(
      ({ symbol, last }) => useAnchoredPriceFields(symbol, last),
      { initialProps: { symbol: "AAPL" as string | undefined, last: null as number | null } },
    );
    rerender({ symbol: "AAPL", last: 190.5 });

    act(() => result.current.onLimitChange("185"));
    expect(result.current.limit).toBe("185");

    // Price arriving "for the first time" again must not touch the dirty field,
    // but still fills the clean ones.
    rerender({ symbol: "AAPL", last: 192 });
    expect(result.current.limit).toBe("185");
  });

  it("typed value survives even if the price becomes available after typing", () => {
    const { result, rerender } = renderHook(
      ({ symbol, last }) => useAnchoredPriceFields(symbol, last),
      { initialProps: { symbol: "AAPL" as string | undefined, last: null as number | null } },
    );
    act(() => result.current.onStopChange("180"));
    rerender({ symbol: "AAPL", last: 190.5 });
    expect(result.current.stop).toBe("180"); // dirty → kept
    expect(result.current.limit).toBe("190.5"); // clean → anchored
  });

  it("switching the symbol resets dirty flags and re-prefills from the new price", () => {
    const { result, rerender } = renderHook(
      ({ symbol, last }) => useAnchoredPriceFields(symbol, last),
      { initialProps: { symbol: "AAPL" as string | undefined, last: 190.5 as number | null } },
    );
    act(() => result.current.onLimitChange("185"));
    expect(result.current.limit).toBe("185");

    rerender({ symbol: "MSFT", last: 420.25 });
    expect(result.current.limit).toBe("420.25");
    expect(result.current.stop).toBe("420.25");
    expect(result.current.trailAmount).toBe("420.25");

    // …and the fields are clean again: a late price for the new symbol anchors.
    act(() => result.current.onStopChange("410"));
    rerender({ symbol: "MSFT", last: 421 });
    expect(result.current.stop).toBe("410");
    expect(result.current.limit).toBe("420.25");
  });

  it("switching to a symbol with no price yet clears the fields", () => {
    const { result, rerender } = renderHook(
      ({ symbol, last }) => useAnchoredPriceFields(symbol, last),
      { initialProps: { symbol: "AAPL" as string | undefined, last: 190.5 as number | null } },
    );
    rerender({ symbol: "MSFT", last: null });
    expect(result.current.limit).toBe("");
    expect(result.current.stop).toBe("");
    expect(result.current.trailAmount).toBe("");
  });

  it("anchors trail amount but exposes no trail% field (a ratio is not a price)", () => {
    const { result, rerender } = renderHook(
      ({ symbol, last }) => useAnchoredPriceFields(symbol, last),
      { initialProps: { symbol: "AAPL" as string | undefined, last: null as number | null } },
    );
    rerender({ symbol: "AAPL", last: 190.5 });
    expect(result.current.trailAmount).toBe("190.5");
    expect("trailPct" in result.current).toBe(false);
  });
});

describe("useWsMessage", () => {
  beforeEach(() => {
    vi.mocked(wsClient.subscribe).mockReset();
  });

  it("subscribes to the type for the lifetime of the effect", () => {
    const unsub = vi.fn();
    vi.mocked(wsClient.subscribe).mockReturnValue(unsub);
    const handler = vi.fn();

    const { unmount } = renderHook(() => useWsMessage("tick", handler));

    expect(wsClient.subscribe).toHaveBeenCalledWith("tick", expect.any(Function));
    expect(unsub).not.toHaveBeenCalled();
    unmount();
    expect(unsub).toHaveBeenCalledTimes(1);
  });

  it("re-subscribes when deps change", () => {
    vi.mocked(wsClient.subscribe).mockReturnValue(vi.fn());
    const { rerender } = renderHook(({ t }) => useWsMessage(t, vi.fn(), [t]), {
      initialProps: { t: "tick" },
    });
    expect(vi.mocked(wsClient.subscribe).mock.calls[0]?.[0]).toBe("tick");
    rerender({ t: "notification" });
    expect(vi.mocked(wsClient.subscribe).mock.calls[1]?.[0]).toBe("notification");
  });

  it("the handler sees the latest closure", () => {
    let captured: ((msg: WsEnvelope) => void) | undefined;
    vi.mocked(wsClient.subscribe).mockImplementation((_type, h) => {
      captured = h;
      return vi.fn();
    });
    let tag = "first";
    const handler = vi.fn(() => tag);
    const { rerender } = renderHook(() => useWsMessage("tick", () => handler()));
    tag = "second";
    rerender();
    act(() => captured?.({ type: "tick", data: {} }));
    expect(handler).toHaveLastReturnedWith("second");
  });
});

describe("useWsState", () => {
  it("returns the current state and tracks listener updates", () => {
    let listener: ((s: "idle" | "connecting" | "open" | "closed") => void) | undefined;
    vi.mocked(wsClient.getState).mockReturnValue("idle");
    vi.mocked(wsClient.onState).mockImplementation((cb) => {
      listener = cb;
      cb("idle");
      return vi.fn();
    });

    const { result } = renderHook(() => useWsState());
    expect(result.current).toBe("idle");

    act(() => listener?.("open"));
    expect(result.current).toBe("open");
  });
});
