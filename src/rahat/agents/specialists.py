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

from .. import metrics, rules
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


QUEUED_REPLY = {
    "pa": "ਤੁਹਾਡਾ ਸੁਨੇਹਾ ਮਿਲ ਗਿਆ ਹੈ। ਨੇੜੇ ਕੋਈ ਯੂਨਿਟ ਖਾਲੀ ਨਹੀਂ, ਤੁਸੀਂ ਅਗਲੀ ਖਾਲੀ ਯੂਨਿਟ ਦੀ ਕਤਾਰ ਵਿੱਚ ਹੋ। ਸਭ ਤੋਂ ਉੱਚੀ ਥਾਂ 'ਤੇ ਰਹੋ, "
          "ਸਾਰੇ ਇਕੱਠੇ ਰਹੋ।",
    "hi": "आपका संदेश मिल गया है। पास में कोई यूनिट खाली नहीं है, आप अगली खाली यूनिट की कतार में हैं। सबसे ऊंची जगह पर रहें, "
          "सब साथ रहें।",
    "hinglish": "Aapka message mil gaya hai. Paas mein koi unit khaali nahi hai, aap agli khaali unit ki line mein hain. "
                "Sabse oonchi jagah par rahein, sab saath rahein.",
    "en": "Your message is received. No rescue unit is free nearby yet; you are queued for the next free one. "
          "Move to the highest point and keep everyone together.",
}

# Asset kinds as the model may write them, in English, Hinglish, Hindi and Punjabi.
_ASSET_KINDS = {
    "boat": r"boats?|kisht[iy]\w*|naav|nauka|नाव|नौका|ਕਿਸ਼ਤੀ|ਬੇੜੀ|\bB-\d+",
    "medical": r"medical unit|ambulance|medic\b|एम्बुलेंस|ਐਂਬੂਲੈਂਸ|\bM-\d+",
    "ndrf": r"ndrf|sdrf|एनडीआरएफ|ਐਨਡੀਆਰਐਫ",
    "air": r"helicopters?|heli\b|chopper|air ?lift|airborne|हेलीकॉप्टर|ਹੈਲੀਕਾਪਟਰ",
}
_ASSET_RE = {k: re.compile(p, re.I) for k, p in _ASSET_KINDS.items()}
_ASSET_ID = re.compile(r"\b([BM])-(\d+)\b")
# A time the text promises: "ETA 30", "30-60 min", "1-3 hours", "20 मिनट", "ਲਗਭਗ 15 ਮਿੰਟ".
_TIME_RE = re.compile(
    r"(?:eta\s*[:~]?\s*(?:approx\.?\s*|about\s*|~\s*)?(\d+)(?:\s*[-–]\s*(\d+))?)"
    r"|(?:(\d+)(?:\s*(?:[-–]|to)\s*(\d+))?\s*(min\w*|mins?\b|minute\w*|hours?|hrs?\b|ghante|ghanta|मिनट|घंटे|घंटा|ਮਿੰਟ|ਘੰਟੇ|ਘੰਟਾ))",
    re.I,
)


def _times(text: str) -> list[int]:
    """Every promised time in the text, in minutes."""
    out = []
    for m in _TIME_RE.finditer(text):
        unit = (m.group(5) or "min").lower()
        scale = 60 if unit.startswith(("hour", "hr", "ghant", "घंट", "ਘੰਟ")) else 1
        for g in (m.group(1), m.group(2), m.group(3), m.group(4)):
            if g:
                out.append(int(g) * scale)
    return out


def names_unassigned(text: str, asg: Assignment) -> bool:
    """True if the text names an asset or a time the allocator did not assign."""
    kinds = {k for k, r in _ASSET_RE.items() if r.search(text)}
    if not asg.asset_id:
        return bool(kinds) or bool(_times(text))
    if kinds - {asg.asset_kind}:
        return True
    mine = _ASSET_ID.search(asg.asset_label or "")
    for m in _ASSET_ID.finditer(text):
        if not mine or m.group(0) != mine.group(0):
            return True
    return any(t != asg.eta_min for t in _times(text))


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
        head = f"{tri.priority.value}: {ext.people_count} people, water {ext.water_level}, {loc.village or 'location unclear'}."
        if not asg.asset_id:
            return {"officer_brief": f"{head} No free unit nearby, queued for the next free unit.",
                    "citizen_reply": QUEUED_REPLY.get(ext.language, QUEUED_REPLY["en"])}
        reply = {
            "pa": f"ਤੁਹਾਡਾ ਸੁਨੇਹਾ ਮਿਲ ਗਿਆ ਹੈ। ਮਦਦ ਭੇਜੀ ਜਾ ਰਹੀ ਹੈ, ਲਗਭਗ {asg.eta_min} ਮਿੰਟ। ਸਭ ਤੋਂ ਉੱਚੀ ਥਾਂ 'ਤੇ ਰਹੋ।",
            "hi": f"आपका संदेश मिल गया है। मदद भेजी जा रही है, लगभग {asg.eta_min} मिनट। सबसे ऊंची जगह पर रहें।",
            "hinglish": f"Aapka message mil gaya hai. Madad aa rahi hai, {eta}. Sabse oonchi jagah par rahein, bacchon ko paas rakhein.",
        }.get(ext.language, f"Your message is received. Help is being arranged, {eta}. Move to the highest point and keep everyone together.")
        return {"officer_brief": f"{head} {asg.asset_label}, ETA {asg.eta_min} min.", "citizen_reply": reply}

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
    # Assets and ETAs are exact work: the allocator decides them, the model only words them.
    # Any asset or time the allocator did not assign is replaced with the code-built text.
    exact = fallback()
    if names_unassigned(plan.officer_brief, asg):
        plan.officer_brief = exact["officer_brief"]
        metrics.GUARD_BLOCKS.labels("commander_unassigned").inc()
    if names_unassigned(plan.citizen_reply, asg):
        plan.citizen_reply = exact["citizen_reply"]
        metrics.GUARD_BLOCKS.labels("commander_unassigned").inc()
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
