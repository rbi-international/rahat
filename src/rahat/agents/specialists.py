"""
The four model-backed specialists. Each one:
  - builds its prompt from typed inputs,
  - calls the gateway with a rules fallback (so it can never return nothing),
  - validates the reply into a typed model before anyone downstream sees it.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from .. import rules
from ..guardrails.policy import bound_priority, clean_reply
from ..llm import LLMResult, complete_json
from ..models import Assignment, Extraction, Location, Plan, Priority, Triage
from . import prompts

_VULN_OK = {"infant", "child", "elderly", "pregnant", "disabled", "injured"}
_LANG_OK = {"pa", "hi", "hinglish", "en"}


def _one_need(value):
    """Models sometimes return need as a list. The most serious single need wins."""
    if not isinstance(value, list):
        return value
    items = [str(v).strip().lower() for v in value if str(v).strip()]
    if not items:
        return "rescue"
    for wanted in ("medical", "rescue"):
        if wanted in items:
            return wanted
    return items[0]


def _language_code(value):
    """Map labels like "Punjabi (romanized)" or "Hindi-English mix" to pa, hi, hinglish or en."""
    s = str(value).strip().lower()
    if s in _LANG_OK:
        return s
    if "hinglish" in s or ("hindi" in s and "english" in s):
        return "hinglish"
    if "punjabi" in s or "panjabi" in s or "gurmukhi" in s:
        return "pa"
    if "hindi" in s:
        return "hi"
    if "english" in s:
        return "en"
    return value


def _normalise_intake(data: dict) -> dict:
    data = dict(data)
    if "need" in data:
        data["need"] = _one_need(data["need"])
    if "language" in data:
        data["language"] = _language_code(data["language"])
    data["vulnerable"] = [v for v in data.get("vulnerable", []) if v in _VULN_OK]
    return data


async def intake(clean_text: str) -> tuple[Extraction, LLMResult]:
    def fallback() -> dict:
        return rules.extract(clean_text).model_dump()

    res = await complete_json(
        "intake", prompts.INTAKE, f"MESSAGE:\n{clean_text}", fallback, tier="fast", max_tokens=400
    )
    try:
        ext = Extraction.model_validate(_normalise_intake(res.data))
    except ValidationError:
        ext = rules.extract(clean_text)
        res.degraded = True
    # Rules catch what a model sometimes drops (the place name, a roof, a child).
    hint = rules.extract(clean_text)
    if not ext.location_text:
        ext.location_text = clean_text
    if ext.water_level == "unknown" and hint.water_level != "unknown":
        ext.water_level = hint.water_level
    # A vulnerable person the rules can read but the model dropped is added back. Only ever adds.
    ext.vulnerable += [v for v in hint.vulnerable if v not in ext.vulnerable]
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
    moved_for = str(res.data.get("moved_for", "") or "").strip()[:120]
    reason = str(res.data.get("reason", ""))[:200] or f"SOP score {score:.0f}"
    if proposed != sop and not moved_for:
        # A move must name the uncounted fact. Without one, the SOP rubric stands.
        reason = f"SOP score {score:.0f} kept (move to {proposed.value} named no uncounted fact)"
        proposed = sop
    final = bound_priority(proposed, sop)
    if final != sop and moved_for:
        reason = f"{reason} · moved for: {moved_for}"[:260]
    if final != proposed:
        reason += f" (clamped from {proposed.value}: max one level from SOP)"
    return Triage(priority=final, score=score, reason=reason, model_tier=res.tier, river_level=river_level), res


ASK_LOCATION = {
    "pa": "ਤੁਹਾਡਾ ਸੁਨੇਹਾ ਮਿਲ ਗਿਆ ਹੈ, ਪਰ ਤੁਹਾਡੀ ਥਾਂ ਨਹੀਂ ਪਤਾ ਲੱਗੀ। ਨੇੜੇ ਦੇ ਪਿੰਡ, ਗੁਰਦੁਆਰੇ, ਸਕੂਲ ਜਾਂ ਕਿਸੇ ਨਿਸ਼ਾਨੀ ਦਾ ਨਾਮ ਲਿਖ ਕੇ "
          "ਦੁਬਾਰਾ ਭੇਜੋ। ਉੱਚੀ ਥਾਂ 'ਤੇ ਰਹੋ।",
    "hi": "आपका संदेश मिल गया है, पर आपकी जगह पता नहीं चली। पास के गांव, गुरुद्वारे, स्कूल या किसी निशानी का नाम लिखकर "
          "दोबारा भेजें। ऊंची जगह पर रहें।",
    "hinglish": "Aapka message mil gaya, par aapki jagah pata nahi chali. Paas ke gaon, gurdwara, school ya kisi landmark "
                "ka naam likh kar dobara bhejein. Oonchi jagah par rahein.",
    "en": "Your message is received, but we could not find your location. Send it again with the nearest village, "
          "gurdwara, school or landmark. Stay on high ground.",
}
# Words that show a reply really asks for a place, in any of the four languages.
_ASKS_PLACE = re.compile(r"village|gurdwara|gurudwara|school|landmark|gaon|gaaon|pind|ਪਿੰਡ|ਗੁਰਦੁਆਰ|ਸਕੂਲ|गांव|गाँव|"
                         r"गुरुद्वार|स्कूल", re.I)


def ask_location_reply(language: str) -> str:
    return ASK_LOCATION.get(language, ASK_LOCATION["en"])


async def commander(
    ext: Extraction, loc: Location, tri: Triage, asg: Assignment
) -> tuple[Plan, LLMResult]:
    eta = f"about {asg.eta_min} minutes" if asg.eta_min else "as soon as a unit is free"

    def fallback() -> dict:
        if not loc.resolved:
            return {
                "officer_brief": f"{tri.priority.value}: {ext.people_count} people, water {ext.water_level}, "
                                 "LOCATION UNKNOWN. Citizen asked to resend with a landmark; call back if urgent.",
                "citizen_reply": ask_location_reply(ext.language),
            }
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
        "location_known": loc.resolved,
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
    if not loc.resolved and not _ASKS_PLACE.search(plan.citizen_reply):
        # The model forgot to ask where they are. Asking back is exact work: code guarantees it.
        plan.citizen_reply = ask_location_reply(ext.language)
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
