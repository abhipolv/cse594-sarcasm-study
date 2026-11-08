"""Sarcasm study server (CSE 594, Assignment 3).

Pages
  /            links to both conditions
  /baseline    the task without AI         (same page; static/app.js reads the path)
  /ai          the task with AI assistance
  /admin       data summary + CSV downloads (needs ?key=ADMIN_PASSWORD)

API used by static/app.js
  GET  /api/config                           number of items per condition
  POST /api/sessions                         start a condition, get this participant's tweets
  GET  /api/sessions/{sid}/ai/{trial_id}     AI suggestion for one tweet (live call, logged)
  POST /api/sessions/{sid}/responses         save one answer
  POST /api/sessions/{sid}/survey            save the end-of-condition questionnaire

Run locally:  uv run uvicorn main:app --reload
"""

import csv
import html
import io
import json
import os
import random
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import ai
import db

HERE = Path(__file__).parent
N_TRIALS = int(os.environ.get("N_TRIALS", "10"))  # real tweets per condition (+1 attention check)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

# The 240 study tweets from Assignment 2, keyed by id ('s000'...). Includes the saved AI output.
with open(HERE / "data" / "trials.csv", newline="") as f:
    TRIALS = {row["id"]: row for row in csv.DictReader(f)}

ATTENTION_ID = "attn"
ATTENTION_TEXT = (
    "This is an attention check, not a real tweet. To show you are reading carefully, "
    "please answer “Definitely not sarcastic”."
)

app = FastAPI()
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.middleware("http")
async def always_revalidate(request, call_next):
    """Make browsers check for a newer page/script on every load, so a fix deployed
    mid-study reaches everyone (unchanged files are still answered with a cheap 304)."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response
db.init()
if not os.environ.get("OPENROUTER_API_KEY"):
    print("WARNING: OPENROUTER_API_KEY is not set; the AI condition will only show saved outputs.")


# ----------------------------------------------------------------------------- pages
@app.get("/")
@app.get("/baseline")
@app.get("/ai")
def page():
    return FileResponse(HERE / "static" / "index.html")


# ----------------------------------------------------------------------------- helpers
def get_session(session_id: str):
    rows = db.query("SELECT * FROM sessions WHERE session_id = ?", session_id)
    if not rows:
        raise HTTPException(404, "unknown session")
    return rows[0]


def pick_trials(seen: set[str]) -> list[str]:
    """N_TRIALS tweet ids, half sarcastic and half not, none the participant has seen before,
    shuffled, with the attention check inserted somewhere in the middle."""
    pool = [t for t in TRIALS.values() if t["id"] not in seen]
    if len(pool) < 2 * N_TRIALS:  # someone has seen almost everything (e.g. repeated testing)
        pool = list(TRIALS.values())
    sarcastic = [t["id"] for t in pool if t["sarcastic"] == "1"]
    literal = [t["id"] for t in pool if t["sarcastic"] == "0"]
    n_sarcastic = N_TRIALS // 2 + random.randint(0, N_TRIALS % 2)  # odd N: coin flip for the extra
    ids = random.sample(sarcastic, n_sarcastic) + random.sample(literal, N_TRIALS - n_sarcastic)
    random.shuffle(ids)
    ids.insert(random.randint(2, max(2, N_TRIALS - 1)), ATTENTION_ID)  # never 1st/2nd or last
    return ids


# ----------------------------------------------------------------------------- API
@app.get("/api/config")
def config():
    return {"n_items": N_TRIALS + 1}  # tweets + the attention check, as the progress counter shows


class NewSession(BaseModel):
    participant: str = Field(min_length=1, max_length=64)
    condition: Literal["baseline", "ai"]
    instructions_ms: int | None = None


@app.post("/api/sessions")
def create_session(body: NewSession):
    participant = body.participant.strip().lower()
    previous = db.query("SELECT condition, status, trial_ids FROM sessions WHERE participant = ?",
                        participant)
    # Never show a tweet twice to the same person (matters most across the two conditions).
    seen = {tid for row in previous for tid in json.loads(row["trial_ids"])}
    did_other_condition = any(r["condition"] != body.condition and r["status"] == "completed"
                              for r in previous)
    session_id = uuid.uuid4().hex
    trial_ids = pick_trials(seen)
    db.insert("sessions",
              session_id=session_id,
              participant=participant,
              condition=body.condition,
              condition_order=2 if did_other_condition else 1,
              trial_ids=json.dumps(trial_ids),
              instructions_ms=body.instructions_ms,
              status="active",
              started_at=db.now())
    # html.unescape: the dataset stores "&" as "&amp;" etc. (the model still gets the original text, as in A2)
    trials = [{"id": tid, "tweet": ATTENTION_TEXT if tid == ATTENTION_ID else html.unescape(TRIALS[tid]["tweet"])}
              for tid in trial_ids]
    return {"session_id": session_id, "trials": trials,
            "condition_order": 2 if did_other_condition else 1}


@app.get("/api/sessions/{session_id}/ai/{trial_id}")
def ai_suggestion(session_id: str, trial_id: str):
    """Call the model live; if that fails, fall back to the saved Assignment 2 output.
    Whatever is returned is logged first, so the data shows exactly what the participant saw."""
    s = get_session(session_id)
    if s["condition"] != "ai" or trial_id not in json.loads(s["trial_ids"]) or trial_id == ATTENTION_ID:
        raise HTTPException(404, "no AI suggestion for this trial")

    logged = db.query("SELECT * FROM ai_outputs WHERE session_id = ? AND trial_id = ?",
                      session_id, trial_id)
    if not logged:
        trial = TRIALS[trial_id]
        out, error = None, None
        for _ in range(2):  # one retry
            try:
                out = ai.classify(trial["tweet"])
                break
            except Exception as e:
                error = repr(e)[:500]
        source = "live" if out else "cached"
        if out is None:
            out = {"label": trial["ai_label"], "confidence": int(trial["ai_confidence"]),
                   "explanation": trial["ai_explanation"], "latency_s": None, "raw": None}
        db.insert("ai_outputs", session_id=session_id, trial_id=trial_id, source=source,
                  label=out["label"], confidence=out["confidence"], explanation=out["explanation"],
                  latency_s=out["latency_s"], error=error, raw=out["raw"], created_at=db.now())
        logged = db.query("SELECT * FROM ai_outputs WHERE session_id = ? AND trial_id = ?",
                          session_id, trial_id)
    row = logged[0]
    return {"label": row["label"], "confidence": row["confidence"], "explanation": row["explanation"]}


class Answer(BaseModel):
    position: int  # 1-based
    answer: Literal["sarcastic", "not_sarcastic"]
    certainty: Literal["definitely", "probably"]
    rt_ms: int | None = None
    ai_wait_ms: int | None = None
    away_count: int | None = None
    comment: str | None = Field(default=None, max_length=1000)


@app.post("/api/sessions/{session_id}/responses")
def save_response(session_id: str, body: Answer):
    """Save an answer. Answering a tweet again (after pressing Back) updates its row:
    the final answer replaces the old one, the first answer is kept, and changes are counted."""
    s = get_session(session_id)
    trial_ids = json.loads(s["trial_ids"])
    n_answered = db.query("SELECT COUNT(*) AS n FROM responses WHERE session_id = ?", session_id)[0]["n"]
    survey_done = db.query("SELECT 1 FROM surveys WHERE session_id = ?", session_id)
    if (s["status"] == "failed_attention" or survey_done
            or not 1 <= body.position <= min(n_answered + 1, len(trial_ids))):
        raise HTTPException(400, "session is not accepting this answer")
    trial_id = trial_ids[body.position - 1]
    is_attention = trial_id == ATTENTION_ID

    if is_attention:
        truth = None
        correct = body.answer == "not_sarcastic"  # lenient: "probably not" also passes
    else:
        truth = "sarcastic" if TRIALS[trial_id]["sarcastic"] == "1" else "not_sarcastic"
        correct = body.answer == truth

    ai_label = ai_conf = agreed = None
    if s["condition"] == "ai" and not is_attention:
        shown = db.query("SELECT label, confidence FROM ai_outputs WHERE session_id = ? AND trial_id = ?",
                         session_id, trial_id)
        if shown:
            ai_label, ai_conf = shown[0]["label"], shown[0]["confidence"]
            agreed = int(body.answer == ai_label)

    previous = db.query("SELECT answer, certainty FROM responses WHERE session_id = ? AND position = ?",
                        session_id, body.position)
    if previous:  # revisited via Back
        changed = (previous[0]["answer"], previous[0]["certainty"]) != (body.answer, body.certainty)
        db.execute(
            """UPDATE responses SET answer = ?, certainty = ?, correct = ?, agreed_with_ai = ?,
                   revisions = revisions + ?, revisit_ms = COALESCE(revisit_ms, 0) + ?,
                   away_count = COALESCE(away_count, 0) + ?, comment = ?, updated_at = ?
               WHERE session_id = ? AND position = ?""",
            body.answer, body.certainty, int(correct), agreed, int(changed), body.rt_ms or 0,
            body.away_count or 0, body.comment, db.now(), session_id, body.position)
    else:
        db.insert("responses", session_id=session_id, participant=s["participant"],
                  condition=s["condition"], position=body.position, trial_id=trial_id,
                  is_attention=int(is_attention), answer=body.answer, certainty=body.certainty,
                  first_answer=body.answer, first_certainty=body.certainty, revisions=0,
                  truth=truth, correct=int(correct), ai_label=ai_label, ai_confidence=ai_conf,
                  agreed_with_ai=agreed, rt_ms=body.rt_ms, ai_wait_ms=body.ai_wait_ms,
                  away_count=body.away_count, comment=body.comment, created_at=db.now())

    if is_attention and not correct:
        db.execute("UPDATE sessions SET status = 'failed_attention', finished_at = ? WHERE session_id = ?",
                   db.now(), session_id)
        return {"failed_attention": True}
    if body.position == len(trial_ids):
        db.execute("UPDATE sessions SET status = 'completed', finished_at = ? WHERE session_id = ?",
                   db.now(), session_id)
    return {"failed_attention": False}


class Survey(BaseModel):
    mental_demand: int = Field(ge=1, le=7)
    ai_trust: int | None = Field(default=None, ge=1, le=7)
    ai_helpful: int | None = Field(default=None, ge=1, le=7)
    comment: str | None = Field(default=None, max_length=2000)


@app.post("/api/sessions/{session_id}/survey")
def save_survey(session_id: str, body: Survey):
    s = get_session(session_id)
    db.insert("surveys", session_id=session_id, participant=s["participant"],
              condition=s["condition"], created_at=db.now(), **body.model_dump())
    return {"ok": True}


# ----------------------------------------------------------------------------- admin
def check_key(key: str | None) -> None:
    if not ADMIN_PASSWORD or key != ADMIN_PASSWORD:
        raise HTTPException(403, "set ADMIN_PASSWORD on the server and pass it as ?key=")


@app.get("/admin", response_class=HTMLResponse)
def admin(key: str | None = None):
    check_key(key)
    counts = db.query("SELECT condition, status, COUNT(*) AS n, COUNT(DISTINCT participant) AS people "
                      "FROM sessions GROUP BY condition, status ORDER BY condition, status")
    sources = db.query("SELECT source, COUNT(*) AS n FROM ai_outputs GROUP BY source")
    rows = "".join(f"<tr><td>{r['condition']}</td><td>{r['status']}</td><td>{r['n']}</td>"
                   f"<td>{r['people']}</td></tr>" for r in counts)
    ai_line = ", ".join(f"{r['source']}: {r['n']}" for r in sources) or "none yet"
    k = html.escape(key)
    links = " · ".join(f'<a href="/admin/{t}.csv?key={k}">{t}.csv</a>' for t in db.TABLES)
    return f"""<!doctype html><meta charset="utf-8"><title>Study data</title>
<body style="font-family:system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem">
<h1>Study data</h1>
<table border="1" cellpadding="6" style="border-collapse:collapse">
<tr><th>condition</th><th>status</th><th>sessions</th><th>participants</th></tr>{rows}</table>
<p>AI outputs shown: {ai_line}</p><p>Download: {links}</p></body>"""


@app.get("/admin/{table}.csv")
def export(table: str, key: str | None = None):
    check_key(key)
    if table not in db.TABLES:
        raise HTTPException(404)
    with db.connect() as conn:
        cur = conn.execute(f"SELECT * FROM {table}")
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([c[0] for c in cur.description])
        writer.writerows(cur.fetchall())
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{table}.csv"'})
