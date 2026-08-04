import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../client";
import { wsClient } from "../ws";
import type { WsEnvelope } from "../ws";

/**
 * Minimal WebSocket stand-in: captures instances, lets tests drive
 * onopen/onmessage/onclose manually. Injected via vi.stubGlobal.
 */
class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
    this.readyState = MockWebSocket.CLOSED;
  }

  // ---- test drivers ----
  open(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  emit(msg: WsEnvelope | string): void {
    this.onmessage?.({ data: typeof msg === "string" ? msg : JSON.stringify(msg) });
  }

  closeFromServer(code = 1006): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code });
  }
}

function lastSocket(): MockWebSocket {
  const s = MockWebSocket.instances.at(-1);
  if (!s) throw new Error("no WebSocket created");
  return s;
}

describe("wsClient", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", MockWebSocket);
    MockWebSocket.instances = [];
    localStorage.clear();
    wsClient.stop(); // reset the module-level singleton between tests
  });

  afterEach(() => {
    wsClient.stop();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("does not connect without a token", () => {
    wsClient.start();
    expect(MockWebSocket.instances).toHaveLength(0);
    expect(wsClient.getState()).toBe("idle");
  });

  it("connects with the token in the URL once start()ed", () => {
    setToken("tok abc");
    wsClient.start();
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(lastSocket().url).toBe(
      `ws://${window.location.host}/api/v1/ws?token=${encodeURIComponent("tok abc")}`,
    );
    expect(wsClient.getState()).toBe("connecting");

    lastSocket().open();
    expect(wsClient.getState()).toBe("open");
  });

  it("start() is idempotent", () => {
    setToken("t");
    wsClient.start();
    wsClient.start();
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("dispatches messages to subscribers by type", () => {
    setToken("t");
    wsClient.start();
    lastSocket().open();

    const tickHandler = vi.fn();
    const noteHandler = vi.fn();
    wsClient.subscribe("tick", tickHandler);
    wsClient.subscribe("notification", noteHandler);

    lastSocket().emit({ type: "tick", data: { price: 1 } });
    expect(tickHandler).toHaveBeenCalledWith({ type: "tick", data: { price: 1 } });
    expect(noteHandler).not.toHaveBeenCalled();
  });

  it("unsubscribe stops delivery", () => {
    setToken("t");
    wsClient.start();
    lastSocket().open();

    const handler = vi.fn();
    const unsub = wsClient.subscribe("tick", handler);
    unsub();
    lastSocket().emit({ type: "tick", data: {} });
    expect(handler).not.toHaveBeenCalled();
  });

  it("ignores malformed frames and survives listener exceptions", () => {
    setToken("t");
    wsClient.start();
    lastSocket().open();

    const good = vi.fn();
    wsClient.subscribe("tick", () => {
      throw new Error("listener bug");
    });
    wsClient.subscribe("tick", good);

    lastSocket().emit("not json{{{"); // ignored
    lastSocket().emit({ type: "tick", data: {} }); // throwing listener isolated
    expect(good).toHaveBeenCalledTimes(1);
    expect(wsClient.getState()).toBe("open");
  });

  it("reconnects with exponential backoff after an unexpected close", () => {
    setToken("t");
    wsClient.start();
    lastSocket().open();

    lastSocket().closeFromServer(1006);
    expect(wsClient.getState()).toBe("closed");
    expect(MockWebSocket.instances).toHaveLength(1);

    // first reconnect after 1 s
    vi.advanceTimersByTime(999);
    expect(MockWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(2);

    // backoff doubles: second reconnect after 2 s
    lastSocket().closeFromServer(1006);
    vi.advanceTimersByTime(1999);
    expect(MockWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it("resets the backoff after a successful open", () => {
    setToken("t");
    wsClient.start();
    lastSocket().open();
    lastSocket().closeFromServer(1006);
    vi.advanceTimersByTime(1000); // → socket #2
    lastSocket().open(); // healthy again
    lastSocket().closeFromServer(1006);
    vi.advanceTimersByTime(1000);
    expect(MockWebSocket.instances).toHaveLength(3); // 1 s again, not 2 s
  });

  it("close code 4401 (bad token) is terminal: drops the token, no reconnect", () => {
    setToken("stale");
    // jsdom's Location.assign cannot be spied — stub the location global.
    const assign = vi.fn();
    vi.stubGlobal("location", {
      origin: "http://localhost:3000",
      protocol: "http:",
      host: "localhost:3000",
      pathname: "/",
      assign,
    });
    wsClient.start();
    lastSocket().open();

    lastSocket().closeFromServer(4401);
    expect(wsClient.getState()).toBe("closed");
    expect(localStorage.getItem("stp_token")).toBeNull();
    expect(assign).toHaveBeenCalledWith("/login");

    vi.advanceTimersByTime(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("stop() closes the socket and prevents reconnects", () => {
    setToken("t");
    wsClient.start();
    const s = lastSocket();
    s.open();

    wsClient.stop();
    expect(s.closed).toBe(true);
    expect(wsClient.getState()).toBe("idle");

    s.closeFromServer(1006); // late close event after stop
    vi.advanceTimersByTime(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("onState fires immediately and on transitions", () => {
    const states: string[] = [];
    const off = wsClient.onState((s) => states.push(s));
    expect(states).toEqual(["idle"]);

    setToken("t");
    wsClient.start();
    lastSocket().open();
    lastSocket().closeFromServer(1006);
    expect(states).toEqual(["idle", "connecting", "open", "closed"]);

    off();
    vi.advanceTimersByTime(1000);
    expect(states).toHaveLength(4); // no more deliveries after unsubscribe
  });
});
