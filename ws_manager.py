"""
WebSocket connection manager.

Supports named rooms so the kitchen, cashier, and order tracking pages
each only receive updates relevant to them.

Usage:
    # In an endpoint:
    await ws_manager.broadcast_to_room("kitchen", {"event": "new_order", ...})
    await ws_manager.broadcast_to_room(f"track:{order_number}", {"event": "status", ...})
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Dict, List, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # room_name → set of active WebSocket connections
        self._rooms: Dict[str, Set[WebSocket]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, room: str) -> None:
        await websocket.accept()
        self._rooms[room].add(websocket)
        logger.debug("WS connected: room=%s total=%d", room, len(self._rooms[room]))

    def disconnect(self, websocket: WebSocket, room: str) -> None:
        self._rooms[room].discard(websocket)
        logger.debug("WS disconnected: room=%s remaining=%d", room, len(self._rooms[room]))

    async def broadcast_to_room(self, room: str, data: dict) -> None:
        """Send JSON payload to every connection in the given room."""
        if room not in self._rooms:
            return
        dead: List[WebSocket] = []
        message = json.dumps(data)
        for ws in list(self._rooms[room]):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._rooms[room].discard(ws)

    async def broadcast_all(self, data: dict) -> None:
        """Broadcast to every connected client across all rooms."""
        for room in list(self._rooms.keys()):
            await self.broadcast_to_room(room, data)

    def room_size(self, room: str) -> int:
        return len(self._rooms.get(room, set()))


# Singleton — import this everywhere
ws_manager = ConnectionManager()
