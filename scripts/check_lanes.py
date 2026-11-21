"""
Preflight: one real call per tier on the configured lane, before anyone is watching.

    python scripts/check_lanes.py

Prints latency, tokens and whether the reply parsed as JSON. If a tier fails
here it would silently fall back to SOP rules in class, so fix it now.
"""

from __future__ import annotations

import asyncio
import sys
import time

from rahat.config import settings
from rahat.lanes import LaneError, get_lane
from rahat.llm import _call_lane, _model_name

PROBE_SYS = 'Reply ONLY with JSON: {"ok": true, "lang": "<language of the message>"}'
PROBE_USER = "Paani chhat tak aa gaya hai, Dera Baba Nanak"


async def probe(lane_key: str, tier: str) -> bool:
    t0 = time.perf_counter()
    try:
        data, tokens = await _call_lane(lane_key, tier, PROBE_SYS, PROBE_USER, 120)
    except LaneError as exc:
        print(f"  {lane_key:8} {tier:6} NOT CONFIGURED  {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 report every failure mode plainly
        print(f"  {lane_key:8} {tier:6} FAILED  {type(exc).__name__}: {str(exc)[:200]}")
        return False
    ms = int((time.perf_counter() - t0) * 1000)
    print(f"  {lane_key:8} {tier:6} ok  model={_model_name(lane_key, tier)}  {ms} ms  {tokens} tokens  reply={data}")
    return True


async def main() -> int:
    lanes = [settings.provider] + ([settings.fallback_provider] if settings.fallback_provider else [])
    print(f"Org allocation: {settings.allocation_tokens:,} tokens ({settings.org_name})")
    ok = True
    for lane in lanes:
        if lane == "fake":
            print("  fake     offline lane, nothing to check")
            continue
        print(f"{get_lane(lane).name}")
        for tier in ("fast", "strong"):
            ok &= await probe(lane, tier)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
