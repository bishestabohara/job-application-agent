"""`jobagent` CLI. Run `python -m jobagent --help`."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

import json

from . import autopilot, db, email_tools, notion_sync, pipeline, session

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def init():
    """Create the database and load data/companies.yaml (safe to re-run; keeps your edits)."""
    console.print(f"loaded {db.init()} seed companies")


def _json(obj) -> None:
    print(json.dumps(obj, indent=1, default=str))


@app.command()
def daily():
    """Free morning run (no Claude calls): pull new postings from company boards + SimplifyJobs lists."""
    db.init()
    pipeline.fetch_simplify()
    pipeline.fetch_jobs()


@app.command()
def auto(discover: bool = typer.Option(True, help="Also source new companies with web search")):
    """Unattended mode via the Claude API (needs ANTHROPIC_API_KEY; costs per run): fetch → score → discover → draft → digest."""
    db.init()
    pipeline.fetch_jobs()
    pipeline.score_jobs()
    if discover:
        pipeline.discover_companies()
        pipeline.fetch_jobs()
        pipeline.score_jobs()
    pipeline.research_and_draft()
    pipeline.write_digest()


@app.command()
def fetch():
    """Pull postings from all job boards."""
    pipeline.fetch_jobs()


@app.command()
def score(limit: int = 150):
    """Score unscored postings with Claude."""
    pipeline.score_jobs(limit)


@app.command()
def discover():
    """Find new mission-fit companies with web search."""
    pipeline.discover_companies()


@app.command()
def outreach():
    """Research people at top companies and draft messages (respects daily cap)."""
    pipeline.research_and_draft()


@app.command()
def digest():
    """Write today's digest to out/."""
    pipeline.write_digest()


@app.command()
def gmail():
    """Create Gmail drafts for every approved email (you still press send)."""
    pipeline.push_gmail_drafts()


def _set_outreach(ids: list[int], status: str, stamp: str | None = None):
    with db.connect() as con:
        for i in ids:
            extra = f", {stamp}=CURRENT_TIMESTAMP" if stamp else ""
            con.execute(f"UPDATE outreach SET status=?{extra} WHERE id=?", (status, i))
    console.print(f"{len(ids)} message(s) → {status}")


@app.command()
def approve(ids: list[int]):
    """Approve outreach drafts (emails go to Gmail on `jobagent gmail`)."""
    _set_outreach(ids, "approved")


@app.command()
def sent(ids: list[int]):
    """Mark messages as sent (starts the follow-up clock)."""
    _set_outreach(ids, "sent", "sent_at")


@app.command()
def skip(ids: list[int]):
    """Skip outreach drafts you don't want to send."""
    _set_outreach(ids, "skipped")


@app.command()
def replied(contact_name: str):
    """Record that someone replied — stops their follow-ups."""
    with db.connect() as con:
        row = con.execute("SELECT id FROM contacts WHERE name LIKE ?", (f"%{contact_name}%",)).fetchone()
        if not row:
            raise typer.BadParameter(f"no contact matching {contact_name!r}")
        con.execute("UPDATE outreach SET status='replied', replied_at=CURRENT_TIMESTAMP WHERE contact_id=? AND step=0", (row["id"],))
        con.execute("UPDATE outreach SET status='skipped' WHERE contact_id=? AND step>0 AND status='drafted'", (row["id"],))
    console.print("🎉 marked replied; follow-ups cancelled")


@app.command("set-email")
def set_email(contact_name: str, email: str):
    """Add or fix a contact's email."""
    with db.connect() as con:
        n = con.execute("UPDATE contacts SET email=?, email_confidence=100 WHERE name LIKE ?", (email, f"%{contact_name}%")).rowcount
    console.print(f"updated {n} contact(s)")


@app.command()
def job(job_id: int, status: str = typer.Argument(..., help="applied | skipped | surfaced")):
    """Update a job's status."""
    with db.connect() as con:
        con.execute("UPDATE jobs SET status=?, applied_at=CASE WHEN ?='applied' THEN CURRENT_TIMESTAMP ELSE applied_at END WHERE id=?",
                    (status, status, job_id))
    console.print(f"job {job_id} → {status}")


@app.command()
def company(name: str, status: str = typer.Argument(..., help="pursuing | evaluate | target | network_only | ruled_out")):
    """Change a company's status (network_only: outreach but no job fetch; ruled_out: nothing)."""
    with db.connect() as con:
        n = con.execute("UPDATE companies SET status=? WHERE name LIKE ?", (status, f"%{name}%")).rowcount
    console.print(f"updated {n} company(ies)")


@app.command()
def status():
    """Pipeline overview."""
    with db.connect() as con:
        t = Table(title="Top open roles")
        for col in ("id", "company", "title", "fit", "NYC", "sponsorship", "status"):
            t.add_column(col)
        for r in con.execute("""SELECT j.id, c.name, j.title, j.fit_score, j.nyc, j.sponsorship, j.status FROM jobs j
                                JOIN companies c ON c.id=j.company_id WHERE j.status IN ('new','surfaced','applied') AND j.fit_score IS NOT NULL
                                ORDER BY j.fit_score DESC LIMIT 20"""):
            t.add_row(str(r[0]), r[1], r[2], str(r[3]), "✓" if r[4] else "", r[5] or "", r[6])
        console.print(t)
        o = con.execute("SELECT status, COUNT(*) n FROM outreach WHERE step=0 GROUP BY status").fetchall()
        console.print("outreach (first touches): " + ", ".join(f"{r['status']}={r['n']}" for r in o) or "none yet")


# ---------------------------------------------------------------- /job-session helpers (JSON in/out)

@app.command()
def today():
    """JSON: quotas left, roles to apply to, drafts awaiting review, follow-ups due."""
    _json(session.today())


@app.command()
def queue(limit: int = 15, chars: int = 2500, offset: int = 0):
    """JSON: unscored postings (trimmed descriptions) for scoring. Use --offset to split parallel batches."""
    _json(session.queue(limit, chars, offset))


@app.command()
def unclassified(limit: int = 150, offset: int = 0):
    """JSON: companies (from SimplifyJobs) awaiting a mission verdict."""
    _json(session.unclassified(limit, offset))


@app.command()
def classify(path: str):
    """Save mission verdicts: JSON list of {id, lane, stepping_stone, nyc, website, why}."""
    _json(session.classify(path))


_PROGRESS = [
    ("companies left to sort by mission", "SELECT COUNT(*) FROM companies WHERE status='unclassified'"),
    ("applications prepared today", "SELECT COUNT(*) FROM applications WHERE status='prepared' AND date(prepared_at)=date('now')"),
    ("roles skipped (not a fit)", "SELECT COUNT(*) FROM applications WHERE status='skipped'"),
    ("applications submitted today", "SELECT COUNT(*) FROM applications WHERE date(submitted_at)=date('now')"),
    ("needs you (CAPTCHA/form)", "SELECT COUNT(*) FROM applications WHERE status='needs_you'"),
    ("people found + emails written today", "SELECT COUNT(*) FROM contacts WHERE date(created_at)=date('now')"),
    ("emails sent today", "SELECT COUNT(*) FROM outreach WHERE channel='email' AND date(sent_at)=date('now')"),
]


@app.command()
def progress(watch: bool = typer.Option(False, "--watch", "-w", help="refresh every 20s until Ctrl+C")):
    """What the current run has done so far (safe to run anytime, from a second terminal)."""
    import subprocess
    import time as _t

    def running(pattern: str) -> bool:
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0

    while True:
        step = ("Claude preparing packets" if running("job-autopilot prepare") else
                "browser submitting applications" if running("apply-run") else
                "Claude sending emails + updating Notion" if running("job-autopilot deliver") else "not running")
        console.print(f"[bold]{_t.strftime('%H:%M:%S')}  step: {step}[/bold]")
        with db.connect() as con:
            for label, sql in _PROGRESS:
                console.print(f"  {label:<38} {con.execute(sql).fetchone()[0]}")
        if not watch:
            break
        _t.sleep(20)
        console.print()


@app.command("apply-check")
def apply_check(job: int = typer.Option(None, help="just this job id"), all_fields: bool = typer.Option(True, help="show filled fields too")):
    """Fill each prepared application in a hidden browser (NEVER submits), then list every field + value and save screenshots."""
    import json as _j

    from . import apply as _a
    from .config import OUT_DIR

    with db.connect() as con:
        q = """SELECT a.*, j.title, c.name company FROM applications a JOIN jobs j ON j.id=a.job_id JOIN companies c ON c.id=j.company_id
               WHERE a.status IN ('prepared','needs_you')""" + (" AND a.job_id=?" if job else "")
        rows = con.execute(q, (job,) if job else ()).fetchall()
    apps = [{"job_id": r["job_id"], "url": r["url"], "fields": _j.loads(r["fields"]), "answers": _j.loads(r["answers"] or "{}"),
             "resume_version": r["resume_version"] or _a.pick_resume(r["title"]), "title": r["title"], "company": r["company"]} for r in rows]
    for app_, rep in zip(apps, _a.check(apps, OUT_DIR / "applications")):
        # one question built as several tick boxes → one row (answered if any box is ticked)
        merged, seen = [], {}
        for f in rep["fields"]:
            k = (f["label"], f["kind"]) if f["kind"] in ("checkbox", "radio") else None
            if k and k in seen:
                prev = seen[k]
                prev["value"] = ", ".join(v for v in (prev["value"], f["value"]) if v)
                prev["required"] = prev["required"] or f["required"]
                continue
            if k:
                seen[k] = f
            if f["kind"] == "file" and not f["cover_letter"] and rep["resume_ok"] and not f["value"]:
                f["value"], f["disabled"] = rep.get("resume_file", "resume"), False
            merged.append(f)
        rep["fields"] = merged
        empty_req = [f for f in rep["fields"] if f["required"] and not f["value"] and not f["disabled"] and not f["cover_letter"]]
        empty_opt = [f for f in rep["fields"] if not f["required"] and not f["value"] and not f["disabled"] and not f["cover_letter"]]
        ok = rep["resume_ok"] and not empty_req and not rep["error"]
        console.print(f"\n[bold]{'✅' if ok else '⚠️ '} [{app_['job_id']}] {app_['company']} — {app_['title']}[/bold]")
        console.print(f"   resume: {rep.get('resume_file', app_['resume_version'])} {'✓ attached' if rep['resume_ok'] else '✗ NOT attached'}")
        console.print(f"   screenshot: {rep['shot']}")
        if rep["error"]:
            console.print(f"   [red]error: {rep['error']}")
        t = Table(show_header=True, box=None, padding=(0, 1))
        for col in ("", "field", "value"):
            t.add_column(col)
        for f in rep["fields"]:
            if f["cover_letter"]:
                mark, val = "—", "(cover letter: skipped on purpose)"
            elif f["disabled"]:
                mark, val = "—", "(disabled by the form)"
            elif f["value"]:
                if not all_fields:
                    continue
                mark, val = "✓", f["value"][:70] + ("…" if len(f["value"]) > 70 else "")
            else:
                mark, val = ("✗" if f["required"] else "○"), ("EMPTY — required" if f["required"] else "empty (optional)")
            t.add_row(mark, (f["label"] or f["kind"])[:55], val)
        console.print(t)
        console.print(f"   → {len(empty_req)} required empty, {len(empty_opt)} optional empty")


@app.command("apply-queue")
def apply_queue(limit: int = typer.Option(None, help="default: applying.per_run")):
    """JSON: this run's applications (recent, eligible, tier 1 targets first, max 2 per company)."""
    _json(session.apply_queue(limit))


@app.command("apply-skip")
def apply_skip(job_id: int, reason: str):
    """Don't apply to this role (e.g. needs 3+ years, not SWE/PM); it won't be queued again."""
    session.apply_skip(job_id, reason)
    console.print(f"job {job_id} skipped: {reason}")


@app.command("save-scores")
def save_scores(path: str):
    """Save scores from a JSON file (list of JobFit + job_id), or '-' for stdin."""
    console.print(f"saved {session.save_scores(path)} scores")


@app.command()
def targets(limit: int = 4):
    """JSON: next companies to research for outreach, with open roles."""
    _json(session.targets(limit))


@app.command("add-companies")
def add_companies(path: str):
    """Add discovered companies from JSON (list of DiscoveredCompany); auto-detects job boards."""
    _json(session.add_companies(path))


@app.command("save-outreach")
def save_outreach(path: str):
    """Save researched contact(s) + drafts from JSON (see skill for shape)."""
    _json(session.save_outreach(path))


@app.command("no-contacts")
def no_contacts(company_ids: list[int]):
    """Mark companies researched when no good person was found."""
    session.mark_companies_researched(company_ids)
    console.print(f"{len(company_ids)} company(ies) marked researched")


@app.command()
def drafts():
    """JSON: first-touch drafts awaiting review, with full text."""
    with db.connect() as con:
        _json([dict(r) for r in con.execute("""
            SELECT o.id, o.channel, o.subject, o.body, o.resume_version, o.status, ct.name, ct.title, ct.email, ct.linkedin_url,
                   ct.hook, ct.evidence_url, co.name company
            FROM outreach o JOIN contacts ct ON ct.id=o.contact_id JOIN companies co ON co.id=ct.company_id
            WHERE o.step=0 AND o.status IN ('drafted','approved') ORDER BY ct.id, o.channel""")])


@app.command()
def edit(outreach_id: int, body_file: str, subject: str = typer.Option(None)):
    """Replace a draft's text from a file (after edits in the session)."""
    session.edit_outreach(outreach_id, open(body_file).read().strip(), subject)
    console.print(f"draft {outreach_id} updated")


# ---------------------------------------------------------------- autopilot: applications

@app.command("apply-fields")
def apply_fields(job_id: int):
    """JSON: read a job's application form fields (read-only, headless)."""
    _json(autopilot.apply_extract(job_id))


@app.command("apply-prepare")
def apply_prepare(job_id: int, answers_file: str, resume: str = typer.Option(None, help="override resume version")):
    """Store answers for a job's form (JSON {field key: value}). Prints problems, if any."""
    problems = autopilot.apply_prepare(job_id, session._load(answers_file), resume)
    _json({"job_id": job_id, "prepared": not problems, "problems": problems})


@app.command("apply-run")
def apply_run(limit: int = 10, dry_run: bool = typer.Option(False, help="fill + screenshot, don't submit"),
              job: int = typer.Option(None, help="just this job id"),
              unattended: bool = typer.Option(False, help="scheduled run: skip CAPTCHA challenges instead of waiting"),
              retry: bool = typer.Option(False, help="re-queue 'needs you' applications first (do this when you're at the laptop)")):
    """Open a visible browser, fill + submit prepared applications. Solve any CAPTCHA that appears."""
    if retry:
        with db.connect() as con:
            n = con.execute("UPDATE applications SET status='prepared' WHERE status='needs_you'").rowcount
        console.print(f"re-queued {n} application(s)")
    autopilot.apply_run(limit, dry_run, job, unattended)


@app.command()
def applications():
    """Application queue and results."""
    with db.connect() as con:
        t = Table(title="Applications")
        for col in ("job", "company", "title", "status", "note"):
            t.add_column(col)
        for r in con.execute("""SELECT a.job_id, c.name, j.title, a.status, a.note FROM applications a JOIN jobs j ON j.id=a.job_id
                                JOIN companies c ON c.id=j.company_id ORDER BY a.status, a.job_id"""):
            t.add_row(str(r[0]), r[1], r[2], r[3], (r[4] or "")[:60])
        console.print(t)


# ---------------------------------------------------------------- autopilot: email + report

@app.command("send-due")
def send_due():
    """Send new first-touch emails + due follow-ups (send_mode=auto, daily cap, PAUSE file respected)."""
    _json(autopilot.send_due())


@app.command("find-email")
def find_email(full_name: str, website: str):
    """JSON: look up a work email via Hunter (needs HUNTER_API_KEY)."""
    email, conf = email_tools.find_email(full_name, website)
    _json({"email": email, "confidence": conf})


@app.command()
def report(send: bool = typer.Option(True, help="email it to yourself")):
    """Daily report: emails sent, replies, applications, LinkedIn notes to paste."""
    if send:
        autopilot.send_report()
    else:
        print(autopilot.report_text())


# ---------------------------------------------------------------- connector path (used by /job-autopilot deliver)

@app.command()
def outbox():
    """JSON: emails allowed to go out right now (caps, PAUSE, verified addresses applied). Send each, then mark-sent."""
    _json(autopilot.plan_outbox())


@app.command("mark-sent")
def mark_sent(outreach_id: int, message_id: str, thread_id: str):
    """Record an email sent via the Gmail connector (ids from its response)."""
    autopilot.mark_sent(outreach_id, message_id, thread_id)
    console.print(f"outreach {outreach_id} → sent")


@app.command()
def threads():
    """JSON: sent first emails still awaiting a reply (check each thread)."""
    _json(autopilot.threads_to_check())


@app.command("mark-thread")
def mark_thread(contact_id: int, status: str = typer.Argument(..., help="replied | bounced")):
    """Record a reply or bounce; cancels that person's follow-ups."""
    autopilot.mark_thread(contact_id, status)
    console.print(f"contact {contact_id} → {status}")


@app.command("notion-changes")
def notion_changes():
    """JSON: Notion rows to create/update (already in Notion property format)."""
    _json(notion_sync.changes())


@app.command("notion-saved")
def notion_saved(path: str):
    """Record Notion page ids: JSON list of {kind, tracker_id, page_id}."""
    console.print(f"recorded {notion_sync.saved(session._load(path))} Notion page ids")


@app.command("notion-report")
def notion_report():
    """JSON: today's Daily Reports page (properties + content)."""
    _json(notion_sync.daily_report_page())


if __name__ == "__main__":
    app()
