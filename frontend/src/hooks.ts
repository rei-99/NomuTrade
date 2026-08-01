import { useEffect, useRef, useState } from "react";
import { wsClient } from "./api/ws";
import type { WsEnvelope, WsState } from "./api/ws";

/**
 * Runs `fn` immediately and then every `intervalMs`. Re-runs when `deps`
 * change. Pass intervalMs <= 0 to disable polling (single run on deps change).
 */
export function usePoll(fn: () => void, intervalMs: number, deps: unknown[] = []): void {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    fnRef.current();
    if (intervalMs <= 0) return;
    const t = setInterval(() => fnRef.current(), intervalMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export interface AnchoredPriceFields {
  limit: string;
  stop: string;
  trailAmount: string;
  onLimitChange: (v: string) => void;
  onStopChange: (v: string) => void;
  onTrailAmountChange: (v: string) => void;
}

/**
 * Terminal convention (design 25 §U5): order price fields re-anchor to the
 * selected instrument's last price on symbol change. Per-field dirty
 * tracking — a hand-typed value is preserved while the symbol stays the
 * same; switching the symbol resets the dirty flags and re-prefills limit /
 * stop / trail-amount from the NEW instrument (per type: LIMIT→limit,
 * STOP→stop, STOP_LIMIT→both, TRAIL→trail amount). Fields also anchor once
 * when the last price first becomes available for the current symbol (e.g.
 * instruments finish loading), but a typed value is never clobbered
 * mid-symbol. trail% is a ratio, not a price, so it is not anchored.
 */
export function useAnchoredPriceFields(
  symbol: string | undefined,
  last: number | null,
): AnchoredPriceFields {
  const [limit, setLimit] = useState("");
  const [stop, setStop] = useState("");
  const [trailAmount, setTrailAmount] = useState("");
  const [dirty, setDirty] = useState({ limit: false, stop: false, trail: false });
  const symbolRef = useRef(symbol);
  const lastRef = useRef<number | null>(null);

  useEffect(() => {
    const symbolChanged = symbolRef.current !== symbol;
    symbolRef.current = symbol;
    const lastBecameAvailable = last !== null && lastRef.current === null;
    lastRef.current = last;
    if (symbolChanged) {
      setDirty({ limit: false, stop: false, trail: false });
      setLimit(last !== null ? String(last) : "");
      setStop(last !== null ? String(last) : "");
      setTrailAmount(last !== null ? String(last) : "");
    } else if (lastBecameAvailable) {
      if (!dirty.limit) setLimit(String(last));
      if (!dirty.stop) setStop(String(last));
      if (!dirty.trail) setTrailAmount(String(last));
    }
  }, [symbol, last, dirty]);

  return {
    limit,
    stop,
    trailAmount,
    onLimitChange: (v) => {
      setDirty((d) => ({ ...d, limit: true }));
      setLimit(v);
    },
    onStopChange: (v) => {
      setDirty((d) => ({ ...d, stop: true }));
      setStop(v);
    },
    onTrailAmountChange: (v) => {
      setDirty((d) => ({ ...d, trail: true }));
      setTrailAmount(v);
    },
  };
}

/**
 * Subscribes to a push-channel message type (design 22) for the lifetime of
 * the effect; re-subscribes when `deps` change. The handler always sees the
 * latest closure (same ref pattern as usePoll).
 */
export function useWsMessage(
  type: string,
  handler: (msg: WsEnvelope) => void,
  deps: unknown[] = [],
): void {
  const fnRef = useRef(handler);
  fnRef.current = handler;

  useEffect(() => wsClient.subscribe(type, (msg) => fnRef.current(msg)), deps);
}

/** Live push-channel connection state for indicators / freshness guards. */
export function useWsState(): WsState {
  const [state, setState] = useState<WsState>(() => wsClient.getState());
  useEffect(() => wsClient.onState(setState), []);
  return state;
}
