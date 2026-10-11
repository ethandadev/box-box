"""A tiny in-process pub/sub so the UI can follow what the engineer is doing in real time."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import Any


class Bus:
    def __init__(self, history: int = 200) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.history: deque[dict[str, Any]] = deque(maxlen=history)

    def publish(self, kind: str, **payload: Any) -> None:
        event = {"type": kind, "at": time.time(), **payload}
        if kind in ("radio", "driver", "queued", "warning", "transcript"):
            self.history.append(event)
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)
