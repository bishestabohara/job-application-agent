"""Change feed for the Notion tracker. The morning Claude run applies it with the Notion connector.

`changes()` → rows to create/update, already mapped to Notion property names; `saved()` records the page ids
Claude got back so the next run updates instead of duplicating.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta

import yaml

from . import db
from .config import DATA_DIR, criteria

_APP_STATUS = {"prepared": "Ready", "extracted": "Ready", "submitted": "Submitted", "needs_you": "Needs you",
               "failed": "Needs you", "skipped": "Skipped"}


def config() -> dict:
    return yaml.safe_load((DATA_DIR / "notion.yaml").read_text())


def _hash(props: dict) -> str:
    return hashlib.sha1(json.dumps(props, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _clean(props: dict) -> dict:
    return {k: v for k, v in props.items() if v not in (None, "")}


def _application_rows(con) -> list[tuple[int, str | None, str | None, dict]]:
    """Every job with an application record (prepared, submitted, needs you, skipped)."""
    rows = con.execute("""
        SELECT j.id, j.title, j.location, j.url, j.fit_score, j.nyc, j.sponsorship, j.resume_version, j.why, j.status jstatus,
               j.notion_page_id, j.notion_hash, c.name company, c.lane, c.ats, a.status astatus, a.submitted_at, a.url apply_url
        FROM jobs j JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id
        WHERE a.job_id IS NOT NULL OR j.notion_page_id IS NOT NULL""").fetchall()
    out = []
    for r in rows:
        status = "Closed" if r["jstatus"] == "closed" else "Submitted" if r["jstatus"] == "applied" else \
                 _APP_STATUS.get(r["astatus"], "Apply manually")
        props = _clean({
            "Role": r["title"], "Company": r["company"], "Status": status, "Fit": r["fit_score"],
            "NYC": "__YES__" if r["nyc"] else "__NO__", "Sponsorship": r["sponsorship"], "Lane": r["lane"],
            "Resume": r["resume_version"], "Location": (r["location"] or "")[:200], "Why": r["why"],
            "userDefined:Link": r["apply_url"] or r["url"], "Tracker ID": r["id"],
            "date:Submitted:start": (r["submitted_at"] or "")[:10] or None,
        })
        out.append((r["id"], r["notion_page_id"], r["notion_hash"], props))
    return out


def _outreach_rows(con) -> list[tuple[int, str | None, str | None, dict]]:
    d1, d2 = criteria()["outreach"]["follow_up_days"]
    out = []
    for c in con.execute("""SELECT ct.*, co.name company, j.title role FROM contacts ct JOIN companies co ON co.id=ct.company_id
                            LEFT JOIN jobs j ON j.id=ct.job_id""").fetchall():
        msgs = {(m["channel"], m["step"]): m for m in con.execute("SELECT * FROM outreach WHERE contact_id=?", (c["id"],))}
        email0, li0 = msgs.get(("email", 0)), msgs.get(("linkedin", 0))
        f1, f2 = msgs.get(("email", 1)), msgs.get(("email", 2))
        sent_dates = [m["sent_at"] for m in msgs.values() if m["sent_at"]]
        first = min(sent_dates)[:10] if sent_dates else None
        if email0 and email0["status"] == "replied":
            status = "Replied"
        elif email0 and email0["status"] == "bounced":
            status = "Bounced"
        elif f2 and f2["status"] == "sent":
            status = "Follow-up 2 sent"
        elif f1 and f1["status"] == "sent":
            status = "Follow-up 1 sent"
        elif email0 and email0["status"] == "sent":
            status = "Emailed"
        elif li0 and li0["status"] == "sent":
            status = "LinkedIn sent"
        elif li0 and li0["status"] == "drafted" and not c["email"]:
            status = "LinkedIn to paste"
        elif all(m["status"] == "skipped" for m in msgs.values()) and msgs:
            status = "Skipped"
        else:
            status = "Drafted"
        nxt = None
        if first and status in ("Emailed", "LinkedIn sent", "Follow-up 1 sent"):
            nxt = (date.fromisoformat(first) + timedelta(days=d2 if status == "Follow-up 1 sent" else d1)).isoformat()
        props = _clean({
            "Person": c["name"], "Company": c["company"], "Title": c["title"], "Status": status,
            "Channel": json.dumps([ch for ch in ("email", "linkedin") if (ch, 0) in msgs]),
            "Email": c["email"], "LinkedIn": c["linkedin_url"], "Hook": ((f"Applied: {c['role']} · " if c["role"] else "") + (c["hook"] or ""))[:500],
            "date:First contact:start": first, "date:Next follow-up:start": nxt, "Tracker ID": c["id"],
        })
        out.append((c["id"], c["notion_page_id"], c["notion_hash"], props))
    return out


def changes() -> dict:
    cfg = config()
    feed = {"applications": {"data_source_id": cfg["applications_data_source"], "create": [], "update": []},
            "outreach": {"data_source_id": cfg["outreach_data_source"], "create": [], "update": []}}
    with db.connect() as con:
        for kind, rows in (("applications", _application_rows(con)), ("outreach", _outreach_rows(con))):
            for tid, page_id, old_hash, props in rows:
                if _hash(props) == old_hash:
                    continue
                if page_id:
                    feed[kind]["update"].append({"tracker_id": tid, "page_id": page_id, "properties": props})
                else:
                    feed[kind]["create"].append({"tracker_id": tid, "properties": props})
    return feed


def saved(entries: list[dict]) -> int:
    """entries: [{kind: applications|outreach, tracker_id, page_id}] — call after creating/updating pages."""
    with db.connect() as con:
        rows = {"applications": dict((t, p) for t, _, _, p in _application_rows(con)),
                "outreach": dict((t, p) for t, _, _, p in _outreach_rows(con))}
        for e in entries:
            table = "jobs" if e["kind"] == "applications" else "contacts"
            props = rows[e["kind"]].get(e["tracker_id"])
            con.execute(f"UPDATE {table} SET notion_page_id=?, notion_hash=? WHERE id=?",
                        (e["page_id"], _hash(props) if props else None, e["tracker_id"]))
    return len(entries)


def daily_report_page() -> dict:
    """Properties + body for today's Daily Reports page."""
    from .autopilot import report_text

    with db.connect() as con:
        n = lambda q: con.execute(q).fetchone()[0]  # noqa: E731
        props = {
            "Day": f"{date.today():%a %b %d}", "date:Date:start": date.today().isoformat(),
            "Emails sent": n("SELECT COUNT(*) FROM outreach WHERE channel='email' AND date(sent_at)=date('now')"),
            "Applications submitted": n("SELECT COUNT(*) FROM applications WHERE date(submitted_at)=date('now')"),
            "Needs you": n("SELECT COUNT(*) FROM applications WHERE status='needs_you'"),
            "Replies": n("SELECT COUNT(*) FROM outreach WHERE status='replied' AND date(replied_at)=date('now')"),
            "Companies added": n("SELECT COUNT(*) FROM companies WHERE date(created_at)=date('now') AND status!='unclassified'"),
        }
    return {"data_source_id": config()["daily_reports_data_source"], "properties": props,
            "content": "```\n" + report_text() + "\n```", "generated_at": datetime.now().isoformat(timespec="minutes")}
