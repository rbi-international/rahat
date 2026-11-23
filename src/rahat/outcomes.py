"""
Learning outcomes: a short entry check and an exit debrief on the participant phone.

  anonymous id   The phone makes a random id in localStorage. It is the only key. No name,
                 phone number, device id or persona is stored with an answer.
  entry check    Three questions with one right answer each, and one confidence item (1 to 5),
                 shown once when a phone joins. It can be skipped.
  exit debrief   The same four items, three agreement items and an optional comment,
                 shown on every phone when the exercise is terminated.
  snapshot       Answers live in memory. On Terminate, after debrief answers arrive, before
                 Reset room, before a new session and at shutdown, a JSON snapshot is written
                 to a sink: an Azure Blob container on the live app, a folder locally.

Standard library only, so scripts/outcomes_report.py can import it without the app's dependencies.
"""

from __future__ import annotations

import json
import logging
import statistics
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("rahat.outcomes")

SCHEMA = 1
MAX_RECORDS = 5000
QUIZ = ("q1", "q2", "q3")
KEY = {"q1": "b", "q2": "b", "q3": "b"}
EXIT_ITEMS = ("helped", "dashboard", "recommend")

QUESTIONS = {
    "quiz": [
        {"id": "q1", "text": "Why does the escalation agent ask a human before a high-risk dispatch?",
         "options": {"a": "The model is slow",
                     "b": "High-impact actions need human approval because the agent can be wrong or manipulated",
                     "c": "The law requires two agents",
                     "d": "To save tokens"}},
        {"id": "q2", "text": "What does the monitoring dashboard let the control room detect?",
         "options": {"a": "Only the number of users",
                     "b": "Latency, errors, guardrail blocks and cost per agent as they happen",
                     "c": "Students' names",
                     "d": "Nothing during the drill"}},
        {"id": "q3", "text": "Why does every public alert pass an output guardrail?",
         "options": {"a": "To shorten it",
                     "b": "To stop unverified or manipulated messages from reaching the public",
                     "c": "To translate it",
                     "d": "To add the time"}},
    ],
    "confidence": {"id": "c1",
                   "text": "I could design and deploy a multi-agent system with monitoring and guardrails."},
    "exit_items": [
        {"id": "helped", "text": "The live drill helped me understand how multi-agent systems work."},
        {"id": "dashboard", "text": "Seeing the live dashboard made deployment and monitoring concrete."},
        {"id": "recommend", "text": "I would recommend this format for other AI courses."},
    ],
    "scale": "1 = strongly disagree, 5 = strongly agree",
}


def quiz_score(answers: dict) -> int:
    return sum(1 for q in QUIZ if answers.get(q) == KEY[q])


# ------------------------------------------------------------------ sinks

class LocalSink:
    """Writes snapshots under a folder. Used for local runs and tests."""

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)

    def write(self, name: str, data: bytes) -> str:
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)


class BlobSink:
    """Writes snapshots to an Azure Blob container with the app's managed identity (no key).

    container_url: https://<account>.blob.core.windows.net/<container>
    """

    def __init__(self, container_url: str, credential=None, opener=None) -> None:
        self.url = container_url.rstrip("/")
        self._cred = credential
        self._open = opener or urllib.request.urlopen

    def _token(self) -> str:
        if self._cred is None:
            from azure.identity import DefaultAzureCredential  # in the image; optional locally
            self._cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        return self._cred.get_token("https://storage.azure.com/.default").token

    def write(self, name: str, data: bytes) -> str:
        req = urllib.request.Request(f"{self.url}/{name}", data=data, method="PUT", headers={
            "Authorization": f"Bearer {self._token()}",
            "x-ms-version": "2021-08-06",
            "x-ms-blob-type": "BlockBlob",
            "Content-Type": "application/json",
        })
        with self._open(req, timeout=30) as r:
            if r.status not in (200, 201):
                raise OSError(f"blob write returned {r.status}")
        return name


class NoSink:
    def write(self, name: str, data: bytes) -> str:
        raise OSError("no outcomes storage configured (set OUTCOMES_BLOB_URL or OUTCOMES_DIR)")


def make_sink(blob_url: str = "", folder: str = ""):
    if blob_url:
        return BlobSink(blob_url)
    if folder:
        return LocalSink(folder)
    return NoSink()


# ------------------------------------------------------------------ the store

class Outcomes:
    def __init__(self, sink=None) -> None:
        self.sink = sink or NoSink()
        self._lock = threading.Lock()
        self.clear()

    def clear(self, session_id: str | None = None) -> None:
        with self._lock:
            self.records: dict[str, dict] = {}
            self.skipped = {"entry": 0, "exit": 0}
            self.session_id = session_id
            self.started_at = time.time()
            self.last_saved: dict | None = None

    def has_data(self) -> bool:
        return bool(self.records or any(self.skipped.values()))

    def record(self, anon_id: str, phase: str, answers: dict) -> bool:
        """Store one phase for one anonymous id. False if that id already answered this phase."""
        with self._lock:
            rec = self.records.get(anon_id)
            if rec is None:
                if len(self.records) >= MAX_RECORDS:
                    return False
                rec = self.records[anon_id] = {}
            if phase in rec:
                return False
            rec[phase] = {**answers, "at": time.time()}
            return True

    def skip(self, phase: str) -> None:
        with self._lock:
            self.skipped[phase] += 1

    def snapshot(self, reason: str) -> dict:
        with self._lock:
            return {
                "schema": SCHEMA,
                "session_id": self.session_id,
                "session_started_at": self.started_at,
                "written_at": time.time(),
                "reason": reason,
                "skipped": dict(self.skipped),
                "answers": [{"anon_id": k, **v} for k, v in self.records.items()],
            }

    def save(self, reason: str) -> str | None:
        """Write a snapshot. Never raises: a storage failure must not stop the drill."""
        snap = self.snapshot(reason)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(snap["written_at"]))
        name = f"{snap['session_id'] or 'no-session'}/{stamp}_{reason}.json"
        try:
            where = self.sink.write(name, json.dumps(snap, ensure_ascii=False, indent=1).encode())
        except Exception as exc:
            log.error("outcomes snapshot %s not written: %s", name, exc)
            self.last_saved = {"name": name, "ok": False, "error": str(exc)[:200], "at": time.time()}
            return None
        log.info("outcomes snapshot written: %s (%d ids)", name, len(snap["answers"]))
        self.last_saved = {"name": name, "ok": True, "at": time.time()}
        return where

    def summary(self) -> dict:
        out = summarize(self.snapshot("live"))
        out["last_saved"] = self.last_saved
        return out


# ------------------------------------------------------------------ numbers

def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 2) if xs else None


def _agree(xs: list[int]) -> dict:
    return {"pct": round(100 * sum(1 for x in xs if x >= 4) / len(xs), 1) if xs else None, "n": len(xs)}


def summarize(snapshot: dict) -> dict:
    """Pair entry and exit by anonymous id (within a session) and compute the report numbers."""
    rows = snapshot.get("answers", [])
    entry = [r["entry"] for r in rows if "entry" in r]
    exit_ = [r["exit"] for r in rows if "exit" in r]
    paired = [r for r in rows if "entry" in r and "exit" in r]

    q_in = [quiz_score(r["entry"]) for r in paired]
    q_out = [quiz_score(r["exit"]) for r in paired]
    c_in = [r["entry"]["c1"] for r in paired]
    c_out = [r["exit"]["c1"] for r in paired]
    gains = [b - a for a, b in zip(q_in, q_out, strict=True)]

    quiz = {"max": len(QUIZ), "entry_mean": _mean(q_in), "exit_mean": _mean(q_out),
            "mean_gain": _mean(gains), "n": len(paired),
            "entry_mean_all": _mean([quiz_score(e) for e in entry]),
            "exit_mean_all": _mean([quiz_score(e) for e in exit_])}
    if len(gains) >= 2 and statistics.stdev(gains) > 0:
        sd = statistics.stdev(gains)
        quiz["sd_gain"] = round(sd, 2)
        quiz["effect_size_dz"] = round(statistics.fmean(gains) / sd, 2)

    per_question = {q: {"entry_correct_pct": _pct_correct(entry, q), "exit_correct_pct": _pct_correct(exit_, q)}
                    for q in QUIZ}
    agree = {"c1_entry": _agree([e["c1"] for e in entry]), "c1_exit": _agree([e["c1"] for e in exit_])}
    for item in EXIT_ITEMS:
        agree[item] = _agree([e[item] for e in exit_ if item in e])

    return {
        "session_id": snapshot.get("session_id"),
        "n_entry": len(entry),
        "n_exit": len(exit_),
        "n_paired": len(paired),
        "skipped": snapshot.get("skipped", {}),
        "quiz": quiz,
        "per_question": per_question,
        "confidence": {"scale": "1-5", "entry_mean": _mean(c_in), "exit_mean": _mean(c_out), "n": len(paired)},
        "agree_pct": agree,
        "comments": [e["comment"] for e in exit_ if e.get("comment")],
    }


def _pct_correct(answers: list[dict], q: str) -> float | None:
    return round(100 * sum(1 for a in answers if a.get(q) == KEY[q]) / len(answers), 1) if answers else None


def merge(snapshots: list[dict]) -> dict:
    """Combine snapshots into one. Ids are kept apart per run (a session, or the part of it
    between two Reset rooms); for each id and phase the latest answer wins. Skips are taken
    from each run's latest snapshot."""
    answers: dict[tuple, dict] = {}
    latest: dict[tuple, dict] = {}
    for snap in sorted(snapshots, key=lambda s: s.get("written_at", 0)):
        sid = snap.get("session_id")
        run = (sid, snap.get("session_started_at"))
        latest[run] = snap
        for row in snap.get("answers", []):
            key = (run, row["anon_id"])
            cur = answers.setdefault(key, {"anon_id": row["anon_id"], "session_id": sid})
            for phase in ("entry", "exit"):
                if phase in row and row[phase].get("at", 0) >= cur.get(phase, {}).get("at", 0):
                    cur[phase] = row[phase]
    skipped = {"entry": sum(s.get("skipped", {}).get("entry", 0) for s in latest.values()),
               "exit": sum(s.get("skipped", {}).get("exit", 0) for s in latest.values())}
    sessions = sorted({str(sid) for sid, _ in latest})
    return {"schema": SCHEMA, "session_id": ",".join(sessions), "sessions": sessions, "runs": len(latest),
            "skipped": skipped, "answers": list(answers.values())}
