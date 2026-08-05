// Shared test setup — runs before every test file (vitest.config.ts setupFiles).
//
// Mock strategy for this suite (keep new tests consistent with it):
// - Components that call the API: `vi.mock("../api/client")` (path adjusted
//   per test file) exposing a mocked `api<T>` fn; set per-test resolved values
//   with `vi.mocked(api).mockResolvedValue(...)`. See
//   src/pages/__tests__/Connect.test.tsx.
// - src/api/client.ts itself: stub `global.fetch`; we cover token attach from
//   localStorage, error-envelope parsing into ApiError, the global error
//   handler (toast) call, and the 401 → /login bounce. See
//   src/api/__tests__/client.test.ts.
// - src/api/ws.ts: a minimal MockWebSocket class (OPEN/CONNECTING consts,
//   send/close, onmessage capture) injected via
//   `vi.stubGlobal("WebSocket", MockWebSocket)`; covers connect-on-token,
//   subscribe-by-type dispatch, reconnect backoff (fake timers), close on
//   stop. See src/api/__tests__/ws.test.ts.
// - ECharts: never render real charts in jsdom — `vi.mock("echarts")` with an
//   `init()` returning `{ setOption: vi.fn(), on: vi.fn(), resize: vi.fn(),
//   dispose: vi.fn(), getOption: vi.fn() }` for any component that imports it.
// - qrcode: `vi.mock("qrcode")` with `toDataURL` resolving a stub data URL.

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Node ≥23 ships its own localStorage/sessionStorage globals (getters on
// globalThis that, without --localstorage-file, expose objects with NO
// Storage methods) and vitest's jsdom environment does not override them —
// tests then see the broken Node stubs ("localStorage.removeItem is not a
// function"). Install a minimal in-memory Storage polyfill when broken.
// defineProperty, NOT vi.stubGlobal: tests that call vi.unstubAllGlobals()
// must not restore the broken Node getter.
class MemoryStorage {
  private map = new Map<string, string>();
  get length() {
    return this.map.size;
  }
  key(index: number) {
    return [...this.map.keys()][index] ?? null;
  }
  getItem(key: string) {
    return this.map.has(key) ? this.map.get(key)! : null;
  }
  setItem(key: string, value: string) {
    this.map.set(key, String(value));
  }
  removeItem(key: string) {
    this.map.delete(key);
  }
  clear() {
    this.map.clear();
  }
}
for (const name of ["localStorage", "sessionStorage"] as const) {
  if (typeof globalThis[name]?.removeItem !== "function") {
    Object.defineProperty(globalThis, name, {
      value: new MemoryStorage(),
      configurable: true,
      writable: true,
    });
  }
}

// Unmount rendered trees between tests (Vitest has no auto-cleanup without
// globals enabled).
afterEach(() => {
  cleanup();
});

// jsdom lacks these browser APIs; components/hooks touch them via charts,
// media-query hooks, and lazy-rendering helpers. Minimal no-op stubs.
if (typeof window !== "undefined") {
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }

  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  class IntersectionObserverStub {
    readonly root = null;
    readonly rootMargin = "0px";
    readonly thresholds = [0];
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }
  if (!window.ResizeObserver) {
    window.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
  }
  if (!window.IntersectionObserver) {
    window.IntersectionObserver =
      IntersectionObserverStub as unknown as typeof IntersectionObserver;
  }

  // jsdom's canvas has no rendering context (throws "not implemented"); give
  // chart-adjacent code a minimal 2d-context no-op instead.
  HTMLCanvasElement.prototype.getContext = (() => null) as never;
}
