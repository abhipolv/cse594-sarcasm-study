# Sarcasm study (CSE 594, Assignment 3)

A small web app for the Assignment 3 study. Participants read tweets from the Assignment 2
study dataset (iSarcasmEval, author-labeled) and decide whether each author meant it
sarcastically. There are two conditions, served by the same app:

| Link | Condition |
|---|---|
| `/baseline` | on their own |
| `/ai` | with a live AI suggestion (label, confidence 50-100, explanation) from `openai/gpt-6-luna` |

Both conditions share one database, so a participant who does both (matched by uniqname) never
sees the same tweet twice, and the order they did the conditions in is recorded.

## What a participant does

1. **Instructions**: task definition (worded like the AI's prompt, identical in both conditions),
   three example tweets from the iSarcasmEval *training* split, a content note, what data is
   recorded, and consent ("I agree, start"). `?pid=uniqname` in the URL pre-fills the uniqname.
2. **N tweets + 1 attention check**, one at a time. Tweets are drawn at random for each person,
   half sarcastic and half not. They answer with one of four buttons
   (*Definitely / Probably sarcastic* in orange, *Probably not / Definitely not sarcastic* in blue)
   or keys 1-4, which records the label and their confidence in one click. The buttons unlock
   1 s after the tweet appears (and, in the AI condition, once the suggestion is shown).
   Response time is measured silently from when the tweet appears (no timer shown); the AI's
   loading delay is logged separately. A collapsible instruction reminder and an optional
   comment box are on every tweet. No feedback on correctness is given.
   **Back**: "← Previous tweet" (and "← Back to the last tweet" on the survey) shows an earlier
   tweet with its answer outlined; answering it again saves the change and returns to the first
   unanswered tweet. The database keeps both the first and the final answer.
3. **Attention check**: an item that asks for "Definitely not sarcastic". Either "not sarcastic"
   answer passes. Any other answer ends the session; they start over with new tweets.
4. **Survey** (1-7): mental demand; in the AI condition also trust in and helpfulness of the AI.
5. **Done**, with a link to the other condition if they haven't done it yet.

## Files

```
main.py           web server: pages, the 4 API endpoints, admin/CSV export
ai.py             live model call (same prompt/model/settings as Assignment 2)
db.py             SQLite schema and helpers (table/column meanings are documented here)
static/index.html all screens (one <section> each)
static/app.js     screen flow, timing, prefetching AI suggestions
static/style.css  styles
data/trials.csv   240 study tweets + author label + saved Assignment 2 AI output (fallback)
scripts/build_trials.py  rebuilds data/trials.csv from the Assignment 2 outputs
tests/test_app.py end-to-end API tests (model call faked)
```

How a trial flows through the system:

```
browser (app.js)                         server (main.py)                    storage
POST /api/sessions  ───────────────────> picks tweets, records order  ────> sessions
GET  /api/sessions/{id}/ai/{trial} ────> ai.classify() live call      ────> ai_outputs
       (prefetched one tweet ahead)        (falls back to saved output)
POST /api/sessions/{id}/responses ─────> scores answer, attention check ──> responses
POST /api/sessions/{id}/survey ────────>                                ──> surveys
```

## Data recorded

All tables are in `$DATA_DIR/study.db`; `db.py` documents every column.

- `sessions`: one row per participant per condition. Includes `condition_order` (1 or 2), the
  tweets shown, time on the instructions, and `status` (`completed` / `failed_attention` /
  `active` = abandoned).
- `responses`: one row per answered item: final answer and certainty (plus `first_answer`,
  `first_certainty`, `revisions` = times changed via Back, `revisit_ms`), author label, correct, the AI's
  label and confidence (AI condition), whether they agreed with the AI, response time,
  `away_count` (times the page lost focus during that tweet, a flag for looking things up or
  using another AI), and an optional `comment`.
- `ai_outputs`: exactly what the AI showed, with `source` = `live` or `cached` (fallback) and
  the error if the live call failed.
- `surveys`: the questionnaire answers.

For analysis, use `completed` sessions and drop rows with `is_attention = 1`.

## Run locally

```bash
uv sync
OPENROUTER_API_KEY=sk-or-... ADMIN_PASSWORD=changeme uv run uvicorn main:app --reload
```

Open http://localhost:8000/baseline or /ai. Data goes to `data_local/study.db` (gitignored).
See it at http://localhost:8000/admin?key=changeme.

Tests: `uv run pytest`

## Settings (environment variables)

| Name | Purpose | Default |
|---|---|---|
| `OPENROUTER_API_KEY` | live AI calls; without it the AI condition shows saved outputs only | none |
| `ADMIN_PASSWORD` | unlocks `/admin` and the CSV downloads | none (admin disabled) |
| `N_TRIALS` | tweets per condition (the attention check is extra) | `10` |
| `DATA_DIR` | folder for `study.db` | `data_local`, `/data` in Docker |
| `AI_MODEL`, `AI_TIMEOUT_S` | model id and per-call timeout | `openai/gpt-6-luna`, `8` |

## Deploy on Railway

Same setup as Assignment 1: Docker + a volume for the SQLite file.

1. Push this folder to a GitHub repo.
2. Railway: **New Project → Deploy from GitHub repo**. It picks up the `Dockerfile`.
3. Service → **Variables**: `OPENROUTER_API_KEY`, `ADMIN_PASSWORD`, `DATA_DIR=/data`
   (and `N_TRIALS` if not 10).
4. Add a **Volume** mounted at `/data`.
5. **Settings → Networking → Generate Domain**. The study links are `https://<domain>/baseline`
   and `https://<domain>/ai`.

Get the data from `https://<domain>/admin?key=<ADMIN_PASSWORD>` (one CSV per table).
