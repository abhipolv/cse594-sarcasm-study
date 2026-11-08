"""SQLite storage. The database file is $DATA_DIR/study.db (a Railway volume in production).

Four tables, all keyed by session_id (one session = one participant doing one condition):
  sessions    who, which condition, which tweets they were given, whether they finished
  ai_outputs  exactly what the AI showed for each trial (live call or saved fallback)
  responses   one row per answered trial
  surveys     the short questionnaire at the end of each condition
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "data_local"))
DB_PATH = DATA_DIR / "study.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    participant     TEXT NOT NULL,     -- lowercased uniqname
    condition       TEXT NOT NULL,     -- 'baseline' or 'ai'
    condition_order INTEGER NOT NULL,  -- 1 if this was their first condition, 2 if second
    trial_ids       TEXT NOT NULL,     -- JSON list, in the order shown ('attn' = attention check)
    instructions_ms INTEGER,           -- time spent on the instructions page
    status          TEXT NOT NULL,     -- 'active', 'completed', or 'failed_attention'
    started_at      TEXT NOT NULL,
    finished_at     TEXT
);
CREATE TABLE IF NOT EXISTS ai_outputs (
    session_id    TEXT NOT NULL,
    trial_id      TEXT NOT NULL,
    source        TEXT NOT NULL,       -- 'live' or 'cached' (A2 output, used if the live call fails)
    label         TEXT NOT NULL,
    confidence    INTEGER NOT NULL,
    explanation   TEXT NOT NULL,
    latency_s     REAL,
    error         TEXT,                -- why the live call failed, if it did
    raw           TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, trial_id)
);
CREATE TABLE IF NOT EXISTS responses (
    session_id     TEXT NOT NULL,
    participant    TEXT NOT NULL,
    condition      TEXT NOT NULL,
    position       INTEGER NOT NULL,   -- 1-based position in the session (attention check included)
    trial_id       TEXT NOT NULL,
    is_attention   INTEGER NOT NULL,
    answer         TEXT NOT NULL,      -- final answer: 'sarcastic' or 'not_sarcastic'
    certainty      TEXT NOT NULL,      -- final certainty: 'definitely' or 'probably'
    first_answer   TEXT NOT NULL,      -- answer given the first time (before any Back + change)
    first_certainty TEXT NOT NULL,
    revisions      INTEGER NOT NULL,   -- how many times the answer was changed via Back
    truth          TEXT,               -- author's label (NULL for the attention check)
    correct        INTEGER,            -- of the final answer
    ai_label       TEXT,               -- what the AI showed (NULL in baseline)
    ai_confidence  INTEGER,
    agreed_with_ai INTEGER,
    rt_ms          INTEGER,            -- tweet shown -> first answer clicked (measured in the browser)
    revisit_ms     INTEGER,            -- total extra time spent on this tweet after coming back to it
    ai_wait_ms     INTEGER,            -- tweet shown -> AI suggestion shown (AI condition; ~0 if prefetched)
    away_count     INTEGER,            -- times the page lost focus while this tweet was shown
    comment        TEXT,               -- optional per-tweet comment
    created_at     TEXT NOT NULL,
    updated_at     TEXT,               -- last change via Back
    PRIMARY KEY (session_id, position)
);
CREATE TABLE IF NOT EXISTS surveys (
    session_id    TEXT PRIMARY KEY,
    participant   TEXT NOT NULL,
    condition     TEXT NOT NULL,
    mental_demand INTEGER,             -- 1 (very low) .. 7 (very high)
    ai_trust      INTEGER,             -- 1 .. 7, AI condition only
    ai_helpful    INTEGER,             -- 1 .. 7, AI condition only
    comment       TEXT,
    created_at    TEXT NOT NULL
);
"""

TABLES = ("sessions", "ai_outputs", "responses", "surveys")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    """Open the database, commit on success (roll back on error), always close."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


def insert(table: str, **cols) -> None:
    """INSERT one row; on a primary-key clash the existing row is kept (makes retries harmless)."""
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    with connect() as conn:
        conn.execute(f"INSERT OR IGNORE INTO {table} ({names}) VALUES ({marks})", list(cols.values()))


def query(sql: str, *params) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql: str, *params) -> None:
    with connect() as conn:
        conn.execute(sql, params)
