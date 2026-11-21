"""
The model gateway. Every agent's model call goes through complete_json().

What it guarantees, in order:
  1. The Neerdhara allocation is reserved before the call and charged after.
  2. A throttled or failing lane trips a circuit breaker, and the call moves to
     the other tier, then to the fallback lane, then to the SOP rules.
  3. The reply parses as JSON or it counts as a failure. Free text never flows
     downstream.
  4. Every call is metered: tokens, latency and result by agent and lane.

The caller always gets an answer. It may be a degraded one, and it says so.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from . import metrics
from .config import settings
from .lanes import LaneError, client_for, get_lane
from .ledger import BudgetExhausted, Ledger

log = logging.getLogger("rahat.llm")

ledger = Ledger(allocation=settings.allocation_tokens, low_ratio=settings.low_budget_ratio)


@dataclass
class LLMResult:
    data: dict
    tokens: int
    lane: str
    model: str
    tier: str
    degraded: bool
    ms: int


class CircuitBreaker:
    """Open after N consecutive failures, stay open for a cool-off, then try one call."""

    def __init__(self, threshold: int = 3, cooloff_s: float = 30.0) -> None:
        self.threshold = threshold
        self.cooloff_s = cooloff_s
        self._fails: dict[str, int] = {}
        self._opened: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        opened = self._opened.get(key)
        if opened is None:
            return True
        if time.time() - opened >= self.cooloff_s:
            return True  # half-open: let one call probe
        return False

    def success(self, key: str) -> None:
        self._fails[key] = 0
        if key in self._opened:
            del self._opened[key]
            lane, tier = key.split(":")
            metrics.BREAKER_OPEN.labels(lane, tier).set(0)

    def failure(self, key: str) -> None:
        self._fails[key] = self._fails.get(key, 0) + 1
        if self._fails[key] >= self.threshold:
            self._opened[key] = time.time()
            lane, tier = key.split(":")
            metrics.BREAKER_OPEN.labels(lane, tier).set(1)

    def state(self) -> dict[str, str]:
        now = time.time()
        return {
            k: ("open" if now - t < self.cooloff_s else "half-open") for k, t in self._opened.items()
        }


breaker = CircuitBreaker()

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


def _estimate(system: str, user: str) -> int:
    # A coarse 4-characters-per-token estimate plus room for the reply.
    return (len(system) + len(user)) // 4 + 400


def _model_name(lane_key: str, tier: str) -> str:
    lane = get_lane(lane_key)
    if lane_key == settings.provider:
        override = settings.model_strong if tier == "strong" else settings.model_fast
        if override:
            return override
    return lane.strong if tier == "strong" else lane.fast


def _route_plan(tier: str) -> list[tuple[str, str]]:
    """Ordered (lane, tier) attempts for one call."""
    other = "fast" if tier == "strong" else "strong"
    plan = [(settings.provider, tier), (settings.provider, other)]
    if settings.fallback_provider and settings.fallback_provider != settings.provider:
        plan.append((settings.fallback_provider, "fast"))
    return plan


async def _call_lane(lane_key: str, tier: str, system: str, user: str, max_tokens: int) -> tuple[dict, int]:
    lane = get_lane(lane_key)
    model = _model_name(lane_key, tier)
    client = client_for(lane, settings.request_timeout_s)
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    effort = settings.reasoning_strong if tier == "strong" else settings.reasoning_fast
    if lane_key == "foundry":
        # max_completion_tokens counts hidden reasoning tokens too, so leave room for them.
        kwargs["max_completion_tokens"] = max_tokens * (4 if effort and effort != "minimal" else 2)
    else:
        kwargs["max_tokens"] = max_tokens
    if settings.json_mode and lane.json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if effort and lane_key in ("foundry", "groq"):
        kwargs["reasoning_effort"] = effort
    resp = await client.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content or ""
    tokens = resp.usage.total_tokens if resp.usage else _estimate(system, user)
    return parse_json(text), tokens


async def complete_json(
    agent: str,
    system: str,
    user: str,
    rules: Callable[[], dict],
    tier: str = "fast",
    max_tokens: int = 700,
) -> LLMResult:
    """
    Ask a model for a JSON object. `rules` is the deterministic answer used by
    the fake lane and by degraded mode, so every agent has a floor.
    """
    t0 = time.perf_counter()

    # The fake lane: deterministic, costs pretend tokens, sleeps like a real call.
    if settings.provider == "fake":
        await asyncio.sleep(random.uniform(0.25, 0.9))
        tokens = _estimate(system, user) - 250
        ledger.commit(0, tokens, agent)
        metrics.LLM_TOKENS.labels(agent, "fake").inc(tokens)
        metrics.LLM_CALLS.labels(agent, "fake", tier, "ok").inc()
        return LLMResult(rules(), tokens, "fake", f"fake-{tier}", tier, False, _ms(t0))

    if ledger.low and tier == "strong":
        tier = "fast"  # budget is low: cheap model only

    estimate = _estimate(system, user)
    for lane_key, attempt_tier in _route_plan(tier):
        key = f"{lane_key}:{attempt_tier}"
        if not breaker.allow(key):
            continue
        try:
            held = ledger.reserve(estimate)
        except BudgetExhausted:
            log.warning("allocation exhausted, %s falls back to rules", agent)
            break
        c0 = time.perf_counter()
        try:
            data, tokens = await _call_lane(lane_key, attempt_tier, system, user, max_tokens)
        except (RateLimitError, APITimeoutError, APIConnectionError, LaneError) as exc:
            ledger.release(held)
            breaker.failure(key)
            metrics.LLM_CALLS.labels(agent, lane_key, attempt_tier, type(exc).__name__).inc()
            log.warning("%s on %s: %s", agent, key, exc)
            continue
        except APIStatusError as exc:
            ledger.release(held)
            if exc.status_code >= 500 or exc.status_code == 429:
                breaker.failure(key)
            metrics.LLM_CALLS.labels(agent, lane_key, attempt_tier, f"http_{exc.status_code}").inc()
            log.warning("%s on %s: HTTP %s", agent, key, exc.status_code)
            continue
        except (json.JSONDecodeError, ValueError) as exc:
            # The model answered but not in JSON. It still cost tokens.
            ledger.commit(held, estimate, agent)
            metrics.LLM_CALLS.labels(agent, lane_key, attempt_tier, "bad_json").inc()
            log.warning("%s on %s returned non-JSON: %s", agent, key, exc)
            continue
        ledger.commit(held, tokens, agent)
        breaker.success(key)
        metrics.LLM_LATENCY.labels(agent, lane_key).observe(time.perf_counter() - c0)
        metrics.LLM_TOKENS.labels(agent, lane_key).inc(tokens)
        metrics.LLM_CALLS.labels(agent, lane_key, attempt_tier, "ok").inc()
        return LLMResult(data, tokens, lane_key, _model_name(lane_key, attempt_tier), attempt_tier, False, _ms(t0))

    metrics.LLM_CALLS.labels(agent, "rules", tier, "degraded").inc()
    return LLMResult(rules(), 0, "rules", "sop-rules", "rules", True, _ms(t0))


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
