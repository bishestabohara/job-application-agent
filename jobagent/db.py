"""SQLite tracker: companies, jobs, contacts, outreach."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import yaml

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  website TEXT, lane TEXT, ats TEXT, slug TEXT,
  status TEXT DEFAULT 'target',          -- pursuing|evaluate|target|candidate|ruled_out
  nyc INTEGER DEFAULT 0,
  sponsorship TEXT DEFAULT 'unknown',
  size TEXT, why TEXT, note TEXT,
  verify INTEGER DEFAULT 0,
  source TEXT DEFAULT 'seed',            -- seed|discover
  contacts_searched_at TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  company_id INTEGER REFERENCES companies(id),
  ext_id TEXT NOT NULL, title TEXT, location TEXT, url TEXT, description TEXT,
  posted_at TEXT, first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
  fit_score INTEGER, role_type TEXT, resume_version TEXT, sponsorship TEXT,
  nyc INTEGER, why TEXT, red_flags TEXT, scored_at TEXT,
  status TEXT DEFAULT 'new',             -- new|surfaced|applied|skipped|closed
  applied_at TEXT,
  UNIQUE(company_id, ext_id)
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  company_id INTEGER REFERENCES companies(id),
  name TEXT NOT NULL, title TEXT, linkedin_url TEXT, email TEXT, email_confidence INTEGER,
  hook TEXT, evidence_url TEXT, why_this_person TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(company_id, name)
);
CREATE TABLE IF NOT EXISTS outreach (
  id INTEGER PRIMARY KEY,
  contact_id INTEGER REFERENCES contacts(id),
  channel TEXT NOT NULL,                 -- email|linkedin
  step INTEGER DEFAULT 0,                -- 0=first touch, 1=follow-up, 2=final
  subject TEXT, body TEXT, resume_version TEXT,
  status TEXT DEFAULT 'drafted',         -- drafted|approved|gmail_draft|sent|replied|skipped
  gmail_draft_id TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, sent_at TEXT, replied_at TEXT,
  gmail_message_id TEXT, gmail_thread_id TEXT, rfc_message_id TEXT
);
CREATE TABLE IF NOT EXISTS applications (
  job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
  url TEXT NOT NULL,
  fields TEXT,                           -- JSON from apply.extract_fields
  answers TEXT,                          -- JSON {field key: value}
  resume_version TEXT,
  status TEXT DEFAULT 'extracted',       -- extracted|prepared|submitted|needs_you|failed|skipped
  note TEXT,
  prepared_at TEXT, submitted_at TEXT
);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init() -> int:
    """Create tables and upsert seed companies. Returns number of seed rows loaded."""
    seeds = yaml.safe_load(Path(DATA_DIR / "companies.yaml").read_text()) or []
    with connect() as con:
        con.executescript(SCHEMA)
        _migrate(con)
        for c in seeds:
            upsert_company(con, c, source="seed")
    return len(seeds)


def _migrate(con: sqlite3.Connection) -> None:
    """Add columns introduced after a DB was first created."""
    for table, col in (("jobs", "applied_at"), ("outreach", "gmail_message_id"), ("outreach", "gmail_thread_id"),
                       ("outreach", "rfc_message_id"), ("jobs", "notion_page_id"), ("jobs", "notion_hash"),
                       ("contacts", "notion_page_id"), ("contacts", "notion_hash"),
                       ("contacts", "job_id")):
        if col not in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")


def upsert_company(con: sqlite3.Connection, c: dict, source: str) -> int:
    cols = ["name", "website", "lane", "ats", "slug", "status", "nyc", "sponsorship", "size", "why", "note", "verify"]
    vals = {k: c.get(k) for k in cols}
    vals["nyc"] = int(bool(vals["nyc"]))
    vals["verify"] = int(bool(vals["verify"]))
    vals["status"] = vals["status"] or ("candidate" if source == "discover" else "target")
    vals["sponsorship"] = vals["sponsorship"] or "unknown"
    set_clause = ", ".join(f"{k}=COALESCE({k}, excluded.{k})" for k in cols if k != "name")
    con.execute(
        f"INSERT INTO companies ({', '.join(cols)}, source) VALUES ({', '.join('?' * len(cols))}, ?) "
        f"ON CONFLICT(name) DO UPDATE SET {set_clause}",
        [*vals.values(), source],
    )
    return con.execute("SELECT id FROM companies WHERE name=?", (c["name"],)).fetchone()["id"]
