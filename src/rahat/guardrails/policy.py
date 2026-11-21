"""
Governance policy: fairness, bounded autonomy, output rules, and the audit log.

  fairness          Influence, rank or money never raises priority.
  bounded autonomy  The Triage agent may move the SOP score by one level, no more.
  output rules      Replies carry no PII, no casualty figures, no promises.
  audit             Every decision that touches a person is written down.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque

from ..models import Priority
from .pii import redact

log = logging.getLogger("rahat.audit")

_PRESSURE = re.compile(
    r"\bmla\b|\bmp\b|minister|mantri|\bvip\b|sarpanch|collector|\bdc sahab|my uncle is|"
    r"i am (an? )?(officer|ias|ips)|paise? (de|dunga|dena)|bribe|reward|inaam|"
    r"pehle (mujhe|sanu|saanu)|sabse pehle mera|first me",
    re.I,
)
_CASUALTY = re.compile(r"\b\d+\s*(dead|died|deaths|bodies|maut|mare|ਮੌਤ|मौत)", re.I)


def pressure_detected(text: str) -> bool:
    return bool(_PRESSURE.search(text))


def bound_priority(proposed: Priority, rubric: Priority) -> Priority:
    """Clamp a model's priority to within one level of the SOP rubric."""
    lo, hi = max(1, rubric.rank - 1), min(4, rubric.rank + 1)
    r = min(max(proposed.rank, lo), hi)
    return Priority(f"P{r}")


def clean_reply(text: str, limit: int = 320) -> str:
    text, _ = redact(text)
    text = _CASUALTY.sub("[figure withheld]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


class AuditLog:
    """Append-only. Also emitted as one JSON line per entry, so Log Analytics keeps it."""

    def __init__(self, maxlen: int = 5000) -> None:
        self._items: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def write(self, event: str, sos_id: str | None = None, actor: str = "system", **details) -> dict:
        entry = {"ts": time.time(), "event": event, "sos_id": sos_id, "actor": actor, **details}
        with self._lock:
            self._items.append(entry)
        log.info(json.dumps({"audit": entry}, default=str))
        return entry

    def recent(self, n: int = 100) -> list[dict]:
        with self._lock:
            return list(self._items)[-n:][::-1]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


audit = AuditLog()
