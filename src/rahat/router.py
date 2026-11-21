"""
Model router: a Thompson-sampling bandit that learns which model tier to use.

Plain version: for each kind of case, the router keeps a running guess of how
often the cheap model's triage is accepted by the duty officer, and how often
the strong model's is. Each new case, it samples from those guesses and picks a
tier. Accepted plans are reward; rejected plans are not. Cost is part of the
reward, so the cheap model wins whenever it is good enough.

This is reinforcement learning at inference time (a contextual bandit with
human feedback as reward). It is not RLHF on model weights, and should never
be described as such.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field

from . import metrics

COST = {"fast": 0.0, "strong": 0.15}  # reward penalty per tier, a stand-in for price


@dataclass
class Arm:
    alpha: float = 1.0
    beta: float = 1.0
    pulls: int = 0

    def sample(self) -> float:
        return random.betavariate(self.alpha, self.beta)

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


@dataclass
class Router:
    arms: dict[str, dict[str, Arm]] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @staticmethod
    def context(vulnerable: list[str], water_level: str) -> str:
        hard = bool(vulnerable) or water_level in ("chest", "roof")
        return "complex" if hard else "simple"

    def choose(self, ctx: str) -> str:
        with self._lock:
            arms = self.arms.setdefault(ctx, {"fast": Arm(), "strong": Arm()})
            scores = {t: a.sample() - COST[t] for t, a in arms.items()}
            tier = max(scores, key=scores.get)
            arms[tier].pulls += 1
        metrics.ROUTER_CHOICE.labels(ctx, tier).inc()
        return tier

    def reward(self, ctx: str, tier: str, accepted: bool) -> None:
        if tier not in ("fast", "strong"):
            return
        with self._lock:
            arm = self.arms.setdefault(ctx, {"fast": Arm(), "strong": Arm()})[tier]
            if accepted:
                arm.alpha += 1
            else:
                arm.beta += 1
            self.history.append({"ctx": ctx, "tier": tier, "accepted": accepted})

    def snapshot(self) -> dict:
        with self._lock:
            return {
                ctx: {t: {"accept_rate": round(a.mean, 3), "pulls": a.pulls} for t, a in arms.items()}
                for ctx, arms in self.arms.items()
            }

    def reset(self) -> None:
        with self._lock:
            self.arms.clear()
            self.history.clear()


router = Router()
