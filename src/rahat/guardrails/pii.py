"""
PII guard. Phone numbers, Aadhaar numbers and emails never reach a model.

Inbound: redact before any agent sees the text, keep the real value in a vault
the officer console can reach. Outbound: scan every reply and every public
answer again, because a model can echo or invent a number.
"""

from __future__ import annotations

import re
import threading

from .. import metrics

PHONE = re.compile(r"(?<![\d+])(?:\+91[\s-]?|0)?[6-9](?:[\s-]?\d){9}(?![\d])")
AADHAAR = re.compile(r"(?<![\d+])[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?![\d])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

_PATTERNS = (("aadhaar", AADHAAR), ("phone", PHONE), ("email", EMAIL))


class Vault:
    """Real values, keyed by SOS id. Officer-only. Cleared when the drill resets."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def put(self, sos_id: str, token: str, value: str) -> None:
        with self._lock:
            self._data.setdefault(sos_id, {})[token] = value

    def masked(self, sos_id: str) -> dict[str, str]:
        with self._lock:
            return {k: "xxxxxx" + re.sub(r"\D", "", v)[-4:] if "PHONE" in k else "[held]"
                    for k, v in self._data.get(sos_id, {}).items()}

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


vault = Vault()


def redact(text: str, sos_id: str | None = None) -> tuple[str, int]:
    count = 0
    for kind, pat in _PATTERNS:
        n = 0

        def _sub(m: re.Match, kind: str = kind) -> str:
            nonlocal n
            n += 1
            token = f"[{kind.upper()}_{n}]"
            if sos_id:
                vault.put(sos_id, token, m.group(0))
            return token

        text = pat.sub(_sub, text)
        if n:
            metrics.PII_REDACTIONS.labels(kind).inc(n)
        count += n
    return text, count


def contains_pii(text: str) -> bool:
    return any(p.search(text) for _, p in _PATTERNS)
