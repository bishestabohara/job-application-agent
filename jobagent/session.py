"""JSON in/out commands used by the /job-session skill. No Claude API calls here — the skill does the thinking.

Every read command prints JSON to stdout; every write command takes a JSON file path (or "-" for stdin)
and validates it with the same pydantic models the API mode uses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from . import db
from .agents import Contact, DiscoveredCompany, Drafts, JobFit
from .config import criteria
from .pipeline import _open_roles, due_followups, pick_targets
from .sources import probe


def _load(path: str):
    return json.loads(sys.stdin.read() if path == "-" else Path(path).read_text())


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


def quotas(con) -> dict:
    q = criteria()["daily_session"]
    applied = con.execute("SELECT COUNT(*) n FROM jobs WHERE date(applied_at)=date('now')").fetchone()["n"]
    people = con.execute("SELECT COUNT(*) n FROM contacts WHERE date(created_at)=date('now')").fetchone()["n"]
    return {
        "applications": {"limit": q["applications"], "done_today": applied, "left": max(q["applications"] - applied, 0)},
        "new_people": {"limit": q["new_people"], "done_today": people, "left": max(q["new_people"] - people, 0)},
    }


def today() -> dict:
    """Everything the session needs to plan the day, in one call."""
    min_fit = criteria()["scoring"]["min_fit_to_surface"]
    with db.connect() as con:
        qs = quotas(con)
        return {
            "quotas": qs,
            "unclassified_companies": con.execute("SELECT COUNT(*) n FROM companies WHERE status='unclassified'").fetchone()["n"],
            "unscored_jobs": con.execute("""SELECT COUNT(*) n FROM jobs j JOIN companies c ON c.id=j.company_id
                WHERE j.scored_at IS NULL AND j.status='new' AND c.status NOT IN ('unclassified','ruled_out')
                AND (COALESCE(c.lane,'') NOT IN ('non_mission','other') OR c.status='stepping_stone')""").fetchone()["n"],
            "roles_to_apply": _rows(con.execute("""
                SELECT j.id, c.name company, j.title, j.location, j.url, j.fit_score, j.nyc, j.sponsorship, j.resume_version, j.why
                FROM jobs j JOIN companies c ON c.id=j.company_id
                WHERE j.status IN ('new','surfaced') AND j.fit_score>=?
                ORDER BY j.nyc DESC, j.fit_score DESC LIMIT ?""", (min_fit, max(qs["applications"]["left"], 0) + 5))),
            "drafts_awaiting_review": _rows(con.execute("""
                SELECT o.id, o.channel, ct.name, co.name company FROM outreach o
                JOIN contacts ct ON ct.id=o.contact_id JOIN companies co ON co.id=ct.company_id
                WHERE o.step=0 AND o.status IN ('drafted','approved')""")),
            "followups_due": _rows(due_followups(con)),
            "untouched_target_companies": con.execute("""
                SELECT COUNT(*) n FROM companies WHERE contacts_searched_at IS NULL AND status NOT IN ('ruled_out','unclassified')
                AND COALESCE(lane,'') NOT IN ('other','non_mission')""").fetchone()["n"],
        }


def queue(limit: int, chars: int, offset: int = 0) -> list[dict]:
    """Unscored postings, trimmed so a batch fits comfortably in a subagent's context."""
    with db.connect() as con:
        rows = con.execute("""SELECT j.id job_id, c.name company, c.lane, c.sponsorship company_sponsorship, c.note company_note,
                                     j.title, j.location, j.description
                              FROM jobs j JOIN companies c ON c.id=j.company_id
                              WHERE j.scored_at IS NULL AND j.status='new'
                                AND c.status NOT IN ('unclassified','ruled_out') AND (COALESCE(c.lane,'') NOT IN ('non_mission','other') OR c.status='stepping_stone')
                              ORDER BY (c.status='pursuing') DESC, c.nyc DESC, j.id LIMIT ? OFFSET ?""",
                           (limit, offset)).fetchall()
    out = _rows(rows)
    for r in out:
        r["description"] = (r["description"] or "")[:chars]
    return out


class _Score(JobFit):
    job_id: int


def save_scores(path: str) -> int:
    scores = TypeAdapter(list[_Score]).validate_python(_load(path))
    with db.connect() as con:
        for f in scores:
            c_spons = con.execute("SELECT c.sponsorship FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=?",
                                  (f.job_id,)).fetchone()
            spons = "confirmed_no" if c_spons and c_spons["sponsorship"] == "confirmed_no" else f.sponsorship
            score = f.fit_score if (f.level_ok and f.role_type != "not_target") else min(f.fit_score, 25)
            con.execute("""UPDATE jobs SET fit_score=?, role_type=?, resume_version=?, sponsorship=?, nyc=?, why=?, red_flags=?,
                           scored_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (score, f.role_type, f.resume_version, spons, int(f.nyc), f.why, json.dumps(f.red_flags), f.job_id))
    return len(scores)


def targets(limit: int) -> list[dict]:
    with db.connect() as con:
        rows = _rows(pick_targets(con, limit))
        for r in rows:
            r["open_roles"] = _open_roles(con, r["id"])
            r["already_contacted"] = [x["name"] for x in con.execute("SELECT name FROM contacts WHERE company_id=?", (r["id"],))]
    keep = ("id", "name", "website", "lane", "status", "nyc", "sponsorship", "why", "note", "open_roles", "already_contacted")
    return [{k: r[k] for k in keep} for r in rows]


def add_companies(path: str) -> list[dict]:
    found = TypeAdapter(list[DiscoveredCompany]).validate_python(_load(path))
    added = []
    with db.connect() as con:
        existing = {r["name"].lower() for r in con.execute("SELECT name FROM companies")}
        for c in found:
            if c.name.lower() in existing:
                continue
            board = probe(c.name)
            db.upsert_company(con, c.model_dump() | {
                "ats": board[0] if board else "none", "slug": board[1] if board else None,
                "verify": bool(board), "note": f"found via {c.evidence_url}"}, source="discover")
            added.append({"name": c.name, "job_board": f"{board[0]}/{board[1]}" if board else None})
    return added


class OutreachIn(BaseModel):
    company_id: int
    job_id: int | None = None      # the application this email accompanies
    contact: Contact
    drafts: Drafts
    email: str | None = None


def save_outreach(path: str) -> list[dict]:
    """Store researched contacts + their drafts. Marks each company as researched."""
    data = _load(path)
    items = TypeAdapter(list[OutreachIn]).validate_python(data if isinstance(data, list) else [data])
    out = []
    with db.connect() as con:
        for it in items:
            p, d = it.contact, it.drafts
            con.execute("UPDATE companies SET contacts_searched_at=CURRENT_TIMESTAMP WHERE id=?", (it.company_id,))
            cur = con.execute("""INSERT OR IGNORE INTO contacts (company_id, name, title, linkedin_url, email, email_confidence, hook, evidence_url, why_this_person, job_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (it.company_id, p.name, p.title, p.linkedin_url, it.email, 100 if it.email else None,
                               p.hook, p.evidence_url, p.why_this_person, it.job_id))
            if not cur.rowcount:
                out.append({"name": p.name, "skipped": "already in tracker"})
                continue
            cid = cur.lastrowid
            ids = {}
            for key, channel, step, subject, body, resume in (
                ("email", "email", 0, d.email_subject, d.email_body, d.resume_version),
                ("linkedin", "linkedin", 0, None, d.linkedin_note, None),
                ("follow_up_1", "email", 1, f"Re: {d.email_subject}", d.follow_up_1, None),
                ("follow_up_2", "email", 2, f"Re: {d.email_subject}", d.follow_up_2, None),
            ):
                ids[key] = con.execute("INSERT INTO outreach (contact_id, channel, step, subject, body, resume_version) VALUES (?,?,?,?,?,?)",
                                       (cid, channel, step, subject, body, resume)).lastrowid
            out.append({"name": p.name, "contact_id": cid, "outreach_ids": ids})
    return out


def mark_companies_researched(ids: list[int]) -> None:
    """For companies where no good contact was found — so they don't come back tomorrow."""
    with db.connect() as con:
        con.executemany("UPDATE companies SET contacts_searched_at=CURRENT_TIMESTAMP WHERE id=?", [(i,) for i in ids])


def edit_outreach(outreach_id: int, body: str, subject: str | None) -> None:
    with db.connect() as con:
        if subject is not None:
            con.execute("UPDATE outreach SET body=?, subject=? WHERE id=?", (body, subject, outreach_id))
        else:
            con.execute("UPDATE outreach SET body=? WHERE id=?", (body, outreach_id))


def unclassified(limit: int, offset: int = 0) -> list[dict]:
    """Companies from Simplify awaiting a mission verdict, with sample role titles as a hint."""
    with db.connect() as con:
        rows = _rows(con.execute("""SELECT c.id, c.name, GROUP_CONCAT(j.title, ' | ') roles FROM companies c
                                    LEFT JOIN jobs j ON j.company_id=c.id WHERE c.status='unclassified'
                                    GROUP BY c.id ORDER BY c.id LIMIT ? OFFSET ?""", (limit, offset)))
    for r in rows:
        r["roles"] = (r["roles"] or "")[:160]
    return rows


class _Verdict(BaseModel):
    id: int
    lane: str          # healthcare|education|caregiving_aging_mental_health|other_human_impact|non_mission
    stepping_stone: bool = False
    nyc: bool = False
    website: str | None = None
    why: str = ""


def classify(path: str) -> dict:
    lanes = {"healthcare", "education", "caregiving_aging_mental_health", "other_human_impact", "non_mission", "avoid"}
    verdicts = TypeAdapter(list[_Verdict]).validate_python(_load(path))
    counts: dict[str, int] = {}
    with db.connect() as con:
        for v in verdicts:
            lane = v.lane if v.lane in lanes else "non_mission"
            # avoid (ads, trading, gambling, defense, engagement-bait) is skipped unless it's a real stepping stone;
            # neutral non_mission companies stay appliable as tier 2.
            status = ("stepping_stone" if v.stepping_stone else "ruled_out") if lane == "avoid" else "target"
            con.execute("""UPDATE companies SET lane=?, status=?, nyc=MAX(nyc, ?), website=COALESCE(website, ?), why=COALESCE(why, ?)
                           WHERE id=? AND status='unclassified'""", (lane, status, int(v.nyc), v.website, v.why or None, v.id))
            counts[status] = counts.get(status, 0) + 1
    return counts


def _posted_ts(v: str | None) -> float | None:
    """posted_at comes as epoch seconds (Simplify), epoch ms (Lever) or ISO (Greenhouse/Ashby)."""
    from datetime import datetime

    if not v:
        return None
    if v.isdigit():
        n = int(v)
        return n / 1000 if n > 10**11 else n
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def apply_queue(limit: int | None = None) -> list[dict]:
    """This run's applications: recent, eligible, auto-appliable roles. Tier 1 (target lanes / pursuing) first."""
    import time

    import re

    from .sources import _STRONG_EARLY, relevant_title

    non_us = re.compile(r"\b(india|bengaluru|bangalore|gurugram|hyderabad|pune|brazil|s[aã]o paulo|rio de janeiro|london|uk|united kingdom|"
                        r"ireland|dublin|canada|toronto|vancouver|montreal|mexico|germany|berlin|munich|france|paris|spain|madrid|"
                        r"netherlands|amsterdam|poland|warsaw|portugal|lisbon|porto|switzerland|zurich|sweden|stockholm|denmark|italy|milan|israel|tel aviv|singapore|australia|sydney|japan|tokyo|taipei|taiwan|"
                        r"korea|seoul|philippines|manila|argentina|colombia|chile|johannesburg|south africa|emea|apac|latam)\b", re.I)
    us = re.compile(r"\b(united states|usa|u\.s\.|us|remote[\s,-]*us|new york|nyc)\b", re.I)
    phd = re.compile(r"\b(phd|ph\.d|doctoral|postdoc)\b", re.I)
    defense = re.compile(r"\b(anduril|shield ai|palantir|lockheed|raytheon|northrop|general dynamics|l3harris|bae systems)\b", re.I)

    a = criteria()["applying"]
    limit = limit or a["per_run"]
    cutoff = time.time() - a["backlog_days"] * 86400
    tier1 = set(a["tier1_lanes"])
    with db.connect() as con:
        per_company = {r["company_id"]: r["n"] for r in con.execute(
            "SELECT j.company_id, COUNT(*) n FROM applications x JOIN jobs j ON j.id=x.job_id WHERE x.status!='skipped' GROUP BY j.company_id")}
        rows = con.execute("""SELECT j.id job_id, j.title, j.location, j.url, j.posted_at, j.company_id, c.name company, c.website,
                                     c.lane, c.status cstatus, c.nyc, c.sponsorship, c.why, c.note, c.ats
                              FROM jobs j JOIN companies c ON c.id=j.company_id
                              WHERE j.status IN ('new','surfaced') AND j.ext_id NOT LIKE 'simplify:%'
                                AND c.ats IN ('greenhouse','lever','ashby')
                                AND c.status NOT IN ('unclassified','ruled_out','network_only')
                                AND NOT EXISTS (SELECT 1 FROM applications x WHERE x.job_id=j.id)""").fetchall()
    picked = []
    for r in rows:
        r = dict(r)
        ts = _posted_ts(r.pop("posted_at"))
        if ts is None or ts < cutoff or not relevant_title(r["title"]) or phd.search(r["title"]) or defense.search(r["company"]):
            continue
        loc = r["location"] or ""
        if non_us.search(loc) and not us.search(loc):
            continue  # US / remote-US roles only
        r["tier"] = 1 if (r["lane"] in tier1 or r["cstatus"] == "pursuing") else 2
        if r["tier"] == 2 and not _STRONG_EARLY.search(r["title"]):
            continue  # tier 2 only takes titles that clearly say intern/new grad/early career
        r["posted_days_ago"] = round((time.time() - ts) / 86400, 1)
        picked.append(r)
    picked.sort(key=lambda r: (r["tier"], r["cstatus"] != "pursuing", -r["nyc"], r["posted_days_ago"]))
    out, taken = [], dict(per_company)
    for r in picked:
        if taken.get(r["company_id"], 0) >= a["max_per_company"]:
            continue
        taken[r["company_id"]] = taken.get(r["company_id"], 0) + 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


def apply_skip(job_id: int, reason: str) -> None:
    """Mark a queued role as not worth applying to (so it never comes back), with the reason."""
    with db.connect() as con:
        j = con.execute("SELECT j.url, j.ext_id, c.ats, c.slug FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=?", (job_id,)).fetchone()
        from .apply import application_url
        con.execute("""INSERT INTO applications (job_id, url, status, note) VALUES (?,?,'skipped',?)
                       ON CONFLICT(job_id) DO UPDATE SET status='skipped', note=excluded.note""",
                    (job_id, application_url(j["ats"], j["slug"], j["ext_id"], j["url"]), reason))
        con.execute("UPDATE jobs SET status='skipped' WHERE id=?", (job_id,))
