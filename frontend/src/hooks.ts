import { useEffect, useRef } from "react";

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
