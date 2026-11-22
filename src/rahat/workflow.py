"""
The RAHAT workflow graph, built on Microsoft Agent Framework.

    guard ──switch──► review_queue                       (injection: human review)
      │
      └──default──► intake ──fan-out──► geo       ──┐
                                   └──► verifier  ──┴─fan-in──► triage ──switch──► info_desk
                                                                           └─default─► allocate ──► commander

Every primitive from the multi-agent unit appears once:
  switch-case routing   guard, triage
  fan-out / fan-in      geo and verifier run in parallel, triage waits for both
  blackboard            the incident record in the store, one field per agent
  manager judgement     the commander composes the plan from the whole board
  human in the loop     the plan stops at AWAITING_APPROVAL; an officer decides

Nodes pass the SOS id. The incident record is the shared board.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass

from agent_framework import Case, Default, WorkflowBuilder, WorkflowContext, executor

from . import metrics
from .agents import specialists
from .events import bus
from .guardrails import injection, pii
from .guardrails.policy import audit, pressure_detected
from .llm import LLMResult
from .models import AgentEvent, Incident, Status, Verification
from .router import router
from .store import store
from .tools import geo
from .tools.resources import Registry
from .tools.rivers import Gauges

registry = Registry.load()
gauges = Gauges()


@dataclass
class Gate:
    sos_id: str
    blocked: bool


@dataclass
class Routed:
    sos_id: str
    need: str


def emit(sos_id: str, agent: str, action: str, detail: str = "", res: LLMResult | None = None,
         level: str = "info") -> None:
    ev = AgentEvent(
        sos_id=sos_id,
        agent=agent,
        action=action,
        detail=detail[:160],
        tokens=res.tokens if res else 0,
        ms=res.ms if res else 0,
        level=level,
    )
    data = ev.model_dump()
    if res:
        data["lane"], data["tier"], data["model"], data["degraded"] = res.lane, res.tier, res.model, res.degraded
    bus.publish("agent", data)


def _charge(inc: Incident, res: LLMResult) -> None:
    inc.tokens_used += res.tokens
    inc.llm_calls += 1 if res.lane != "rules" else 0
    inc.degraded = inc.degraded or res.degraded


def _inc(sos_id: str) -> Incident:
    inc = store.get(sos_id)
    if inc is None:
        raise KeyError(sos_id)
    return inc


# --------------------------------------------------------------------- nodes

@executor(id="guard")
async def guard(sos_id: str, ctx: WorkflowContext[Gate]) -> None:
    inc = _inc(sos_id)
    inc.status = Status.PROCESSING
    emit(sos_id, "guard", "screening", "PII redaction and prompt-injection screen")
    clean, n = pii.redact(inc.sos.text, sos_id)
    inc.redactions = n
    ctx.set_state("clean_text", clean)
    if n:
        emit(sos_id, "guard", "redacted", f"{n} personal number(s) masked before any model", level="ok")
    result = await injection.screen(clean)
    if result.flagged:
        inc.blocked_reason = "possible prompt injection: " + ", ".join(result.kinds)
        inc.flags.append("injection")  # stays even if an officer later releases it from review
        for k in result.kinds:
            metrics.GUARD_BLOCKS.labels(f"injection_{k}").inc()
        audit.write("injection_flagged", sos_id, kinds=result.kinds, source=result.source)
        emit(sos_id, "guard", "blocked", inc.blocked_reason, level="block")
    if pressure_detected(clean):
        metrics.GUARD_BLOCKS.labels("fairness_pressure").inc()
        inc.flags.append("influence")
        audit.write("influence_attempt_ignored", sos_id)
        emit(sos_id, "policy", "fairness", "influence or money mentioned, priority unaffected", level="warn")
    await ctx.send_message(Gate(sos_id, result.flagged))


@executor(id="review_queue")
async def review_queue(g: Gate, ctx: WorkflowContext) -> None:
    inc = _inc(g.sos_id)
    inc.status = Status.BLOCKED
    inc.finished_at = time.time()
    emit(g.sos_id, "guard", "manual review", "taken off the automated path, officer will read it", level="block")
    await ctx.yield_output(g.sos_id)


@executor(id="intake")
async def intake(g: Gate, ctx: WorkflowContext[str]) -> None:
    inc = _inc(g.sos_id)
    emit(g.sos_id, "intake", "reading", "loading multilingual extraction schema")
    ext, res = await specialists.intake(ctx.get_state("clean_text"))
    inc.extraction = ext
    _charge(inc, res)
    emit(g.sos_id, "intake", "extracted",
         f"{ext.language} · {ext.people_count} people · water {ext.water_level} · {','.join(ext.vulnerable) or 'no vulnerable flagged'}",
         res)
    await ctx.send_message(g.sos_id)


@executor(id="geo")
async def geo_node(sos_id: str, ctx: WorkflowContext[str]) -> None:
    inc = _inc(sos_id)
    emit(sos_id, "geo", "tool call", "gazetteer.locate()")
    loc = geo.locate(inc.extraction.location_text if inc.extraction else inc.sos.text)
    if not loc.resolved:
        loc = geo.locate(inc.sos.text)
    inc.location = loc
    if loc.resolved:
        emit(sos_id, "geo", "located", f"{loc.village}, {loc.district} · {loc.river} · {loc.method}", level="ok")
    else:
        emit(sos_id, "geo", "unresolved", "no known place in message, officer must call back", level="warn")
    await ctx.send_message("geo")


def _mark_asked_location(inc: Incident) -> None:
    """The commander's reply asked the citizen to resend with a landmark (see specialists.commander)."""
    if not (inc.location and inc.location.resolved) and "asked_location" not in inc.flags:
        inc.flags.append("asked_location")
        emit(inc.sos.id, "commander", "asked for location", "citizen asked to resend with a village or landmark",
             level="warn")


def _location_follow_up(other_id: str, device: str, inc: Incident) -> bool:
    """A resend from the same phone after RAHAT asked where they are is an answer, not a duplicate."""
    if device != inc.sos.device_id:
        return False
    other = store.get(other_id)
    return bool(other and "asked_location" in other.flags)


@executor(id="verifier")
async def verifier(sos_id: str, ctx: WorkflowContext[str]) -> None:
    inc = _inc(sos_id)
    flags: list[str] = []
    dup_of = None
    for other_id, device, text in store.recent_texts():
        if other_id == sos_id:
            continue
        if _location_follow_up(other_id, device, inc):
            if "location follow-up" not in flags:
                flags.append("location follow-up")
            continue
        if difflib.SequenceMatcher(None, text.lower(), inc.sos.text.lower()).ratio() > 0.9:
            dup_of = other_id
            flags.append("same device repeat" if device == inc.sos.device_id else "near-identical text")
            break
    ext = inc.extraction
    if ext and ext.people_count > 60:
        flags.append("implausible head count")
    doubts = [f for f in flags if f != "location follow-up"]
    inc.verification = Verification(genuine=not doubts, duplicate_of=dup_of, flags=flags)
    if dup_of:
        inc.flags.append("duplicate")
    emit(sos_id, "verifier", "checked", ", ".join(flags) or "no duplicate, plausible",
         level="warn" if flags else "ok")
    await ctx.send_message("verifier")


@executor(id="triage")
async def triage_node(done: list[str], ctx: WorkflowContext[Routed]) -> None:
    sos_id = ctx.get_state("sos_id")
    inc = _inc(sos_id)
    ext, loc = inc.extraction, inc.location
    rctx = router.context(ext.vulnerable, ext.water_level)
    tier = router.choose(rctx)
    ctx.set_state("router", (rctx, tier))
    emit(sos_id, "router", "chose tier", f"{rctx} case → {tier} model")
    tri, res = await specialists.triage(ext, loc, gauges.level(loc.river), tier)
    if inc.verification and inc.verification.duplicate_of:
        tri.reason += f" · duplicate of {inc.verification.duplicate_of}"
    inc.triage = tri
    _charge(inc, res)
    metrics.PRIORITY.labels(tri.priority.value).inc()
    emit(sos_id, "triage", tri.priority.value, tri.reason, res,
         level="block" if tri.priority.value == "P1" else "info")
    await ctx.send_message(Routed(sos_id, ext.need))


@executor(id="info_desk")
async def info_desk(r: Routed, ctx: WorkflowContext) -> None:
    inc = _inc(r.sos_id)
    plan, res = await specialists.commander(inc.extraction, inc.location, inc.triage,
                                            registry.hold_nearest(r.sos_id, inc.location, "information",
                                                                  inc.triage.priority, 0))
    inc.plan = plan
    _charge(inc, res)
    _mark_asked_location(inc)
    inc.status = Status.INFO_SENT
    inc.finished_at = time.time()
    emit(r.sos_id, "info_desk", "replied", plan.citizen_reply, res, level="ok")
    await ctx.yield_output(r.sos_id)


@executor(id="allocate")
async def allocate(r: Routed, ctx: WorkflowContext[str]) -> None:
    inc = _inc(r.sos_id)
    emit(r.sos_id, "resource", "tool call", "registry.hold_nearest()")
    asg = registry.hold_nearest(r.sos_id, inc.location, inc.extraction.need, inc.triage.priority,
                                inc.extraction.people_count)
    inc.assignment = asg
    if asg.asset_id:
        emit(r.sos_id, "resource", "held", f"{asg.asset_label} {asg.note} · {asg.distance_km} km · ETA {asg.eta_min} min", level="ok")
    else:
        emit(r.sos_id, "resource", "no unit", asg.note, level="warn")
    await ctx.send_message(r.sos_id)


@executor(id="commander")
async def commander(sos_id: str, ctx: WorkflowContext) -> None:
    inc = _inc(sos_id)
    plan, res = await specialists.commander(inc.extraction, inc.location, inc.triage, inc.assignment)
    inc.plan = plan
    _charge(inc, res)
    _mark_asked_location(inc)
    inc.status = Status.AWAITING_APPROVAL
    inc.finished_at = time.time()
    emit(sos_id, "commander", "plan ready", plan.officer_brief, res, level="ok")
    audit.write("plan_proposed", sos_id, priority=inc.triage.priority.value,
                asset=inc.assignment.asset_id if inc.assignment else None)
    await ctx.yield_output(sos_id)


@executor(id="start")
async def start(sos_id: str, ctx: WorkflowContext[str]) -> None:
    ctx.set_state("sos_id", sos_id)
    await ctx.send_message(sos_id)


def build_workflow():
    return (
        WorkflowBuilder(start_executor=start)
        .add_edge(start, guard)
        .add_switch_case_edge_group(
            guard,
            [Case(condition=lambda g: g.blocked, target=review_queue), Default(target=intake)],
        )
        .add_fan_out_edges(intake, [geo_node, verifier])
        .add_fan_in_edges([geo_node, verifier], triage_node)
        .add_switch_case_edge_group(
            triage_node,
            [Case(condition=lambda r: r.need == "information", target=info_desk), Default(target=allocate)],
        )
        .add_edge(allocate, commander)
        .build()
    )


async def run_incident(sos_id: str) -> Incident:
    """Run one SOS through the graph. Any crash leaves a FAILED record, never a silent drop."""
    t0 = time.perf_counter()
    inc = _inc(sos_id)
    try:
        wf = build_workflow()
        await wf.run(sos_id)
    except Exception as exc:  # noqa: BLE001 a single bad message must never stop the room
        inc.status = Status.FAILED
        inc.finished_at = time.time()
        emit(sos_id, "system", "failed", f"{type(exc).__name__}: {exc}", level="block")
        audit.write("pipeline_failed", sos_id, error=str(exc)[:300])
    metrics.PIPELINE_LATENCY.observe(time.perf_counter() - t0)
    metrics.SOS_OUTCOME.labels(inc.status.value).inc()
    bus.publish("incident", inc.public_view())
    return inc
