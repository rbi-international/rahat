"""
The SOP rulebook: deterministic reading and scoring of a distress message.

Three jobs, one file:
  1. The triage rubric (the district's standard operating procedure as code).
     Both the drill's ground truth and the Triage agent's guard rails use it.
  2. Rules-only extraction, used when the token allocation runs out or every
     model lane is down. Worse than a model, never silent, never broke.
  3. The behaviour of the offline fake model, so CI and first runs need no key.
"""

from __future__ import annotations

import re

from .models import Extraction, Priority

# --------------------------------------------------------------- the rubric

WATER_POINTS = {"roof": 40, "chest": 32, "waist": 20, "knee": 10, "ankle": 4, "unknown": 12}
VULN_POINTS = {
    "infant": 18,
    "pregnant": 18,
    "injured": 16,
    "disabled": 14,
    "elderly": 12,
    "child": 10,
}
P1_AT, P2_AT, P3_AT = 65, 45, 20


def rubric_score(
    water_level: str,
    vulnerable: list[str],
    people: int,
    need: str,
    river_danger: bool = False,
) -> float:
    s = WATER_POINTS.get(water_level, 12)
    s += min(35, sum(VULN_POINTS.get(v, 0) for v in set(vulnerable)))
    s += min(15, 2 * max(0, people))
    if need == "medical":
        s += 10
    if river_danger:
        s += 10
    if need == "information" and water_level in ("unknown", "ankle", "knee"):
        s = min(s, P3_AT - 1)
    return float(min(100, s))


def priority_for(score: float) -> Priority:
    if score >= P1_AT:
        return Priority.P1
    if score >= P2_AT:
        return Priority.P2
    if score >= P3_AT:
        return Priority.P3
    return Priority.P4


RUBRIC_TEXT = (
    "Score = water (roof 40, chest 32, waist 20, knee 10, ankle 4, unknown 12) "
    "+ vulnerable people (infant 18, pregnant 18, injured 16, disabled 14, elderly 12, child 10; max 35) "
    "+ 2 per person (max 15) + 10 if medical need + 10 if the nearest river gauge is at danger. "
    f"P1 >= {P1_AT}, P2 >= {P2_AT}, P3 >= {P3_AT}, else P4."
)

# --------------------------------------------------------------- extraction

_WATER = [
    # chhatt?(?!i): "chhat" is the roof, "chhati" is the chest
    ("roof", r"roof|chhatt?(?!i)|chat pe|chat te|terrace|upar chadh|ਛੱਤ|छत"),
    ("chest", r"chest|chaati|chhaati|chhati|chati|gale tak|neck|ਛਾਤੀ|ਗਲ|छाती|गले"),
    ("waist", r"waist|kamar|lakk|ਲੱਕ|ਕਮਰ|कमर"),
    ("knee", r"knee|goda|gode|gutne|ghutne|ਗੋਡੇ|घुटन"),
    ("ankle", r"ankle|gitte|ankle deep|ਗਿੱਟੇ|टखन"),
]
_VULN = [
    ("infant", r"infant|baby|newborn|navjaat|nanha|ਨਵਜੰਮ|ਬੇਬੀ|नवजात|शिशु"),
    ("pregnant", r"pregnan|garbh|delivery|labou?r pain|ਗਰਭ|गर्भ"),
    ("injured", r"injur|bleed|khoon|fractur|chot|zakhm|wound|snake|saanp|sap ne|tut gayi|toot gayi|haddi|\bbaah\b|\blatt\b"
                r"|ਸੱਟ|ਖੂਨ|ਸੱਪ|चोट|खून|सांप"),
    ("disabled", r"wheelchair|disab|apahij|can.?t walk|chal nahi|chal nhi|paralys|ਤੁਰ ਨਹੀਂ|चल नहीं"),
    ("elderly", r"elder|old (man|woman|lady)|buzurg|bebe|bapu|dadi|dada|nani|nana|grand(mother|father|ma|pa)|ਬਜ਼ੁਰਗ|ਬੇਬੇ|बुजुर्ग|दादी"),
    ("child", r"child|kid|bacch|bachch|bache|munda|kudi|ਬੱਚ|बच्च"),
]
_MEDICAL = r"doctor|dr\.|hospital|ambulance|bleed|unconscious|behosh|heart|dialysis|insulin|snake|saanp|fever|bukhar|labou?r|delivery|medicine|dawai|ਦਵਾਈ|ਬੇਹੋਸ਼|दवा|बेहोश"
_FOOD = r"food|khana|roti|ration|bhookh|bhukh|drinking water|peene|ਰੋਟੀ|ਖਾਣਾ|खाना"
_INFO = r"where is|kithe hai|kahan hai|kidhar|which camp|helpline|number of|ਕਿੱਥੇ|कहाँ|कहां"

_NUMWORDS = {
    "ek": 1, "ikk": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "tin": 3, "three": 3,
    "char": 4, "chaar": 4, "four": 4, "panj": 5, "paanch": 5, "five": 5, "chhe": 6,
    "six": 6, "saat": 7, "sat": 7, "seven": 7, "aath": 8, "eight": 8, "nau": 9, "nine": 9,
    "das": 10, "ten": 10,
}
_PEOPLE_WORDS = r"(people|log|lok|bande|jane|janey|member|members|family|parivar|bacche|bachche|bache|persons|jan)"


def detect_language(text: str) -> str:
    if re.search(r"[਀-੿]", text):
        return "pa"
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    if re.search(r"\b(hai|haan|nahi|paani|pani|bachao|madad|ji|sanu|asi|ae|ne|kol|ghar)\b", text, re.I):
        return "hinglish"
    return "en"


def count_people(text: str) -> int:
    low = text.lower()
    total = 0
    for m in re.finditer(r"(\d{1,3})\s*" + _PEOPLE_WORDS, low):
        total += int(m.group(1))
    if total == 0:
        for w, n in _NUMWORDS.items():
            if re.search(rf"\b{w}\s+{_PEOPLE_WORDS}", low):
                total += n
    if total == 0:
        m = re.search(r"\b(\d{1,2})\b", low)
        total = int(m.group(1)) if m and 0 < int(m.group(1)) <= 40 else 1
    return max(1, min(total, 200))


def extract(text: str, location_text: str = "") -> Extraction:
    low = text.lower()
    water = next((lvl for lvl, pat in _WATER if re.search(pat, low)), "unknown")
    vulnerable = [v for v, pat in _VULN if re.search(pat, low)]
    if "infant" in vulnerable and "child" not in vulnerable:
        vulnerable.append("child")
    if re.search(_MEDICAL, low):
        need = "medical"
    elif re.search(_INFO, low) and water == "unknown":
        need = "information"
    elif re.search(_FOOD, low) and water in ("unknown", "ankle", "knee"):
        need = "food_water"
    else:
        need = "rescue"
    return Extraction(
        language=detect_language(text),
        summary_en=text[:160],
        people_count=count_people(text),
        vulnerable=vulnerable,
        water_level=water,
        location_text=location_text or text,
        need=need,
        medical_detail="",
    )
