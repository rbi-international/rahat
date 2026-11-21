"""
Geo-locator tool. Turns "near the gurudwara in Dera Baba Nanak" into coordinates.

Deliberately not a model call. Matching a place name against a known gazetteer
is exact work, and code does exact work better, cheaper and faster than an LLM.
"""

from __future__ import annotations

import difflib
import json
import math
import re
from functools import lru_cache
from importlib import resources

from ..models import Location


@lru_cache(maxsize=1)
def gazetteer() -> list[dict]:
    raw = resources.files("rahat.data").joinpath("gazetteer.json").read_text(encoding="utf-8")
    return json.loads(raw)["places"]


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", " ", s.lower()).strip()


def locate(text: str) -> Location:
    """Exact alias match first (longest alias wins), then fuzzy match on words."""
    low = " " + _norm(text) + " "
    best = None
    best_len = 0
    for place in gazetteer():
        for alias in place["aliases"]:
            a = _norm(alias)
            if not a:
                continue
            if f" {a} " in low or (not a.isascii() and a in low):
                if len(a) > best_len:
                    best, best_len = place, len(a)
    if best:
        return _loc(best, 0.95, "alias")

    words = [w for w in low.split() if len(w) >= 5]
    names = {}
    for place in gazetteer():
        for alias in place["aliases"]:
            if alias.isascii():
                names[_norm(alias)] = place
    for w in words:
        hit = difflib.get_close_matches(w, list(names), n=1, cutoff=0.82)
        if hit:
            return _loc(names[hit[0]], 0.7, "fuzzy")
    return Location(resolved=False, method="none")


def _loc(place: dict, conf: float, method: str) -> Location:
    return Location(
        resolved=True,
        village=place["name"],
        district=place["district"],
        lat=place["lat"],
        lon=place["lon"],
        river=place["river"],
        confidence=conf,
        method=method,
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
