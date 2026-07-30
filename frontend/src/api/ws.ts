import { getToken } from "./client";

/**
 * Real-time push channel client (design 22): a single WebSocket to
 * `/api/v1/ws?token=<stp_token>` carrying `{"type","data"}` envelopes —
 * `tick` (market data, applied in place), `notification` and `execution`
 * (hints: refetch the affected REST resource). REST stays the source of
 * truth; everything degrades to plain polling while the socket is down.
 *
 * Lifecycle is driven from auth.tsx: `start()` once a token exists,
 * `stop()` on logout. On an unexpected close the client reconnects with
 * exponential backoff capped at ~15 s, re-validating the session per
 * reconnect (the server closes 4401 on a bad token).
 */

export type WsState = "idle" | "connecting" | "open" | "closed";

export interface WsEnvelope<T = unknown> {
  type: string;
  data: T;
}

/** market.ticks payload (design 01): day O/H/L and day-cumulative volume. */
export interface TickData {
  instrument_id: string;
  symbol: string;
  ts: string;
  price: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type WsHandler = (msg: WsEnvelope) => void;

const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 15_000;

class WsClient {
  private socket: WebSocket | null = null;
  private handlers = new Map<string, Set<WsHandler>>();
  private stateListeners = new Set<(s: WsState) => void>();
  private reconnectDelay = RECONNECT_MIN_MS;
  private reconnectTimer: number | null = null;
  private running = false;
  private state: WsState = "idle";

  /** Connect (idempotent). Called by the auth lifecycle when a token exists. */
  start(): void {
    if (this.running) return;
    this.running = true;
    this.connect();
  }

  /** Disconnect and stop reconnecting (logout / session gone). */
  stop(): void {
    this.running = false;
    this.clearTimer();
    this.reconnectDelay = RECONNECT_MIN_MS;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.setState("idle");
  }

  /** Subscribe to a message type; returns an unsubscribe function. */
  subscribe(type: string, handler: WsHandler): () => void {
    let set = this.handlers.get(type);
    if (!set) {
      set = new Set();
      this.handlers.set(type, set);
    }
    set.add(handler);
    return () => {
      set.delete(handler);
      if (set.size === 0) this.handlers.delete(type);
    };
  }

  /** Listen for connection-state changes; fires immediately with the current state. */
  onState(cb: (s: WsState) => void): () => void {
    this.stateListeners.add(cb);
    cb(this.state);
    return () => {
      this.stateListeners.delete(cb);
    };
  }

  getState(): WsState {
    return this.state;
  }

  private setState(state: WsState): void {
    if (this.state === state) return;
    this.state = state;
    for (const cb of [...this.stateListeners]) cb(state);
  }

  private clearTimer(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private connect(): void {
    if (!this.running) return;
    const token = getToken();
    if (!token) {
      this.running = false;
      this.setState("idle");
      return;
    }
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/api/v1/ws?token=${encodeURIComponent(token)}`;
    this.setState("connecting");
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.onopen = () => {
      this.reconnectDelay = RECONNECT_MIN_MS;
      this.setState("open");
    };
    socket.onmessage = (ev) => {
      let msg: WsEnvelope;
      try {
        msg = JSON.parse(ev.data as string) as WsEnvelope;
      } catch {
        return; // malformed frame: ignore
      }
      const set = this.handlers.get(msg.type);
      if (!set) return;
      for (const handler of [...set]) {
        try {
          handler(msg);
        } catch {
          // a listener bug must not kill the socket
        }
      }
    };
    socket.onclose = () => {
      if (this.socket === socket) this.socket = null;
      if (!this.running) return;
      this.setState("closed");
      this.scheduleReconnect();
    };
    // onerror is always followed by onclose; reconnect happens there.
  }

  private scheduleReconnect(): void {
    this.clearTimer();
    const delay = this.reconnectDelay;
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, RECONNECT_MAX_MS);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}

export const wsClient = new WsClient();
