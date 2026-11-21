"""
In-process event bus for the live screens (Server-Sent Events).

Each subscriber gets its own bounded queue. A slow projector never slows the
pipeline: when its queue is full, the oldest events for that screen are dropped.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque


class Bus:
    def __init__(self, history: int = 300) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._recent: deque[dict] = deque(maxlen=history)

    def publish(self, kind: str, data: dict) -> None:
        msg = {"kind": kind, "ts": time.time(), "data": data}
        self._recent.append(msg)
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        for m in self._recent:
            q.put_nowait(m)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    @staticmethod
    def sse(msg: dict) -> str:
        return f"event: {msg['kind']}\ndata: {json.dumps(msg, default=str, ensure_ascii=False)}\n\n"

    def clear(self) -> None:
        self._recent.clear()


bus = Bus()
