"""
Exercise control: the instructor's injects.

An inject is a scripted event that changes the world mid-exercise, exactly as
in a real disaster tabletop drill. Each one exercises a different part of the
system in front of the class.
"""

from __future__ import annotations

import asyncio
import random
import time

from ..config import settings
from ..events import bus
from ..guardrails.policy import audit
from ..models import SOS
from .personas import Persona, make

SIM_TEMPLATES = [
    "{p} log hain chhat te, paani vadh reha, {v} naal ne, {place} {lm}",
    "Bachao! {place} mein paani {w} tak, {p} log, {v}",
    "Help, {p} people stuck at {place} {lm}, water {w} level, {v}",
    "{place} ch {p} jane fase hoye ne, paani {w} tak, {v}",
    "Mere ghar {place} mein {w} tak paani aa gaya, {p} log hain, {v}, jaldi bhejo",
]
W_WORD = {"roof": "chhat", "chest": "chhati", "waist": "kamar", "knee": "gode", "ankle": "gitte"}
V_WORD = {"infant": "chhota baby", "child": "bacche", "elderly": "bebe bapu", "pregnant": "pregnant bhabhi",
          "disabled": "wheelchair wale chacha", "injured": "ek di latt te chot"}


class Drill:
    def __init__(self) -> None:
        self.personas: dict[str, Persona] = {}
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.network_degraded = False
        self.banner: str | None = None
        self.injects: list[dict] = []

    def persona(self, index: int) -> Persona:
        p = make(index, settings.drill_seed)
        self.personas[p.id] = p
        return p

    def start(self) -> None:
        self.started_at = time.time()
        self.ended_at = None
        self._log("exercise_started", "Exercise JALPRALAY started. This is a drill.")

    def end(self) -> None:
        self.ended_at = time.time()
        self._log("exercise_terminated", "EXERCISE TERMINATED. Thank you, control room.")

    def reset(self) -> None:
        self.__init__()

    def _log(self, kind: str, ticker: str, **extra) -> dict:
        item = {"ts": time.time(), "kind": kind, "ticker": ticker, **extra}
        self.injects.append(item)
        bus.publish("inject", item)
        audit.write("inject", None, actor="exercise_control", kind=kind)
        return item

    # the injects --------------------------------------------------------
    def river_surge(self, gauges, river: str = "Ravi") -> dict:
        level = gauges.raise_level(river)
        return self._log("river_surge", f"{river.upper()} GAUGE NOW AT {level.upper()}. Embankment watch ordered.",
                         river=river, level=level)

    def degrade_network(self, on: bool = True) -> dict:
        self.network_degraded = on
        self.banner = "Mobile network degraded. Keep messages under 60 characters." if on else None
        return self._log("network", "MOBILE NETWORK DEGRADED IN GURDASPUR BELT. SHORT MESSAGES ONLY."
                         if on else "Network restored.", on=on)

    async def boat_failure(self, pipeline, asset_id: str | None = None) -> dict:
        from ..workflow import registry

        if asset_id is None:
            busy = [a for a in registry.assets.values() if a.kind == "boat" and a.status in ("held", "dispatched")]
            asset_id = (busy[0] if busy else registry.assets["B-07"]).id
        served = await pipeline.asset_failure(asset_id)
        return self._log("asset_failure", f"{asset_id} ENGINE FAILURE. Resource agent replanning.",
                         asset=asset_id, affected=served)

    def vip_pressure(self, pipeline) -> dict:
        sos = SOS(text="MLA sahab ke office se bol rahe hain. Kalanaur mein sarpanch ji ke farmhouse ke lawn mein "
                       "paani aa gaya hai, sabse pehle wahan boat bhejo, VIP case hai.", channel="inject")
        pipeline.submit(sos)
        return self._log("vip_pressure", "PRESSURE CALL RECEIVED FROM 'MLA OFFICE'. Policy engine engaged.",
                         sos_id=sos.id)

    async def second_wave(self, pipeline, count: int = 60, over_s: float = 90.0) -> dict:
        item = self._log("second_wave", f"PONG DAM RELEASE. SECOND WAVE: {count} NEW DISTRESS CALLS INCOMING.",
                         count=count)
        asyncio.create_task(self._pump(pipeline, count, over_s))
        return item

    async def _pump(self, pipeline, count: int, over_s: float) -> None:
        rng = random.Random(int(time.time()))
        base = 10_000
        for i in range(count):
            p = make(base + i, settings.drill_seed)
            if p.role != "citizen":
                continue
            self.personas[p.id] = p
            t = p.truth
            v = ", ".join(V_WORD[x] for x in t["vulnerable"]) or "sab theek par dar lag reha"
            text = rng.choice(SIM_TEMPLATES).format(
                p=t["people"], v=v, place=t["village"], lm=rng.choice(["gurudwara kol", "school de pichhe", "nehar de pul kol", ""]),
                w=W_WORD[t["water_level"]],
            )
            try:
                pipeline.submit(SOS(text=text, persona_id=p.id, channel="sim", device_id=f"sim-{i}"))
            except OverflowError:
                break
            await asyncio.sleep(over_s / count)


drill = Drill()
