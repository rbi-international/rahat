"""Concurrent SOS must run concurrently. Shared executors once let one SOS at a time through each node."""

import asyncio

from rahat import workflow
from rahat.agents import specialists
from rahat.models import SOS, Incident
from rahat.store import store


def test_concurrent_incidents_overlap_their_model_calls(monkeypatch):
    monkeypatch.setenv("CONTENT_SAFETY_ENDPOINT", "")
    live = {"now": 0, "max": 0}
    real = specialists.complete_json

    async def counting(agent, *a, **kw):
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        try:
            return await real(agent, *a, **kw)
        finally:
            live["now"] -= 1

    monkeypatch.setattr(specialists, "complete_json", counting)

    async def run():
        ids = []
        for i in range(10):
            s = SOS(text=f"3 log, paani chhati tak, Kalanaur near the school, message {i}", device_id=f"conc-{i}")
            store.add(Incident(sos=s))
            ids.append(s.id)
        await asyncio.gather(*(workflow.run_incident(x) for x in ids))
        return ids

    ids = asyncio.run(run())
    assert all(store.get(x).plan for x in ids)
    assert live["max"] >= 8, f"only {live['max']} model calls overlapped across 10 concurrent SOS"


def test_each_workflow_gets_its_own_executors():
    a, b = workflow.build_workflow(), workflow.build_workflow()
    ea = {e.id: e for e in a.executors.values()} if hasattr(a, "executors") else {}
    eb = {e.id: e for e in b.executors.values()} if hasattr(b, "executors") else {}
    assert ea and ea.keys() == eb.keys()
    assert all(ea[k] is not eb[k] for k in ea)
