# 22 — Real-time WebSocket Push

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> New document (post-split addition, same template). Implements the authenticated WebSocket channel that [03 — Portfolio Management](03-portfolio-management.md) and [14 — Notifications](14-notifications.md) reference (former DESIGN.md §9); no existing decision, ID or requirement text was changed.

## Purpose

Push market ticks, user notifications and execution hints to the browser over a single authenticated WebSocket, so dashboards refresh within 5 s of a tick (NFR-PER-004) without depending on the UI's polling cadence. Push is a **hint + market-data channel: REST stays the source of truth** — the UI reacts to `execution`/`notification` messages by refetching the affected REST resource; only ticks are applied in place. Everything keeps working on polling alone when the socket is down.

## SRS requirements covered

- **NFR-PER-004** — dashboard refresh within 5 s of a tick (push channel; previously mapped to the valuation projector's WebSocket in [03](03-portfolio-management.md)).
- Former DESIGN.md §9 — the authenticated WebSocket `/ws` referenced by 03 and 14; this document pins the concrete route and protocol (parity mapping under Components).

## Components

- **Route** — `WS /api/v1/ws` on the `push` module's `APIRouter`. Module routers mount under `/api/v1` per the auto-discovery contract (`app/main.py`); the bare `/ws` reserved in `nginx.conf` and former §9 **maps onto `/api/v1/ws`** (parity mapping: deployments proxy `/ws` → `/api/v1/ws`).
- **Auth** — `?token=` query parameter (browsers cannot set headers on a WebSocket handshake), validated against the server-side session store (`app.state.session_store` — the same store `get_current_user` uses). Missing/invalid/expired token → close code **4401** before accept. Any authenticated role may connect (no permission gate; per-user filtering happens per message). The session is re-validated on reconnect, not per message.
- **ConnectionManager** — module-level singleton: the set of live connections tagged with their session `user_id`; `add`/`remove` on connect/disconnect. `broadcast`/`send_to_user` snapshot the registry before awaiting sends, so a disconnect landing mid-broadcast cannot corrupt an iteration; a failed send drops the connection.
- **Fan-out workers** — one worker per source stream (not per connection), each failure-isolated per event (a bad event is logged, never fatal; project worker idioms, DB units shielded):
  - `market.ticks` → `broadcast {"type":"tick","data":<tick>}` — tick payload per [01](01-market-data.md): `instrument_id, symbol, ts, price, open, high, low, close, volume` (day O/H/L and day-cumulative volume).
  - `notify` → `send_to_user(data.user_id, {"type":"notification","data":<event>})`.
  - `trading.executions` → resolve the owner via a `Portfolio.owner_id` DB lookup (the event carries no `user_id`) → `send_to_user(owner, {"type":"execution","data":<event>})`.
- **Config** — `WS_PUSH_ENABLED: bool = True` (instability kill-switch). When false, `get_workers` returns `[]` and the endpoint closes with **4403**; the UI runs on polling alone.
- **Frontend client** — `frontend/src/api/ws.ts` singleton: connects `ws(s)://<host>/api/v1/ws?token=<stp_token>`, auto-reconnects with capped backoff (~15 s), subscribe/unsubscribe by message type, exposes connection state. Lifecycle is bound to auth: connect when a token is present, close on logout/401.

## Flows

1. UI logs in over REST → opens `WS /api/v1/ws?token=…` → the server validates the session against the session store, accepts, and registers the connection with its `user_id`.
2. The tick replayer publishes `market.ticks` → the tick fan-out broadcasts to every connection → TickerTape / PriceChart / Layout apply ticks in place (hero price, day O/H/L, sparklines, last candle + last-price tag, SIM clock).
3. A domain event lands on `notify` / `trading.executions` → the fan-out delivers a filtered message to the owning user's connection(s) → the UI immediately refetches the affected REST resource (notification list; positions + valuation). Polling remains as a relaxed 30 s structural fallback.

## Data entities used

- Read-only: the session store (`SessionData`) for auth; `Portfolio.owner_id` for execution→user resolution.
- No new tables: the channel carries existing event payloads (outbox streams `market.ticks`, `notify`, `trading.executions` — see [16 — Data Design](16-data-design.md)).

## API endpoints used

- `WS /api/v1/ws?token=<session token>` — the only endpoint. Message envelope `{"type","data"}` with `type ∈ {tick, notification, execution}`. Client→server messages are ignored; the receive loop exists only to detect disconnects.

## Error / edge cases

- **Bad/expired token** — close 4401 before accept (observed through ASGI as a rejected handshake); the client reconnects with backoff and re-validates on every reconnect.
- **Channel disabled** — `WS_PUSH_ENABLED=false`: no workers, endpoint closes with 4403, UI falls back to polling.
- **Send failure / half-open socket** — the connection is dropped from the registry; the endpoint's receive loop notices the disconnect and cleans up (remove is idempotent).
- **Worker crash on a bad event** — logged and isolated; the fan-out never dies, so the supervisor's gather cannot take the other workers down. DB units (execution-owner lookup) run shielded per the project's aiosqlite-cancellation idiom.
- **Replay loop restart / stale tick** — the UI ignores ticks older than the last rendered candle; the 30 s poll realigns structure.
- **Statelessness (NFR-SCL-001)** — the registry is process-local, the same acceptance as notification preferences (14): one uvicorn worker per deployment. Multi-replica deployments need a Redis-backed fan-out (future work, not MVP).

## Acceptance criteria mapping

- **NFR-PER-004** — tick → UI within 5 s: covered by `backend/tests/test_realtime.py` (live-socket broadcast of a published tick) and the in-place tick application in the trading workspace ([20](20-trading-workspace-ui.md)).
- No dedicated AC ID is cited for the push channel itself; it is exercised inside the end-to-end trading flows (order → execution hint → positions refetch) per [19 — Testing Strategy](19-testing-strategy.md), Integration row.
