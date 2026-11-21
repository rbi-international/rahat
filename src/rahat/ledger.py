"""
The token ledger: the Neerdhara Systems allocation, enforced in code.

Azure Pay As You Go has no hard spending limit, only budget emails that arrive
a day late. So the real cap lives here. Every model call reserves an estimate
before it runs and commits the true usage after. When the balance is gone, the
ledger refuses, and the system falls back to rules-only triage instead of
spending money it was never given.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class Ledger:
    allocation: int
    low_ratio: float = 0.10
    _used: int = 0
    _reserved: int = 0
    _by_agent: dict[str, int] = field(default_factory=dict)
    _calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.allocation - self._used - self._reserved)

    @property
    def low(self) -> bool:
        return self.remaining <= self.allocation * self.low_ratio

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def reserve(self, estimate: int) -> int:
        """Hold tokens for a call about to run. Raises if the allocation cannot cover it."""
        with self._lock:
            if self.allocation - self._used - self._reserved < estimate:
                raise BudgetExhausted(
                    f"allocation exhausted: {self.remaining} tokens left, call needs ~{estimate}"
                )
            self._reserved += estimate
            return estimate

    def commit(self, reserved: int, actual: int, agent: str) -> None:
        """Release the hold and charge what the provider actually reported."""
        with self._lock:
            self._reserved = max(0, self._reserved - reserved)
            self._used += actual
            self._calls += 1
            self._by_agent[agent] = self._by_agent.get(agent, 0) + actual

    def release(self, reserved: int) -> None:
        with self._lock:
            self._reserved = max(0, self._reserved - reserved)

    def reset(self, allocation: int | None = None) -> None:
        with self._lock:
            if allocation is not None:
                self.allocation = allocation
            self._used = 0
            self._reserved = 0
            self._calls = 0
            self._by_agent.clear()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "allocation": self.allocation,
                "used": self._used,
                "reserved": self._reserved,
                "remaining": max(0, self.allocation - self._used - self._reserved),
                "calls": self._calls,
                "by_agent": dict(self._by_agent),
                "low": self.remaining <= self.allocation * self.low_ratio,
            }
