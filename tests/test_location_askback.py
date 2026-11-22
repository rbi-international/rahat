"""No place in the SOS: RAHAT asks back, the resend is not a duplicate, and scoring reports it apart."""

import asyncio
import difflib
import random
import time

from fastapi.testclient import TestClient

from rahat import rules
from rahat.agents import specialists
from rahat.api import app
from rahat.drill import scoring
from rahat.drill.control import SW_NEED, second_wave_text
from rahat.drill.personas import make
from rahat.llm import LLMResult
from rahat.models import SOS, Assignment, Extraction, Incident, Location, Priority, Triage
from rahat.tools import geo

NOWHERE = Location(resolved=False)
TRI = Triage(priority=Priority.P2, score=50)


def _commander(language: str, model_reply: dict | None, monkeypatch):
    async def fake(agent, system, user, fallback, **kw):
        data = model_reply if model_reply is not None else fallback()
        return LLMResult(data=data, tokens=10, lane="test", model="m", tier="fast", degraded=False, ms=1)

    monkeypatch.setattr(specialists, "complete_json", fake)
    ext = Extraction(language=language, people_count=3, water_level="chest")
    plan, _ = asyncio.run(specialists.commander(ext, NOWHERE, TRI, Assignment(note="location unresolved")))
    return plan


def test_fallback_asks_for_a_landmark_in_all_four_languages(monkeypatch):
    for lang in ("pa", "hi", "hinglish", "en"):
        plan = _commander(lang, None, monkeypatch)
        assert plan.citizen_reply == specialists.ask_location_reply(lang)
        assert "LOCATION UNKNOWN" in plan.officer_brief
    assert "landmark" in specialists.ask_location_reply("en")
    assert "ਪਿੰਡ" in specialists.ask_location_reply("pa") and "गांव" in specialists.ask_location_reply("hi")


def test_a_model_reply_that_forgets_to_ask_is_replaced(monkeypatch):
    plan = _commander("en", {"officer_brief": "P2, 3 people", "citizen_reply": "Help is on the way, 20 minutes."},
                      monkeypatch)
    assert plan.citizen_reply == specialists.ask_location_reply("en")
    kept = "We could not find you. Send again with the nearest gurdwara or school."
    plan = _commander("en", {"officer_brief": "P2", "citizen_reply": kept}, monkeypatch)
    assert plan.citizen_reply == kept


def _settle(client, n, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        items = client.get("/api/incidents").json()
        if len(items) >= n and all(i["status"] not in ("queued", "processing") for i in items):
            return items
        time.sleep(0.2)
    raise AssertionError("pipeline did not settle")


def test_resend_with_a_place_after_ask_back_is_not_a_duplicate():
    first = "gale tak paani aa gaya, 3 log hain, bebe bimaar naal, jaldi boat bhejo"
    second = first + ", Ajnala"
    assert difflib.SequenceMatcher(None, first, second).ratio() > 0.9  # would look like a duplicate

    with TestClient(app) as c:
        assert c.post("/api/login", json={"secret": "test-admin"}).status_code == 200
        c.post("/api/admin/reset", json={})
        s = c.post("/api/admin/session", json={"minutes": 30}).json()
        c.post("/api/admin/inject/start")
        phone = TestClient(app)
        phone.get(f"/j/{s['code']}", follow_redirects=False)

        assert phone.post("/api/sos", json={"text": first}).status_code == 200
        asked = _settle(c, 1)[0]
        assert "asked_location" in asked["flags"]
        assert asked["plan"]["citizen_reply"]  # the reply asks for a village or landmark

        assert phone.post("/api/sos", json={"text": second}).status_code == 200
        items = _settle(c, 2)
        resend = next(i for i in items if i["sos"]["text"].endswith("Ajnala"))
        assert resend["location"]["village"] == "Ajnala"
        assert "duplicate" not in resend["flags"]
        assert resend["verification"]["duplicate_of"] is None
        assert "location follow-up" in resend["verification"]["flags"]
        c.post("/api/admin/reset", json={})


def test_scoring_reports_asked_for_location_apart_from_misses():
    personas = {}
    idx = 0
    while len(personas) < 3:
        p = make(idx, 2026)
        if p.role == "citizen":
            personas[p.id] = p
        idx += 1
    a, b, c = personas.values()

    def inc(p, loc, flags):
        i = Incident(sos=SOS(text="help paani aa gaya", device_id="d", persona_id=p.id, channel="web"))
        i.location, i.flags = loc, flags
        i.triage = Triage(priority=Priority.P3, score=30)
        return i

    found = Location(resolved=True, village=a.truth["village"])
    wrong = Location(resolved=True, village="Nowhere")
    result = scoring.score([inc(a, found, []), inc(b, NOWHERE, ["asked_location"]), inc(c, wrong, [])],
                           personas, {"used": 0})["citizen"]
    assert result["asked_location"] == 1
    assert result["located"] == 1
    assert result["located_pct"] == 50.0  # 1 of the 2 where a place could be read; the wrong village is a miss
    assert result["asked_location_pct"] == 33.3


def test_second_wave_messages_carry_every_ground_truth_fact():
    checked = 0
    for idx in range(10_000, 10_200):
        p = make(idx, 2026)
        if p.role != "citizen":
            continue
        t = p.truth
        text = second_wave_text(t, random.Random(idx))
        e = rules.extract(text)
        assert e.water_level == t["water_level"], (text, t)
        assert set(t["vulnerable"]) <= set(e.vulnerable), (text, t)
        assert any(w in text for w in SW_NEED[t["need"]]), (text, t)
        assert geo.locate(text).village == t["village"], (text, t)
        checked += 1
    assert checked > 100
