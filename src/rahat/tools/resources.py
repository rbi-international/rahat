"""
Resource registry and allocator: boats, NDRF teams, medical units, relief camps.

Assignment is an optimisation over known facts (distance, availability,
capability), so it is code, not a model. The Commander agent explains the
plan in words; it does not choose the boat.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from importlib import resources

from ..models import Assignment, Location, Priority
from .geo import haversine_km


@dataclass
class Asset:
    id: str
    kind: str
    label: str
    base: str
    lat: float
    lon: float
    capacity: int
    speed_kmh: float
    status: str = "available"  # available, held, dispatched, out_of_service
    assigned_to: str | None = None
    trips: int = 0


@dataclass
class Registry:
    assets: dict[str, Asset] = field(default_factory=dict)
    camps: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def load(cls) -> Registry:
        raw = json.loads(resources.files("rahat.data").joinpath("assets.json").read_text())
        reg = cls()
        for a in raw["assets"]:
            reg.assets[a["id"]] = Asset(**a)
        reg.camps = raw["camps"]
        return reg

    def _eligible(self, need: str, priority: Priority) -> list[str]:
        if need == "medical":
            return ["medical", "boat", "ndrf"] if priority.rank <= 2 else ["medical"]
        if need == "rescue":
            return ["ndrf", "boat"] if priority == Priority.P1 else ["boat", "ndrf"]
        return []

    def hold_nearest(self, sos_id: str, loc: Location, need: str, priority: Priority, people: int) -> Assignment:
        """Hold the nearest suitable available asset for an SOS. The officer confirms or releases."""
        if not loc.resolved or loc.lat is None:
            return Assignment(note="location unresolved, officer must call back")
        kinds = self._eligible(need, priority)
        camp = min(self.camps, key=lambda c: haversine_km(loc.lat, loc.lon, c["lat"], c["lon"]))
        if not kinds:
            return Assignment(camp=camp["name"], note="no asset needed, guidance only")
        with self._lock:
            best: tuple[float, Asset] | None = None
            for kind_rank, kind in enumerate(kinds):
                for a in self.assets.values():
                    if a.kind != kind or a.status != "available":
                        continue
                    d = haversine_km(loc.lat, loc.lon, a.lat, a.lon)
                    # prefer the first-listed kind unless it is much further away
                    cost = d + kind_rank * 15
                    if people > a.capacity:
                        cost += 10
                    if best is None or cost < best[0]:
                        best = (cost, a)
            if best is None:
                return Assignment(camp=camp["name"], note="all suitable assets busy, queued for next free")
            a = best[1]
            a.status, a.assigned_to = "held", sos_id
        d = haversine_km(loc.lat, loc.lon, a.lat, a.lon)
        eta = int(round(d / a.speed_kmh * 60)) + 8
        return Assignment(
            asset_id=a.id,
            asset_kind=a.kind,
            asset_label=a.label,
            distance_km=round(d, 1),
            eta_min=eta,
            camp=camp["name"],
            note=f"from {a.base}",
        )

    def confirm(self, asset_id: str | None) -> None:
        if asset_id and asset_id in self.assets:
            with self._lock:
                a = self.assets[asset_id]
                a.status = "dispatched"
                a.trips += 1

    def release(self, asset_id: str | None) -> None:
        if asset_id and asset_id in self.assets:
            with self._lock:
                a = self.assets[asset_id]
                if a.status in ("held", "dispatched"):
                    a.status, a.assigned_to = "available", None

    def take_out_of_service(self, asset_id: str) -> str | None:
        """Inject: an asset breaks down. Returns the SOS it was serving, if any."""
        with self._lock:
            a = self.assets.get(asset_id)
            if not a:
                return None
            served = a.assigned_to
            a.status, a.assigned_to = "out_of_service", None
            return served

    def complete_trip(self, asset_id: str) -> None:
        self.release(asset_id)

    def summary(self) -> dict:
        out: dict[str, dict[str, int]] = {}
        for a in self.assets.values():
            out.setdefault(a.kind, {}).setdefault(a.status, 0)
            out[a.kind][a.status] += 1
        return out

    def snapshot(self) -> list[dict]:
        return [
            {"id": a.id, "kind": a.kind, "label": a.label, "lat": a.lat, "lon": a.lon,
             "status": a.status, "assigned_to": a.assigned_to, "base": a.base}
            for a in self.assets.values()
        ]
