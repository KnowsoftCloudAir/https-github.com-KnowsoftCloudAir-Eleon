"""In-memory WebSocket hub for Eleon live presentations."""
from __future__ import annotations
import asyncio
import json
from typing import Any, Dict, Set
from fastapi import WebSocket


class LiveHub:
    def __init__(self) -> None:
        # token -> set of websockets
        self.rooms: Dict[str, Set[WebSocket]] = {}
        self.lock = asyncio.Lock()
        # latest state per room for fast join
        self.state: Dict[str, Dict[str, Any]] = {}

    async def connect(self, token: str, ws: WebSocket, role: str = "viewer") -> None:
        await ws.accept()
        async with self.lock:
            self.rooms.setdefault(token, set()).add(ws)
            self.state.setdefault(token, {"index": 0, "speaking": None, "role_meta": {}})
        # send snapshot
        snap = dict(self.state.get(token) or {})
        snap["type"] = "snapshot"
        snap["role"] = role
        try:
            await ws.send_text(json.dumps(snap))
        except Exception:
            pass

    async def disconnect(self, token: str, ws: WebSocket) -> None:
        async with self.lock:
            room = self.rooms.get(token)
            if room and ws in room:
                room.discard(ws)
            if room is not None and not room:
                self.rooms.pop(token, None)

    async def broadcast(self, token: str, message: dict, exclude: WebSocket | None = None) -> None:
        # update state for key events
        st = self.state.setdefault(token, {"index": 0})
        t = message.get("type")
        if t == "slide":
            st["index"] = int(message.get("index") or 0)
        elif t == "speak":
            st["speaking"] = message.get("text")
        elif t == "speak_end":
            st["speaking"] = None
        data = json.dumps(message)
        room = list(self.rooms.get(token) or [])
        dead = []
        for ws in room:
            if ws is exclude:
                continue
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        if dead:
            async with self.lock:
                for ws in dead:
                    self.rooms.get(token, set()).discard(ws)


hub = LiveHub()
