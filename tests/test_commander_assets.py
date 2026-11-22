"""The commander may only word the allocator's decision: no invented asset, no invented ETA, in any language."""

import asyncio

import pytest

from rahat.agents import specialists
from rahat.llm import LLMResult
from rahat.models import Assignment, Extraction, Location, Priority, Triage

HERE = Location(resolved=True, village="Kalanaur", district="Gurdaspur")
TRI = Triage(priority=Priority.P1, score=80)
QUEUED = Assignment(note="nearest free unit too far, queued for next free nearby")
BOAT = Assignment(asset_id="B-03", asset_kind="boat", asset_label="Boat B-03", distance_km=4.1, eta_min=12)


def _plan(model_reply, asg, language="en", loc=HERE, monkeypatch=None):
    async def fake(agent, system, user, fallback, **kw):
        return LLMResult(data=model_reply, tokens=10, lane="test", model="m", tier="fast", degraded=False, ms=1)

    monkeypatch.setattr(specialists, "complete_json", fake)
    ext = Extraction(language=language, people_count=7, water_level="roof", vulnerable=["pregnant"])
    plan, _ = asyncio.run(specialists.commander(ext, loc, TRI, asg))
    return plan


def test_invented_helicopter_and_eta_range_are_replaced_when_nothing_is_assigned(monkeypatch):
    plan = _plan({"officer_brief": "P1: 7 people (1 pregnant) trapped; water at roof level. Asset: helicopter "
                                   "assigned. ETA approx 30-60 min. High hazard.",
                  "citizen_reply": "Help is coming by helicopter in 30-60 minutes. Stay on the roof."}, QUEUED,
                 monkeypatch=monkeypatch)
    assert "helicopter" not in plan.officer_brief.lower() and "60" not in plan.officer_brief
    assert "No free unit nearby, queued" in plan.officer_brief
    assert plan.citizen_reply == specialists.QUEUED_REPLY["en"]


@pytest.mark.parametrize("language,reply", [
    ("pa", "ਮਦਦ ਆ ਰਹੀ ਹੈ, ਕਿਸ਼ਤੀ ਲਗਭਗ 20 ਮਿੰਟ ਵਿੱਚ ਪਹੁੰਚੇਗੀ।"),
    ("hi", "मदद आ रही है, नाव लगभग 20 मिनट में पहुंचेगी।"),
    ("hinglish", "Madad aa rahi hai, boat 20 min mein pahunchegi."),
    ("en", "A rescue boat will reach you in about 1-2 hours."),
])
def test_queued_citizen_reply_promises_no_asset_or_time_in_any_language(language, reply, monkeypatch):
    plan = _plan({"officer_brief": "P1: 7 people, roof. No free unit nearby, queued for next free unit.",
                  "citizen_reply": reply}, QUEUED, language=language, monkeypatch=monkeypatch)
    assert plan.citizen_reply == specialists.QUEUED_REPLY[language]
    assert plan.officer_brief.startswith("P1: 7 people, roof. No free unit nearby")  # an honest brief is kept


def test_a_brief_that_matches_the_assignment_is_kept(monkeypatch):
    brief = "P1: 7 people, roof, Kalanaur. Boat B-03, ETA 12 min. Approve."
    reply = "Your message is received. A boat is coming, about 12 minutes. Stay on the highest point."
    plan = _plan({"officer_brief": brief, "citizen_reply": reply}, BOAT, monkeypatch=monkeypatch)
    assert plan.officer_brief == brief and plan.citizen_reply == reply


@pytest.mark.parametrize("brief", [
    "P1: 7 people. Asset: helicopter assigned. ETA 12 min.",       # wrong kind
    "P1: 7 people. Boat B-07 assigned, ETA 12 min.",                # wrong unit
    "P1: 7 people. Boat B-03, ETA approx 30-45 min.",               # invented range
    "P1: 7 people. Boat B-03, ETA ~8-12 mins.",                     # range around the real ETA
    "P1: 7 people. Boat B-03, arrives in 1-3 hours.",
])
def test_a_brief_that_departs_from_the_assignment_is_rebuilt_from_code(brief, monkeypatch):
    plan = _plan({"officer_brief": brief, "citizen_reply": "Help is coming, about 12 minutes."}, BOAT,
                 monkeypatch=monkeypatch)
    assert plan.officer_brief == "P1: 7 people, water roof, Kalanaur. Boat B-03, ETA 12 min."


def test_priority_and_counts_are_not_read_as_times():
    asg = BOAT
    assert not specialists.names_unassigned("P1 8 people, Boat B-03, ETA 12 min, helpline 1070", asg)
    assert not specialists.names_unassigned("P1: 2 people, water roof. No free unit nearby, queued.", QUEUED)


def test_rules_fallback_never_shows_a_question_mark_eta(monkeypatch):
    async def fake(agent, system, user, fallback, **kw):
        return LLMResult(data=fallback(), tokens=0, lane="rules", model="rules", tier="fast", degraded=True, ms=0)

    monkeypatch.setattr(specialists, "complete_json", fake)
    for lang in ("pa", "hi", "hinglish", "en"):
        ext = Extraction(language=lang, people_count=3, water_level="chest")
        plan, _ = asyncio.run(specialists.commander(ext, HERE, TRI, QUEUED))
        assert "?" not in plan.officer_brief and "?" not in plan.citizen_reply
        assert plan.citizen_reply == specialists.QUEUED_REPLY[lang]
