from rahat import rules
from rahat.models import Location, Priority
from rahat.tools import geo
from rahat.tools.resources import Registry, eta_minutes


def test_rubric_ranks_a_rooftop_infant_above_a_flooded_street():
    roof = rules.rubric_score("roof", ["infant", "elderly"], 4, "rescue")
    street = rules.rubric_score("knee", [], 2, "rescue")
    assert rules.priority_for(roof) == Priority.P1
    assert rules.priority_for(street) in (Priority.P3, Priority.P4)


def test_information_requests_never_become_urgent_by_headcount():
    s = rules.rubric_score("ankle", [], 8, "information")
    assert rules.priority_for(s) == Priority.P4


def test_extraction_reads_hinglish():
    e = rules.extract("Paani chhat tak aa gaya, 3 bacche te bebe, Ajnala")
    assert e.water_level == "roof"
    assert "child" in e.vulnerable and "elderly" in e.vulnerable
    assert e.language == "hinglish"


def test_rules_read_punjabi_injury_and_elder_words():
    e = rules.extract("bebe di baah tut gayi, chhat te aa gaye, Batala")
    assert "elderly" in e.vulnerable and "injured" in e.vulnerable
    assert "injured" in rules.extract("latt toot gayi, haddi dikh rahi").vulnerable
    e = rules.extract("ek di latt te chot, wheelchair wale chacha, Fazilka")
    assert "injured" in e.vulnerable and "disabled" in e.vulnerable
    assert "injured" in rules.extract("chot lagi hai").vulnerable
    assert "injured" in rules.extract("ek banda zakhmi hai").vulnerable
    # vocabulary, not substrings: "baahar" (outside) is not an injury
    assert "injured" not in rules.extract("sab baahar aa gaye, Ajnala").vulnerable


def test_geo_resolves_aliases_in_three_scripts():
    assert geo.locate("near gurudwara, dera baba nanak").village == "Dera Baba Nanak"
    assert geo.locate("ਪਾਣੀ ਬਟਾਲਾ ਵਿੱਚ").village == "Batala"
    assert geo.locate("अजनाला में पानी").village == "Ajnala"
    assert geo.locate("sultanpur lodi road").village == "Sultanpur Lodhi"  # fuzzy
    assert not geo.locate("somewhere near the river").resolved


def test_allocator_holds_nearest_boat_and_never_double_books():
    reg = Registry.load()
    loc = geo.locate("Dera Baba Nanak")
    a1 = reg.hold_nearest("S1", loc, "rescue", Priority.P2, 3)
    a2 = reg.hold_nearest("S2", loc, "rescue", Priority.P2, 3)
    assert a1.asset_id and a2.asset_id and a1.asset_id != a2.asset_id
    assert a1.distance_km <= a2.distance_km


def test_allocator_never_holds_a_unit_hours_away():
    reg = Registry.load()
    loc = geo.locate("Dera Baba Nanak")
    # Every boat and NDRF team that could reach Dera Baba Nanak in time is already out.
    for a in reg.assets.values():
        if a.kind in ("boat", "ndrf"):
            d = geo.haversine_km(loc.lat, loc.lon, a.lat, a.lon)
            if eta_minutes(d, a.speed_kmh) <= reg.max_eta_min:
                a.status = "held"
    far = [a for a in reg.assets.values() if a.kind in ("boat", "ndrf") and a.status == "available"]
    assert far, "the test needs free units that are too far away"

    asg = reg.hold_nearest("S4", loc, "rescue", Priority.P1, 4)
    assert asg.asset_id is None and asg.eta_min is None
    assert asg.note == "nearest free unit too far, queued for next free nearby"
    assert all(a.status == "available" for a in far)  # nothing far was held


def test_unresolved_location_gets_no_unit():
    reg = Registry.load()
    a = reg.hold_nearest("S3", Location(resolved=False), "rescue", Priority.P1, 2)
    assert a.asset_id is None
