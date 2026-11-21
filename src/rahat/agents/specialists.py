"""
The four model-backed specialists. Each one:
  - builds its prompt from typed inputs,
  - calls the gateway with a rules fallback (so it can never return nothing),
  - validates the reply into a typed model before anyone downstream sees it.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from .. import rules
from ..guardrails.policy import bound_priority, clean_reply
from ..llm import LLMResult, complete_json
from ..models import Assignment, Extraction, Location, Plan, Priority, Triage
from . import prompts

_VULN_OK = {"infant", "child", "elderly", "pregnant", "disabled", "injured"}


async def intake(clean_text: str) -> tuple[Extraction, LLMResult]:
    def fallback() -> dict:
        return rules.extract(clean_text).model_dump()

    res = await complete_json(
        "intake", prompts.INTAKE, f"MESSAGE:\n{clean_text}", fallback, tier="fast", max_tokens=400
    )
    try:
        data = dict(res.data)
        data["vulnerable"] = [v for v in data.get("vulnerable", []) if v in _VULN_OK]
        ext = Extraction.model_validate(data)
    except ValidationError:
        ext = rules.extract(clean_text)
        res.degraded = True
    # Rules catch what a model sometimes drops (the place name, a roof, a child).
    hint = rules.extract(clean_text)
    if not ext.location_text:
        ext.location_text = clean_text
    if ext.water_level == "unknown" and hint.water_level != "unknown":
        ext.water_level = hint.water_level
    return ext, res


async def triage(ext: Extraction, loc: Location, river_level: str, tier: str) -> tuple[Triage, LLMResult]:
    danger = river_level in ("danger", "extreme")
    score = rules.rubric_score(ext.water_level, ext.vulnerable, ext.people_count, ext.need, danger)
    sop = rules.priority_for(score)

    def fallback() -> dict:
        return {"priority": sop.value, "reason": f"SOP score {score:.0f}"}

    facts = {
        "facts": ext.model_dump(exclude={"location_text"}),
        "village": loc.village,
        "river": loc.river,
        "river_gauge": river_level,
        "sop_score": score,
        "sop_priority": sop.value,
    }
    res = await complete_json(
        "triage",
        prompts.TRIAGE.format(rubric=rules.RUBRIC_TEXT),
        json.dumps(facts, ensure_ascii=False),
        fallback,
        tier=tier,
        max_tokens=200,
    )
    try:
        proposed = Priority(str(res.data.get("priority", sop.value)).upper())
    except ValueError:
        proposed = sop
    final = bound_priority(proposed, sop)
    reason = str(res.data.get("reason", ""))[:200] or f"SOP score {score:.0f}"
    if final != proposed:
        reason += f" (clamped from {proposed.value}: max one level from SOP)"
    return Triage(priority=final, score=score, reason=reason, model_tier=res.tier, river_level=river_level), res


async def commander(
    ext: Extraction, loc: Location, tri: Triage, asg: Assignment
) -> tuple[Plan, LLMResult]:
    eta = f"about {asg.eta_min} minutes" if asg.eta_min else "as soon as a unit is free"

    def fallback() -> dict:
        if ext.need == "information":
            camp = asg.camp or "the nearest relief camp"
            return {
                "officer_brief": f"Information request near {loc.village or 'unknown place'}, guidance sent.",
                "citizen_reply": f"Nearest open point: {camp}. If water rises, move to high ground and send a new SOS. Helpline 1070.",
            }
        brief = (
            f"{tri.priority.value}: {ext.people_count} people, water {ext.water_level}, "
            f"{loc.village or 'location unclear'}. {asg.asset_label or 'No unit free'}, ETA {asg.eta_min or '?'} min."
        )
        reply = {
            "pa": f"ਤੁਹਾਡਾ ਸੁਨੇਹਾ ਮਿਲ ਗਿਆ ਹੈ। ਮਦਦ ਭੇਜੀ ਜਾ ਰਹੀ ਹੈ, ਲਗਭਗ {asg.eta_min or '?'} ਮਿੰਟ। ਸਭ ਤੋਂ ਉੱਚੀ ਥਾਂ 'ਤੇ ਰਹੋ।",
            "hi": f"आपका संदेश मिल गया है। मदद भेजी जा रही है, लगभग {asg.eta_min or '?'} मिनट। सबसे ऊंची जगह पर रहें।",
            "hinglish": f"Aapka message mil gaya hai. Madad aa rahi hai, {eta}. Sabse oonchi jagah par rahein, bacchon ko paas rakhein.",
        }.get(ext.language, f"Your message is received. Help is being arranged, {eta}. Move to the highest point and keep everyone together.")
        return {"officer_brief": brief, "citizen_reply": reply}

    payload = {
        "priority": tri.priority.value,
        "people": ext.people_count,
        "vulnerable": ext.vulnerable,
        "water_level": ext.water_level,
        "need": ext.need,
        "village": loc.village,
        "asset": asg.asset_label,
        "eta_min": asg.eta_min,
        "camp": asg.camp,
        "language": ext.language,
    }
    res = await complete_json(
        "commander",
        prompts.COMMANDER.format(language=ext.language),
        json.dumps(payload, ensure_ascii=False),
        fallback,
        tier="fast",
        max_tokens=350,
    )
    try:
        plan = Plan.model_validate(res.data)
    except ValidationError:
        plan = Plan.model_validate(fallback())
    plan.officer_brief = clean_reply(plan.officer_brief, 240)
    plan.citizen_reply = clean_reply(plan.citizen_reply, 320)
    return plan, res


async def public_info(question: str, stats: dict) -> tuple[str, LLMResult]:
    def fallback() -> dict:
        return {
            "answer": (
                f"Exercise update: {stats.get('received', 0)} requests received, "
                f"{stats.get('dispatched', 0)} units dispatched. Individual details are not shared. "
                "For help call 1070."
            )
        }

    res = await complete_json(
        "public_info",
        prompts.PUBLIC_INFO,
        json.dumps({"question": question, "aggregate_facts": stats}, ensure_ascii=False),
        fallback,
        tier="fast",
        max_tokens=200,
    )
    return clean_reply(str(res.data.get("answer", fallback()["answer"])), 400), res
