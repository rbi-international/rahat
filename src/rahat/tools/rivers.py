"""
River gauge feed (simulated for the exercise).

In a real deployment this tool would call the Central Water Commission flood
forecast feed. For the drill it is a small state machine the instructor moves
with injects, so the whole class sees the same river.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LEVELS = ("normal", "warning", "danger", "extreme")


@dataclass
class Gauges:
    state: dict[str, str] = field(
        default_factory=lambda: {"Ravi": "warning", "Beas": "warning", "Sutlej": "normal"}
    )

    def level(self, river: str | None) -> str:
        return self.state.get(river or "", "normal")

    def is_danger(self, river: str | None) -> bool:
        return self.level(river) in ("danger", "extreme")

    def raise_level(self, river: str) -> str:
        cur = self.state.get(river, "normal")
        nxt = LEVELS[min(len(LEVELS) - 1, LEVELS.index(cur) + 1)]
        self.state[river] = nxt
        return nxt

    def reset(self) -> None:
        self.state = {"Ravi": "warning", "Beas": "warning", "Sutlej": "normal"}
