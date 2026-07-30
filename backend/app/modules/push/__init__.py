"""Real-time WebSocket push channel (docs/design/22-websocket-push.md).

`WS /api/v1/ws` — authenticated push channel (NFR-PER-004). Auth is the
`?token=` query parameter (browsers cannot set headers on a WebSocket
handshake), validated against the server-side session store; failures close
with 4401 before accept. Any authenticated role may connect; the session is
re-validated on reconnect, not per message.

Push is a hint + market-data channel: REST stays the source of truth. The
envelope is {"type", "data"}: `tick` (market.ticks, broadcast to all),
`notification` (notify stream, filtered to the connection's user),
`execution` (trading.executions, owner resolved via Portfolio.owner_id).
One fan-out worker per stream; the ConnectionManager singleton in
`manager.py` tracks connections. WS_PUSH_ENABLED=false disables the workers
and closes the endpoint with 4403.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.modules.push.manager import manager
from app.modules.push.workers import execution_fanout, notify_fanout, tick_fanout

logger = logging.getLogger(__name__)

router = APIRouter(tags=["push"])

CLOSE_UNAUTHORIZED = 4401
CLOSE_DISABLED = 4403


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    if not settings.WS_PUSH_ENABLED:
        await websocket.close(code=CLOSE_DISABLED)
        return
    token = websocket.query_params.get("token")
    session = (
        await websocket.app.state.session_store.get(token) if token else None
    )
    if session is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    await websocket.accept()
    manager.add(websocket, session.user_id)
    try:
        while True:
            # Client messages are ignored; the receive loop exists only to
            # detect the disconnect and deregister promptly.
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove(websocket)


def get_workers(settings):
    if not settings.WS_PUSH_ENABLED:
        return []
    return [tick_fanout, notify_fanout, execution_fanout]
