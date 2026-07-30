"""Connection registry for the push module (design 22, Components).

Single-process by design (one uvicorn worker): the fan-out workers and the
WS endpoint share this module-level singleton. Senders snapshot the registry
before awaiting sends, so a disconnect landing mid-broadcast cannot corrupt
an iteration; a failed send drops the connection (the endpoint's own remove
on disconnect is idempotent).
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Live WebSocket connections tagged with their session user_id."""

    def __init__(self) -> None:
        self._user_of: dict[WebSocket, str] = {}

    def add(self, ws: WebSocket, user_id: str) -> None:
        self._user_of[ws] = user_id

    def remove(self, ws: WebSocket) -> None:
        self._user_of.pop(ws, None)

    @property
    def count(self) -> int:
        return len(self._user_of)

    async def broadcast(self, message: dict) -> None:
        for ws in list(self._user_of):
            await self._send(ws, message)

    async def send_to_user(self, user_id: str, message: dict) -> None:
        targets = [ws for ws, uid in list(self._user_of.items()) if uid == user_id]
        for ws in targets:
            await self._send(ws, message)

    async def _send(self, ws: WebSocket, message: dict) -> None:
        try:
            await ws.send_json(message)
        except Exception:
            # Dead or closing connection: drop it; the endpoint's finally
            # remove() is idempotent.
            logger.debug("push: dropping connection after send failure")
            self.remove(ws)


manager = ConnectionManager()  # module-level singleton
