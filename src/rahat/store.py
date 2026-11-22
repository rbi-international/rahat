"""
The incident board: shared state every agent reads and writes.

This is the blackboard pattern at system scale. One record per SOS, each agent
fills in its own field (extraction, location, verification, triage...). Agents
never overwrite each other's fields, which is how the read-modify-write bug
from the blackboard lecture is avoided here.

In memory, single replica, on purpose: it keeps the demo to one container.
Scaling out means moving this to Redis or Cosmos DB, and that is noted as a
known limitation, not hidden.
"""

from __future__ import annotations

import threading
import time

from .models import Incident, Status


class Store:
    def __init__(self) -> None:
        self._items: dict[str, Incident] = {}
        self._lock = threading.Lock()

    def add(self, inc: Incident) -> None:
        with self._lock:
            self._items[inc.sos.id] = inc

    def get(self, sos_id: str) -> Incident | None:
        return self._items.get(sos_id)

    def all(self) -> list[Incident]:
        with self._lock:
            return list(self._items.values())

    def by_status(self, *statuses: Status) -> list[Incident]:
        return [i for i in self.all() if i.status in statuses]

    def recent_texts(self, window_s: float = 900) -> list[tuple[str, str, str]]:
        """(sos_id, device_id, text) from the last window, for duplicate detection."""
        cut = time.time() - window_s
        return [(i.sos.id, i.sos.device_id, i.sos.text) for i in self.all() if i.sos.received_at >= cut]

    def stats(self) -> dict:
        items = self.all()
        by_status: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        people = 0
        for i in items:
            by_status[i.status.value] = by_status.get(i.status.value, 0) + 1
            if i.triage:
                by_priority[i.triage.priority.value] = by_priority.get(i.triage.priority.value, 0) + 1
            if i.extraction and i.status == Status.DISPATCHED:
                people += i.extraction.people_count
        done = [i for i in items if i.finished_at]
        lat = sorted(i.finished_at - i.sos.received_at for i in done)
        return {
            "received": len(items),
            "by_status": by_status,
            "by_priority": by_priority,
            "dispatched": by_status.get(Status.DISPATCHED.value, 0),
            "people_reached": people,
            "blocked": by_status.get(Status.BLOCKED.value, 0),  # currently in manual review

            "median_s_to_plan": round(lat[len(lat) // 2], 1) if lat else None,
            "p90_s_to_plan": round(lat[int(len(lat) * 0.9)], 1) if lat else None,
            "tokens": sum(i.tokens_used for i in items),
        }

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


store = Store()
