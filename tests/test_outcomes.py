"""Learning outcomes: storage, pairing, the admin export, admin-only access and the snapshots
written on Terminate and on Reset room. All offline on the fake lane."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rahat import api
from rahat.api import app
from rahat.outcomes import BlobSink, LocalSink, Outcomes, merge, quiz_score, summarize

RIGHT = {"q1": "b", "q2": "b", "q3": "b"}
WRONG = {"q1": "a", "q2": "c", "q3": "b"}  # one right


def entry(quiz, c1):
    return {**quiz, "c1": c1}


def exit_(quiz, c1, helped=5, dashboard=4, recommend=3, comment=""):
    return {**quiz, "c1": c1, "helped": helped, "dashboard": dashboard, "recommend": recommend,
            "comment": comment}


@pytest.fixture
def sink(tmp_path, monkeypatch):
    s = LocalSink(tmp_path)
    monkeypatch.setattr(api.outcomes, "sink", s)
    return tmp_path


def snapshots(folder: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.rglob("*.json"))]


def admin_client():
    c = TestClient(app)
    c.cookies.set("rahat_role", "admin:test-admin")
    return c


def join(c, code):
    phone = TestClient(app)
    assert phone.get(f"/j/{code}", follow_redirects=False).status_code == 303
    return phone


# ------------------------------------------------------------------ storage

def test_store_keys_by_anonymous_id_and_keeps_one_answer_per_phase(tmp_path):
    o = Outcomes(LocalSink(tmp_path))
    o.clear(session_id="JAL-TEST")
    assert o.record("anon-0001", "entry", entry(WRONG, 2))
    assert not o.record("anon-0001", "entry", entry(RIGHT, 5))  # second entry ignored
    assert o.record("anon-0001", "exit", exit_(RIGHT, 4))
    o.skip("entry")

    where = o.save("terminate")
    snap = json.loads(Path(where).read_text(encoding="utf-8"))
    assert snap["session_id"] == "JAL-TEST" and snap["reason"] == "terminate"
    assert snap["written_at"] >= snap["session_started_at"]
    assert snap["skipped"] == {"entry": 1, "exit": 0}
    (row,) = snap["answers"]
    assert set(row) == {"anon_id", "entry", "exit"}  # nothing that identifies a person or a phone
    assert row["entry"]["q1"] == "a" and "at" in row["entry"]


def test_a_storage_failure_never_raises(tmp_path):
    class Broken:
        def write(self, name, data):
            raise OSError("disk full")

    o = Outcomes(Broken())
    o.record("anon-0001", "entry", entry(RIGHT, 3))
    assert o.save("reset") is None
    assert o.last_saved["ok"] is False


def test_blob_sink_puts_a_block_blob_with_a_bearer_token():
    sent = {}

    class Cred:
        def get_token(self, scope):
            sent["scope"] = scope
            return type("T", (), {"token": "tok"})()

    class Resp(io.BytesIO):
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        sent["req"] = req
        return Resp()

    BlobSink("https://acct.blob.core.windows.net/outcomes/", Cred(), opener).write("JAL-X/a.json", b"{}")
    req = sent["req"]
    assert sent["scope"] == "https://storage.azure.com/.default"
    assert req.full_url == "https://acct.blob.core.windows.net/outcomes/JAL-X/a.json"
    assert req.get_method() == "PUT"
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.get_header("X-ms-blob-type") == "BlockBlob"


# ------------------------------------------------------------------ pairing

def test_pairing_uses_only_ids_with_both_phases():
    snap = {"answers": [
        {"anon_id": "a1", "entry": entry(WRONG, 2), "exit": exit_(RIGHT, 4, comment="great")},
        {"anon_id": "a2", "entry": entry(RIGHT, 3), "exit": exit_(RIGHT, 5, helped=2)},
        {"anon_id": "a3", "entry": entry(WRONG, 1)},                       # entry only
        {"anon_id": "a4", "exit": exit_(WRONG, 3, recommend=5)},           # exit only
    ], "skipped": {"entry": 2, "exit": 1}}
    s = summarize(snap)
    assert (s["n_entry"], s["n_exit"], s["n_paired"]) == (3, 3, 2)
    assert quiz_score(WRONG) == 1 and quiz_score(RIGHT) == 3
    assert s["quiz"]["entry_mean"] == 2.0 and s["quiz"]["exit_mean"] == 3.0 and s["quiz"]["mean_gain"] == 1.0
    assert s["confidence"]["entry_mean"] == 2.5 and s["confidence"]["exit_mean"] == 4.5
    assert s["agree_pct"]["helped"] == {"pct": 66.7, "n": 3}
    assert s["agree_pct"]["recommend"] == {"pct": 33.3, "n": 3}
    assert s["agree_pct"]["c1_entry"] == {"pct": 0.0, "n": 3}
    assert s["comments"] == ["great"]
    assert s["skipped"] == {"entry": 2, "exit": 1}


def test_merge_keeps_runs_apart_and_latest_answer_wins():
    a = {"session_id": "S1", "session_started_at": 1, "written_at": 10, "skipped": {"entry": 1, "exit": 0},
         "answers": [{"anon_id": "x", "entry": {**entry(WRONG, 1), "at": 5}}]}
    b = {"session_id": "S1", "session_started_at": 1, "written_at": 20, "skipped": {"entry": 2, "exit": 0},
         "answers": [{"anon_id": "x", "entry": {**entry(WRONG, 1), "at": 5}, "exit": {**exit_(RIGHT, 5), "at": 15}}]}
    c = {"session_id": "S2", "session_started_at": 30, "written_at": 40, "skipped": {"entry": 0, "exit": 0},
         "answers": [{"anon_id": "x", "entry": {**entry(RIGHT, 4), "at": 35}}]}  # same phone, Wednesday
    m = merge([b, c, a])
    assert m["runs"] == 2 and m["sessions"] == ["S1", "S2"]
    assert m["skipped"] == {"entry": 2, "exit": 0}  # latest snapshot of S1, not the sum of both
    s = summarize(m)
    assert (s["n_entry"], s["n_exit"], s["n_paired"]) == (2, 1, 1)  # Wednesday's entry is not paired with Monday's exit


# ------------------------------------------------------------------ the app

def test_phone_flow_export_and_snapshots_on_terminate_and_reset(sink):
    with admin_client() as c:
        s = c.post("/api/admin/session", json={"minutes": 30}).json()
        p1, p2 = join(c, s["code"]), join(c, s["code"])
        assert p1.get("/api/me").json()["outcomes"] == {"entry_done": False, "exit_done": False}

        q = p1.get("/api/outcomes/questions").json()
        assert [i["id"] for i in q["quiz"]] == ["q1", "q2", "q3"]
        assert set(q) == {"quiz", "confidence", "exit_items", "scale"}  # the answer key is not sent
        assert all(set(i) == {"id", "text", "options"} for i in q["quiz"])

        assert p1.post("/api/outcomes/entry", json={"anon_id": "anon-p1-0001", **entry(WRONG, 2)}).status_code == 200
        assert p1.post("/api/outcomes/entry", json={"anon_id": "anon-p1-0002", **entry(RIGHT, 5)}).status_code == 409
        assert p2.post("/api/outcomes/skip", json={"phase": "entry"}).status_code == 200
        assert p1.get("/api/me").json()["outcomes"]["entry_done"] is True
        # bad input is refused before it is stored
        assert p2.post("/api/outcomes/entry", json={"anon_id": "x", **entry(RIGHT, 9)}).status_code == 422
        # the debrief opens only when the exercise is terminated
        assert p1.post("/api/outcomes/exit", json={"anon_id": "anon-p1-0001", **exit_(RIGHT, 4)}).status_code == 409

        c.post("/api/admin/inject/start")
        c.post("/api/admin/inject/end")
        (term,) = [x for x in snapshots(sink) if x["reason"] == "terminate"]
        assert term["session_id"] == s["code"]
        assert [r["anon_id"] for r in term["answers"]] == ["anon-p1-0001"]

        assert p1.get("/api/me").json()["ended"] is True
        r = p1.post("/api/outcomes/exit", json={"anon_id": "anon-p1-0001",
                                                **exit_(RIGHT, 4, comment="call me on 9876543210")})
        assert r.status_code == 200
        assert p2.post("/api/outcomes/exit", json={"anon_id": "anon-p2-0001", **exit_(RIGHT, 5)}).status_code == 200

        out = c.get("/api/admin/outcomes").json()
        assert (out["n_entry"], out["n_exit"], out["n_paired"]) == (1, 2, 1)
        assert out["quiz"]["entry_mean"] == 1.0 and out["quiz"]["exit_mean"] == 3.0
        assert out["confidence"] == {"scale": "1-5", "entry_mean": 2.0, "exit_mean": 4.0, "n": 1}
        assert out["agree_pct"]["helped"] == {"pct": 100.0, "n": 2}
        assert out["skipped"] == {"entry": 1, "exit": 0}
        assert len(out["comments"]) == 1 and "9876543210" not in out["comments"][0]
        assert out["last_saved"]["ok"] is True

        assert c.post("/api/admin/reset").status_code == 200
        (reset,) = [x for x in snapshots(sink) if x["reason"] == "reset"]
        assert {r["anon_id"] for r in reset["answers"]} == {"anon-p1-0001", "anon-p2-0001"}
        assert "9876543210" not in json.dumps(reset)
        assert c.get("/api/admin/outcomes").json()["n_entry"] == 0  # wiped only after it was written


def test_outcomes_are_admin_only(sink):
    with TestClient(app) as anon:
        assert anon.get("/api/admin/outcomes").status_code == 401
        assert anon.get("/api/admin/outcomes/snapshot").status_code == 401
        assert anon.post("/api/outcomes/entry", json={"anon_id": "anon-0000-01", **entry(RIGHT, 3)}).status_code == 403
    with admin_client() as c:
        s = c.post("/api/admin/session", json={"minutes": 30}).json()
        officer = TestClient(app)
        assert officer.post("/api/login", json={"secret": s["officer_code"]}).status_code == 200
        assert officer.get("/api/state").status_code == 200  # an officer, yet
        assert officer.get("/api/admin/outcomes").status_code == 401
        assert c.get("/api/admin/outcomes").status_code == 200


# ------------------------------------------------------------------ report script

def test_report_writes_csv_and_summary(tmp_path):
    snap = {"session_id": "JAL-R", "session_started_at": 1, "written_at": 2, "skipped": {"entry": 0, "exit": 0},
            "answers": [{"anon_id": "r1", "entry": {**entry(WRONG, 2), "at": 1}, "exit": {**exit_(RIGHT, 4), "at": 2}},
                        {"anon_id": "r2", "entry": {**entry(RIGHT, 3), "at": 1}}]}
    (tmp_path / "s.json").write_text(json.dumps(snap), encoding="utf-8")
    out = tmp_path / "report"
    script = Path(__file__).resolve().parents[1] / "scripts" / "outcomes_report.py"
    r = subprocess.run([sys.executable, str(script), "--snapshots", str(tmp_path / "s.json"), "--out", str(out),
                        "--no-charts"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    rows = (out / "outcomes.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("run,anon_id,paired,entry_quiz,exit_quiz") and len(rows) == 3
    text = (out / "summary.txt").read_text(encoding="utf-8")
    assert "paired: 1" in text and "rose from 1.00 to 3.00 out of 3" in text
