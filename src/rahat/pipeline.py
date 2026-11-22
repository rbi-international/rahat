"""
The control room runtime: queue, workers, the human decision, and the scheduler.

  queue      SOS messages wait here with a visible position. Backpressure, not
             collapse: when every worker is busy, people see "position 14".
  workers    A fixed pool runs the workflow. The pool size, not the crowd size,
             sets the load on the model provider.
  officer    approve / reject. Approval dispatches the asset; both outcomes are
             the router's reward signal.
  scheduler  dispatched assets come back after their trip (time compressed for
             the drill) and pick up the most urgent case still waiting.
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import metrics
from .agents import specialists
from .config import settings
from .drill import scoring
from .drill.control import drill
from .events import bus
from .guardrails.policy import audit
from .llm import ledger
from .models import SOS, Incident, Status
from .router import router
from .store import store
from .workflow import emit, registry, run_incident

log = logging.getLogger("rahat.pipeline")

TRIP_SECONDS_PER_ETA_MIN = 1.5  # drill time compression: a 10 minute ETA trip takes 30 s round trip


def wall_stats() -> dict:
    """Board counters for the wall. Attacks stopped uses the findings' own definition, so the two always agree."""
    st = store.stats()
    st["attacks_stopped"] = scoring.attacks_stopped(store.all(), drill.personas)
    return st


class Pipeline:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] | None = None
        self.workers: list[asyncio.Task] = []
        self.in_flight = 0
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.queue = asyncio.Queue(maxsize=settings.queue_max)
        self.workers = [asyncio.create_task(self._worker(i)) for i in range(settings.workers)]
        self._spawn(self._ticker())

    async def stop(self) -> None:
        for t in self.workers + list(self._tasks):
            t.cancel()

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    # intake ---------------------------------------------------------------
    def submit(self, sos: SOS) -> tuple[Incident, int]:
        if self.queue is None:
            raise RuntimeError("pipeline not started")
        if self.queue.full():
            raise OverflowError("control room queue is full")
        inc = Incident(sos=sos)
        store.add(inc)
        self.queue.put_nowait(sos.id)
        metrics.SOS_RECEIVED.labels(sos.channel).inc()
        metrics.QUEUE_DEPTH.set(self.queue.qsize())
        bus.publish("incident", inc.public_view())
        emit(sos.id, "gateway", "queued", f"position {self.queue.qsize()}")
        return inc, self.queue.qsize()

    def position(self, sos_id: str) -> int | None:
        inc = store.get(sos_id)
        if not inc or inc.status != Status.QUEUED:
            return None
        queued = sorted((i for i in store.by_status(Status.QUEUED)), key=lambda i: i.sos.received_at)
        for n, i in enumerate(queued, 1):
            if i.sos.id == sos_id:
                return n
        return None

    async def _worker(self, n: int) -> None:
        assert self.queue is not None
        while True:
            sos_id = await self.queue.get()
            self.in_flight += 1
            metrics.IN_FLIGHT.set(self.in_flight)
            metrics.QUEUE_DEPTH.set(self.queue.qsize())
            try:
                await run_incident(sos_id)
            finally:
                self.in_flight -= 1
                metrics.IN_FLIGHT.set(self.in_flight)
                self.queue.task_done()

    async def _ticker(self) -> None:
        """Push ledger and queue state to the screens twice a second."""
        while True:
            snap = ledger.snapshot()
            metrics.ALLOCATION_REMAINING.set(snap["remaining"])
            metrics.ALLOCATION_USED.set(snap["used"])
            bus.publish("tick", {
                "ledger": snap,
                "queue": self.queue.qsize() if self.queue else 0,
                "in_flight": self.in_flight,
                "assets": registry.summary(),
                "stats": wall_stats(),
            })
            await asyncio.sleep(0.5)

    # the human decision ---------------------------------------------------
    def decide(self, sos_id: str, approve: bool, officer: str) -> Incident:
        inc = store.get(sos_id)
        if inc is None:
            raise KeyError(sos_id)
        if inc.status not in (Status.AWAITING_APPROVAL, Status.BLOCKED):
            raise ValueError(f"{sos_id} is {inc.status.value}, nothing to decide")
        was_blocked = inc.status == Status.BLOCKED
        inc.decided_at = time.time()
        rctx = router.context(inc.extraction.vulnerable, inc.extraction.water_level) if inc.extraction else "simple"
        tier = inc.triage.model_tier if inc.triage else "rules"
        if approve and was_blocked:
            # A human read a flagged message and judged it genuine: run it through the automated path.
            inc.blocked_reason = None
            inc.status = Status.QUEUED
            audit.write("review_released", sos_id, actor=officer)
            self.queue.put_nowait(sos_id)  # type: ignore[union-attr]
            return inc
        if approve and not (inc.assignment and inc.assignment.asset_id):
            raise ValueError("no unit is held for this case yet; it is assigned when one frees up")
        if approve:
            registry.confirm(inc.assignment.asset_id if inc.assignment else None)
            inc.status = Status.DISPATCHED
            metrics.OFFICER_DECISION.labels("approve").inc()
            router.reward(rctx, tier, True)
            audit.write("dispatch_approved", sos_id, actor=officer,
                        asset=inc.assignment.asset_id if inc.assignment else None)
            emit(sos_id, "officer", "approved", f"{inc.assignment.asset_label if inc.assignment else 'no unit'} dispatched", level="ok")
            if inc.assignment and inc.assignment.asset_id:
                self._spawn(self._trip(inc.assignment.asset_id, inc.assignment.eta_min or 10))
        else:
            registry.release(inc.assignment.asset_id if inc.assignment else None)
            inc.status = Status.REJECTED
            metrics.OFFICER_DECISION.labels("reject").inc()
            if not was_blocked:
                router.reward(rctx, tier, False)
            audit.write("dispatch_rejected", sos_id, actor=officer)
            emit(sos_id, "officer", "rejected", "plan rejected by duty officer", level="warn")
        bus.publish("incident", inc.public_view())
        return inc

    async def _trip(self, asset_id: str, eta_min: int) -> None:
        await asyncio.sleep(min(90.0, max(8.0, eta_min * TRIP_SECONDS_PER_ETA_MIN)))
        registry.complete_trip(asset_id)
        bus.publish("asset", {"id": asset_id, "status": "available"})
        await self.replan_waiting()

    async def replan_waiting(self) -> None:
        """Give freed units to the most urgent plan that has no unit yet."""
        waiting = [i for i in store.by_status(Status.AWAITING_APPROVAL)
                   if i.assignment and not i.assignment.asset_id and i.location and i.location.resolved]
        waiting.sort(key=lambda i: (i.triage.priority.rank if i.triage else 9, i.sos.received_at))
        for inc in waiting[:3]:
            await self.reallocate(inc, reason="unit freed")

    async def reallocate(self, inc: Incident, reason: str) -> None:
        if inc.assignment:
            registry.release(inc.assignment.asset_id)
        asg = registry.hold_nearest(inc.sos.id, inc.location, inc.extraction.need, inc.triage.priority,
                                    inc.extraction.people_count)
        if not asg.asset_id:
            return
        inc.assignment = asg
        emit(inc.sos.id, "resource", "replanned", f"{reason}: {asg.asset_label} {asg.note}, ETA {asg.eta_min} min", level="warn")
        plan, res = await specialists.commander(inc.extraction, inc.location, inc.triage, asg)
        inc.plan = plan
        inc.tokens_used += res.tokens
        emit(inc.sos.id, "commander", "plan revised", plan.officer_brief, res, level="ok")
        bus.publish("incident", inc.public_view())

    async def asset_failure(self, asset_id: str) -> str | None:
        served = registry.take_out_of_service(asset_id)
        audit.write("asset_out_of_service", served, asset=asset_id)
        bus.publish("asset", {"id": asset_id, "status": "out_of_service"})
        if served:
            inc = store.get(served)
            if inc and inc.status in (Status.AWAITING_APPROVAL, Status.DISPATCHED):
                inc.status = Status.AWAITING_APPROVAL
                await self.reallocate(inc, reason=f"{asset_id} broke down")
        return served


pipeline = Pipeline()
