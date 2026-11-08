"""End-to-end API tests. The model call is faked, so no API key or network is needed.

Run: uv run pytest
"""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp()  # fresh database for this test run
os.environ["ADMIN_PASSWORD"] = "test"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import ai  # noqa: E402
import main  # noqa: E402

client = TestClient(main.app)


def fake_classify(tweet):
    return {"label": "sarcastic", "confidence": 77, "explanation": "fake",
            "latency_s": 0.01, "raw": "{}"}


def run_condition(participant, condition, fail_attention=False):
    s = client.post("/api/sessions", json={"participant": participant, "condition": condition}).json()
    for pos, trial in enumerate(s["trials"], start=1):
        if condition == "ai" and trial["id"] != "attn":
            sug = client.get(f"/api/sessions/{s['session_id']}/ai/{trial['id']}").json()
            assert sug["confidence"] == 77
        answer = "not_sarcastic" if trial["id"] == "attn" and not fail_attention else "sarcastic"
        r = client.post(f"/api/sessions/{s['session_id']}/responses",
                        json={"position": pos, "answer": answer, "certainty": "probably", "rt_ms": 1234,
                              "away_count": 0, "comment": "note" if pos == 1 else None})
        if r.json()["failed_attention"]:
            return s, "failed"
    client.post(f"/api/sessions/{s['session_id']}/survey",
                json={"mental_demand": 4, "ai_trust": 5 if condition == "ai" else None})
    return s, "completed"


def test_pages_load():
    for path in ("/", "/baseline", "/ai", "/static/app.js"):
        assert client.get(path).status_code == 200
    assert client.get("/api/config").json()["n_items"] == main.N_TRIALS + 1


def test_trials_are_balanced_with_one_attention_check():
    s = client.post("/api/sessions", json={"participant": "Balance", "condition": "baseline"}).json()
    ids = [t["id"] for t in s["trials"]]
    assert ids.count("attn") == 1 and ids[0] != "attn" and ids[-1] != "attn"
    real = [i for i in ids if i != "attn"]
    n_sarcastic = sum(main.TRIALS[i]["sarcastic"] == "1" for i in real)
    assert len(real) == main.N_TRIALS and abs(2 * n_sarcastic - len(real)) <= 1


def test_within_subjects_flow(monkeypatch):
    monkeypatch.setattr(ai, "classify", fake_classify)
    first, status1 = run_condition("Alice", "baseline")
    second, status2 = run_condition("alice ", "ai")  # same person, different spelling/case
    assert status1 == status2 == "completed"
    assert first["condition_order"] == 1 and second["condition_order"] == 2

    # No tweet repeated across the two conditions.
    ids1 = {t["id"] for t in first["trials"]} - {"attn"}
    ids2 = {t["id"] for t in second["trials"]} - {"attn"}
    assert not ids1 & ids2

    # AI outputs were logged and copied into the responses.
    rows = main.db.query("SELECT * FROM responses WHERE session_id = ? AND is_attention = 0",
                         second["session_id"])
    assert len(rows) == main.N_TRIALS
    assert all(r["ai_label"] == "sarcastic" and r["agreed_with_ai"] == 1 for r in rows)
    first_row = next(r for r in rows if r["position"] == 1)
    assert first_row["comment"] == "note" and first_row["away_count"] == 0
    logged = main.db.query("SELECT source FROM ai_outputs WHERE session_id = ?", second["session_id"])
    assert {r["source"] for r in logged} == {"live"}


def test_ai_falls_back_to_saved_output(monkeypatch):
    def broken(tweet):
        raise TimeoutError("simulated")
    monkeypatch.setattr(ai, "classify", broken)
    s = client.post("/api/sessions", json={"participant": "bob", "condition": "ai"}).json()
    trial = next(t for t in s["trials"] if t["id"] != "attn")
    sug = client.get(f"/api/sessions/{s['session_id']}/ai/{trial['id']}").json()
    assert sug["confidence"] == int(main.TRIALS[trial["id"]]["ai_confidence"])
    row = main.db.query("SELECT source, error FROM ai_outputs WHERE session_id = ?", s["session_id"])[0]
    assert row["source"] == "cached" and "simulated" in row["error"]


def test_failed_attention_check_ends_session():
    s, status = run_condition("carol", "baseline", fail_attention=True)
    assert status == "failed"
    row = main.db.query("SELECT status FROM sessions WHERE session_id = ?", s["session_id"])[0]
    assert row["status"] == "failed_attention"
    # Further answers are rejected.
    r = client.post(f"/api/sessions/{s['session_id']}/responses",
                    json={"position": len(s["trials"]), "answer": "sarcastic", "certainty": "probably"})
    assert r.status_code == 400


def test_admin_requires_key_and_exports_csv():
    assert client.get("/admin").status_code == 403
    assert client.get("/admin?key=test").status_code == 200
    csv_text = client.get("/admin/responses.csv?key=test").text
    assert csv_text.splitlines()[0].startswith("session_id,participant,condition")
    assert json.loads(client.get("/api/config").text)


def test_back_keeps_first_answer_and_counts_changes():
    s = client.post("/api/sessions", json={"participant": "dana", "condition": "baseline"}).json()
    sid, url = s["session_id"], f"/api/sessions/{s['session_id']}/responses"
    post = lambda pos, ans, cert="probably", rt=1000: client.post(
        url, json={"position": pos, "answer": ans, "certainty": cert, "rt_ms": rt, "away_count": 0})

    assert post(1, "sarcastic").status_code == 200
    assert post(3, "sarcastic").status_code == 400          # can't skip ahead
    assert post(2, "not_sarcastic").status_code == 200
    assert post(1, "sarcastic", rt=500).status_code == 200  # Back, same answer: not a change
    assert post(1, "not_sarcastic", "definitely", rt=700).status_code == 200  # Back, changed

    row = main.db.query("SELECT * FROM responses WHERE session_id = ? AND position = 1", sid)[0]
    assert (row["first_answer"], row["answer"], row["certainty"]) == ("sarcastic", "not_sarcastic", "definitely")
    assert row["revisions"] == 1 and row["rt_ms"] == 1000 and row["revisit_ms"] == 1200
    truth = main.TRIALS[row["trial_id"]]["sarcastic"] == "1"
    assert row["correct"] == int(not truth)  # scored on the final answer

    client.post(f"/api/sessions/{sid}/survey", json={"mental_demand": 3})
    assert post(1, "sarcastic").status_code == 400          # no changes after the survey
