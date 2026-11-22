"""A real model's loose JSON is normalised before validation, so it is not thrown away for rules."""

import asyncio

from rahat.agents import specialists
from rahat.llm import LLMResult


def test_need_list_collapses_to_the_most_serious_need():
    one = specialists._one_need
    assert one(["rescue", "medical"]) == "medical"
    assert one(["food_water", "rescue"]) == "rescue"
    assert one(["information", "other"]) == "information"
    assert one([]) == "rescue"
    assert one("food_water") == "food_water"


def test_language_labels_map_to_codes():
    code = specialists._language_code
    assert code("Punjabi (romanized)") == "pa"
    assert code("Hindi") == "hi"
    assert code("Hindi-English mix") == "hinglish"
    assert code("English") == "en"
    assert code("hinglish") == "hinglish"


def test_intake_keeps_a_model_reply_with_a_need_list(monkeypatch):
    reply = {"language": "Punjabi (romanized)", "summary_en": "Four people on a roof, mother injured.",
             "people_count": 4, "vulnerable": ["elderly", "injured", "rich"], "water_level": "roof",
             "location_text": "Batala", "need": ["rescue", "medical"], "medical_detail": "broken arm"}

    async def fake_complete_json(agent, system, user, fallback, **kw):
        return LLMResult(data=reply, tokens=100, lane="test", model="m", tier="fast", degraded=False, ms=1)

    monkeypatch.setattr(specialists, "complete_json", fake_complete_json)
    ext, res = asyncio.run(specialists.intake("bebe di baah tut gayi, chhat te 4 jane, Batala"))
    assert not res.degraded  # the model's answer was used, not the rules fallback
    assert ext.need == "medical"
    assert ext.language == "pa"
    assert ext.vulnerable == ["elderly", "injured"]


def test_intake_adds_back_a_vulnerable_person_the_model_dropped(monkeypatch):
    reply = {"language": "pa", "summary_en": "Three people, chest-deep water, need a doctor.", "people_count": 3,
             "vulnerable": [], "water_level": "chest", "location_text": "Ramdas", "need": "medical"}

    async def fake_complete_json(agent, system, user, fallback, **kw):
        return LLMResult(data=reply, tokens=100, lane="test", model="m", tier="fast", degraded=False, ms=1)

    monkeypatch.setattr(specialists, "complete_json", fake_complete_json)
    ext, _ = asyncio.run(specialists.intake("doctor chahida, 3 log, baah tut gayi, gale tak paani, Ramdas"))
    assert "injured" in ext.vulnerable
