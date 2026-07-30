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
