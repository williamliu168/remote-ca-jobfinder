"""SQLite connection + schema bootstrap.

One table, `jobs`. The schema is created on first use; later column additions
go in `_JOB_COLUMN_MIGRATIONS` and are backfilled by `connect()` so there is no
separate migration tool (pattern borrowed from the larger reference pipeline).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "db.sqlite"     # the default profile's DB


def db_path() -> Path:
    """DB file for the active profile: db.sqlite for the default profile,
    db.<profile-name>.sqlite otherwise, so statuses never mix across profiles."""
    from .profile import load as load_profile   # local import: profile -> roles only
    prof = load_profile()
    if prof.is_default:
        return DB_PATH
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in prof.name)
    return DB_PATH.with_name(f"db.{safe}.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,          -- greenhouse | lever | ashby | remotive | remoteok | manual
    source_id TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    url TEXT,
    jd_text TEXT,
    posted_at TEXT,
    scraped_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'seen',  -- seen|shortlist|applied|interviewing|closed|rejected
    notes TEXT,
    country TEXT,        -- CA | remote | other | unknown
    remote_policy TEXT,  -- remote-ca | remote-global | remote-multi | hybrid | onsite | unknown
    role_tag TEXT,       -- legal | law-adjacent | other
    role_notes TEXT,     -- matched regex snippet (debug / tooltip)
    ca_eligible INTEGER, -- cached 1/0
    classified_at TEXT,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_role_tag ON jobs(role_tag);
"""

# Columns added after the initial schema. `connect()` adds any that are missing.
# v2 hook: light-intensity work would add e.g. ("job_type", "TEXT") here.
_JOB_COLUMN_MIGRATIONS: list[tuple[str, str]] = [
    ("fit_score", "INTEGER"),   # 0-100 résumé fit (jobfinder/score.py); NULL = unscored
    ("fit_hits", "TEXT"),       # vocab terms the JD asks for that the résumé shows
    ("fit_gaps", "TEXT"),       # vocab terms the JD asks for that the résumé lacks
    ("fit_flags", "TEXT"),      # dealbreakers / seniority notes
    ("scored_at", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    for col, decl in _JOB_COLUMN_MIGRATIONS:
        if col not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")
    conn.commit()


def connect() -> sqlite3.Connection:
    """Open a connection; create schema on first use, then backfill new columns."""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn
