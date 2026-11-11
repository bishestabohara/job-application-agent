"""Autopilot actions run by scripts/daily.sh (not by Claude): send emails, run applications, email the daily report.

Claude's scheduled session only *prepares* (scores, researches, drafts, fills application answers).
These functions carry out what was prepared, inside the caps in profile/criteria.yaml.
"""

from __future__ import annotations

import json
from datetime import date

from rich.console import Console

from . import apply, db, email_tools
from .config import ROOT, criteria
from .pipeline import due_followups

console = Console()


def paused() -> bool:
    return (ROOT / criteria()["outreach"]["pause_file"]).exists()


# ------------------------------------------------------------------ applications

def apply_extract(job_id: int) -> dict:
    """Read-only: fetch the application form's fields for a job and store them."""
    with db.connect() as con:
        j = con.execute("""SELECT j.*, c.name company, c.ats, c.slug FROM jobs j JOIN companies c ON c.id=j.company_id
                           WHERE j.id=?""", (job_id,)).fetchone()
    if not j:
        raise ValueError(f"no job {job_id}")
    url = apply.application_url(j["ats"], j["slug"], j["ext_id"], j["url"])
    fields = apply.extract_fields(url)
    resume = j["resume_version"] or apply.pick_resume(j["title"])
    with db.connect() as con:
        con.execute("""INSERT INTO applications (job_id, url, fields, resume_version) VALUES (?,?,?,?)
                       ON CONFLICT(job_id) DO UPDATE SET url=excluded.url, fields=excluded.fields""",
                    (job_id, url, json.dumps(fields), resume))
    return {"job_id": job_id, "company": j["company"], "title": j["title"], "url": url,
            "resume_version": resume, "resume_text": f"profile/resumes/{resume}.md", "fields": fields}


def apply_prepare(job_id: int, answers: dict, resume_version: str | None = None) -> list[str]:
    """Validate + store answers. Returns problems (empty list = prepared)."""
    with db.connect() as con:
        a = con.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
        if not a:
            return [f"run apply-fields {job_id} first"]
        problems = apply.validate(json.loads(a["fields"]), answers)
        con.execute("""UPDATE applications SET answers=?, resume_version=COALESCE(?, resume_version),
                       status=?, note=?, prepared_at=CURRENT_TIMESTAMP WHERE job_id=?""",
                    (json.dumps(answers), resume_version, "extracted" if problems else "prepared",
                     "; ".join(problems) or None, job_id))
    return problems


def apply_run(limit: int, dry_run: bool = False, job_id: int | None = None, unattended: bool = False) -> list[dict]:
    if paused() and not dry_run:
        console.print("[yellow]PAUSE file present — not applying")
        return []
    with db.connect() as con:
        q = "SELECT a.*, j.title FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.status='prepared'" + (" AND a.job_id=?" if job_id else "") + " ORDER BY a.prepared_at LIMIT ?"
        rows = con.execute(q, (job_id, limit) if job_id else (limit,)).fetchall()
    apps = [{"job_id": r["job_id"], "url": r["url"], "fields": json.loads(r["fields"]),
             "answers": json.loads(r["answers"]), "resume_version": r["resume_version"] or apply.pick_resume(r["title"])} for r in rows]
    if not apps:
        console.print("no prepared applications")
        return []
    results = apply.run(apps, dry_run=dry_run, unattended=unattended)
    if dry_run:
        return results
    with db.connect() as con:
        for r in results:
            con.execute("UPDATE applications SET status=?, note=?, submitted_at=CASE WHEN ?='submitted' THEN CURRENT_TIMESTAMP END WHERE job_id=?",
                        (r["status"], r["note"], r["status"], r["job_id"]))
            if r["status"] == "submitted":
                con.execute("UPDATE jobs SET status='applied', applied_at=CURRENT_TIMESTAMP WHERE id=?", (r["job_id"],))
    for r in results:
        console.print(f"  job {r['job_id']}: {r['status']} {r['note']}")
    return results


# ------------------------------------------------------------------ email

def _sent_today(con) -> int:
    return con.execute("SELECT COUNT(*) n FROM outreach WHERE channel='email' AND status IN ('sent','replied') AND date(sent_at)=date('now')").fetchone()["n"]


def mark_thread(contact_id: int, status: str) -> None:
    """Record a reply or bounce for a contact; cancels their remaining follow-ups."""
    with db.connect() as con:
        if status == "replied":
            con.execute("UPDATE outreach SET status='replied', replied_at=CURRENT_TIMESTAMP WHERE contact_id=? AND step=0 AND channel='email'", (contact_id,))
        elif status == "bounced":
            con.execute("UPDATE contacts SET email=NULL, email_confidence=NULL WHERE id=?", (contact_id,))
            con.execute("UPDATE outreach SET status='bounced' WHERE contact_id=? AND step=0 AND channel='email'", (contact_id,))
        else:
            raise ValueError("status must be replied or bounced")
        con.execute("UPDATE outreach SET status='skipped' WHERE contact_id=? AND channel='email' AND step>0 AND status='drafted'", (contact_id,))


def threads_to_check() -> list[dict]:
    """Sent first-touch emails still awaiting a reply — check each Gmail thread for replies/bounces."""
    with db.connect() as con:
        return [dict(r) for r in con.execute("""SELECT o.contact_id, ct.name, ct.email, o.gmail_thread_id thread_id FROM outreach o
                                                JOIN contacts ct ON ct.id=o.contact_id WHERE o.step=0 AND o.channel='email'
                                                AND o.status='sent' AND o.gmail_thread_id IS NOT NULL""")]


def check_replies() -> int:
    """Script path (Google Cloud credentials): check every open thread via the Gmail API."""
    n = 0
    for r in threads_to_check():
        if st := email_tools.thread_status(r["thread_id"]):
            mark_thread(r["contact_id"], st)
            n += 1
    return n


def plan_outbox() -> dict:
    """What may be sent right now, after every safety rule: send_mode, PAUSE, daily cap, verified addresses.
    Follow-ups first (warmest), then new first touches. Follow-ups carry the thread to reply into."""
    o = criteria()["outreach"]
    if o["send_mode"] != "auto":
        return {"blocked": "send_mode is not auto", "emails": []}
    if paused():
        return {"blocked": "PAUSE file present", "emails": []}
    out = []
    with db.connect() as con:
        budget = o["max_emails_per_day"] - _sent_today(con)
        for f in (due_followups(con) if o.get("follow_ups", True) else []):
            orig = con.execute("""SELECT o.gmail_thread_id, o.rfc_message_id FROM outreach o JOIN outreach f ON f.contact_id=o.contact_id
                                  WHERE f.id=? AND o.step=0 AND o.channel='email' AND o.status='sent'""", (f["id"],)).fetchone()
            if f["email"] and orig and orig["gmail_thread_id"]:  # LinkedIn-only first touches get follow-ups via the report
                fu = con.execute("SELECT subject, body, step FROM outreach WHERE id=?", (f["id"],)).fetchone()
                out.append({"outreach_id": f["id"], "kind": f"follow_up_{fu['step']}", "name": f["name"], "company": f["company"],
                            "to": f["email"], "subject": fu["subject"], "body": fu["body"],
                            "thread_id": orig["gmail_thread_id"], "in_reply_to": orig["rfc_message_id"]})
        for m in con.execute("""SELECT o.id, o.subject, o.body, o.resume_version, ct.email, ct.name, co.name company FROM outreach o
                                JOIN contacts ct ON ct.id=o.contact_id JOIN companies co ON co.id=ct.company_id
                                WHERE o.channel='email' AND o.step=0 AND o.status IN ('drafted','approved')
                                AND ct.email IS NOT NULL AND COALESCE(ct.email_confidence,0) >= ?
                                -- an email that accompanies an application goes out only once that application is submitted
                                AND (ct.job_id IS NULL OR EXISTS (SELECT 1 FROM applications a WHERE a.job_id=ct.job_id AND a.status='submitted'))
                                ORDER BY o.id""",
                             (o["min_email_confidence"],)):
            out.append({"outreach_id": m["id"], "kind": "first", "name": m["name"], "company": m["company"], "to": m["email"],
                        "subject": m["subject"], "body": m["body"], "thread_id": None, "in_reply_to": None})
    return {"blocked": None, "budget": max(budget, 0), "emails": out[:max(budget, 0)]}


def mark_sent(outreach_id: int, message_id: str, thread_id: str, rfc_message_id: str | None = None) -> None:
    with db.connect() as con:
        con.execute("""UPDATE outreach SET status='sent', sent_at=CURRENT_TIMESTAMP, gmail_message_id=?, gmail_thread_id=?,
                       rfc_message_id=COALESCE(?, rfc_message_id) WHERE id=?""", (message_id, thread_id, rfc_message_id, outreach_id))


def send_due() -> dict:
    """Script path (Google Cloud credentials): send everything plan_outbox() allows."""
    plan = plan_outbox()
    if plan["blocked"]:
        console.print(f"[yellow]not sending: {plan['blocked']}")
        return plan
    replies = check_replies()
    plan = plan_outbox()  # replies may have cancelled follow-ups
    sent, errors = [], []
    for e in plan["emails"]:
        try:
            r = email_tools.send_email(e["to"], e["subject"], e["body"], None, e["thread_id"], e["in_reply_to"])
        except Exception as ex:
            errors.append(f"{e['name']}: {ex.__class__.__name__}")
            continue
        mark_sent(e["outreach_id"], r["id"], r["thread_id"], r["rfc_message_id"])
        sent.append(f"{e['kind']} → {e['name']}")
    console.print(f"email: {len(sent)} sent, {replies} replies/bounces detected, {len(errors)} errors")
    return {"sent": sent, "errors": errors, "replies_detected": replies}


# ------------------------------------------------------------------ daily report

def report_text() -> str:
    today = date.today().isoformat()
    L = [f"Job agent — {today}", ""]
    with db.connect() as con:
        sent = con.execute("""SELECT ct.name, co.name company, o.step, o.subject FROM outreach o JOIN contacts ct ON ct.id=o.contact_id
                              JOIN companies co ON co.id=ct.company_id WHERE o.channel='email' AND date(o.sent_at)=date('now')""").fetchall()
        L.append(f"EMAILS SENT TODAY ({len(sent)})")
        L += [f"  • {s['name']} @ {s['company']} — {'first email' if s['step'] == 0 else f'follow-up {s[2]}'}: {s['subject']}" for s in sent] or ["  (none)"]

        replied = con.execute("""SELECT ct.name, co.name company FROM outreach o JOIN contacts ct ON ct.id=o.contact_id
                                 JOIN companies co ON co.id=ct.company_id WHERE o.status='replied' AND date(o.replied_at)=date('now')""").fetchall()
        if replied:
            L += ["", f"🎉 REPLIES ({len(replied)}) — answer these yourself!"] + [f"  • {r['name']} @ {r['company']}" for r in replied]

        apps = con.execute("""SELECT a.status, a.note, j.title, c.name company, a.url FROM applications a JOIN jobs j ON j.id=a.job_id
                              JOIN companies c ON c.id=j.company_id WHERE a.status IN ('prepared','needs_you')
                              OR date(a.submitted_at)=date('now') ORDER BY a.status""").fetchall()
        ready = [a for a in apps if a["status"] == "prepared"]
        L += ["", f"APPLICATIONS READY TO SUBMIT ({len(ready)}) — run:  ./ja apply-run"]
        L += [f"  • {a['company']} — {a['title']}" for a in ready] or ["  (none)"]
        for label, st in (("SUBMITTED TODAY", "submitted"), ("NEED YOU (captcha/form issue) — at your laptop run: ./ja apply-run --retry", "needs_you")):
            rows = [a for a in apps if a["status"] == st]
            if rows:
                L += ["", f"{label} ({len(rows)})"] + [f"  • {a['company']} — {a['title']}  {a['url'] if st == 'needs_you' else ''}" for a in rows]

        li = con.execute("""SELECT o.id, o.body, ct.name, ct.title, ct.linkedin_url, co.name company FROM outreach o
                            JOIN contacts ct ON ct.id=o.contact_id JOIN companies co ON co.id=ct.company_id
                            WHERE o.channel='linkedin' AND o.step=0 AND o.status='drafted' ORDER BY o.id""").fetchall()
        L += ["", f"LINKEDIN NOTES TO PASTE ({len(li)}) — after sending: ./ja sent <id>"]
        for x in li:
            L += [f"  [{x['id']}] {x['name']} ({x['title']}, {x['company']}) {x['linkedin_url'] or '(search their name)'}", f"      {x['body']}"]

        no_email = con.execute("""SELECT COUNT(*) n FROM outreach o JOIN contacts ct ON ct.id=o.contact_id
                                  WHERE o.channel='email' AND o.step=0 AND o.status='drafted' AND ct.email IS NULL""").fetchone()["n"]
        if no_email:
            L += ["", f"({no_email} people had no verified email — reached via LinkedIn note only)"]

        s = con.execute("""SELECT (SELECT COUNT(*) FROM outreach WHERE step=0 AND status IN ('sent','replied')) touched,
                                  (SELECT COUNT(DISTINCT contact_id) FROM outreach WHERE status='replied') replies,
                                  (SELECT COUNT(*) FROM jobs WHERE status='applied') applied""").fetchone()
        L += ["", f"TOTALS  people reached: {s['touched']} · replies: {s['replies']} · applications: {s['applied']}",
              "", "Pause everything: touch PAUSE in the project folder. Resume: delete it."]
    return "\n".join(L)


def send_report() -> None:
    text = report_text()
    path = ROOT / "out" / f"report-{date.today().isoformat()}.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    if email_tools.gmail_ready():
        email_tools.send_email(email_tools.MY_ADDRESS, f"Job agent report — {date.today():%b %d}", text)
        console.print("report emailed to you")
    console.print(f"report: {path}")
