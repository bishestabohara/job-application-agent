"""Daily steps. Each is idempotent and safe to re-run."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from rich.console import Console

from . import agents, db, email_tools
from .config import OUT_DIR, criteria
from .llm import LLMError
from . import sources
from .sources import FETCHERS, probe, relevant_title

console = Console()


def fetch_jobs() -> int:
    """Pull postings from every tracked board; store new, title-relevant ones. Marks vanished ones closed."""
    new = 0
    with db.connect() as con:
        companies = con.execute("SELECT * FROM companies WHERE ats IN ('greenhouse','lever','ashby') AND status NOT IN ('ruled_out','network_only','unclassified','stepping_stone') AND COALESCE(lane,'') NOT IN ('non_mission','avoid')").fetchall()
        for c in companies:
            try:
                jobs = FETCHERS[c["ats"]](c["slug"])
            except Exception as e:  # one broken board shouldn't stop the run
                console.print(f"[yellow]  {c['name']}: fetch failed ({e.__class__.__name__})")
                continue
            live = set()
            for j in jobs:
                if not relevant_title(j.title):
                    continue
                live.add(j.ext_id)
                cur = con.execute(
                    "INSERT OR IGNORE INTO jobs (company_id, ext_id, title, location, url, description, posted_at) VALUES (?,?,?,?,?,?,?)",
                    (c["id"], j.ext_id, j.title, j.location, j.url, j.description, j.posted_at))
                new += cur.rowcount
            open_ids = [r["ext_id"] for r in con.execute("SELECT ext_id FROM jobs WHERE company_id=? AND status IN ('new','surfaced')", (c["id"],))]
            for gone in set(open_ids) - live:
                con.execute("UPDATE jobs SET status='closed' WHERE company_id=? AND ext_id=?", (c["id"], gone))
    console.print(f"fetch: {new} new relevant postings across {len(companies)} boards")
    return new


def fetch_simplify() -> int:
    """SimplifyJobs internship + new-grad lists. New companies arrive as 'unclassified' until Claude sorts them by mission."""
    cfg = criteria().get("sources", {}).get("simplify", {})
    if not cfg.get("enabled", True):
        return 0
    try:
        postings = sources.simplify(cfg.get("lookback_days", 30))
    except Exception as e:
        console.print(f"[yellow]  simplify: fetch failed ({e.__class__.__name__})")
        return 0
    new, live = 0, set()
    with db.connect() as con:
        known = {r["name"].lower(): dict(r) for r in con.execute("SELECT id, name, ats, slug, status FROM companies")}
        for x in postings:
            name = x["company_name"].strip()
            ats = sources.parse_ats_url(x["url"])
            c = known.get(name.lower())
            if not c:
                cid = db.upsert_company(con, {"name": name, "status": "unclassified",
                                              "ats": ats[0] if ats else None, "slug": ats[1] if ats else None}, source="simplify")
                c = known[name.lower()] = {"id": cid, "name": name, "ats": ats and ats[0], "slug": ats and ats[1], "status": "unclassified"}
            elif ats and not c["ats"] or c["ats"] == "none" and ats:
                con.execute("UPDATE companies SET ats=?, slug=? WHERE id=?", (ats[0], ats[1], c["id"]))
                c["ats"], c["slug"] = ats[0], ats[1]
            # Same id as the board fetcher uses when the link is Greenhouse/Lever/Ashby → no duplicates.
            ext_id = ats[2] if ats else f"simplify:{x['id']}"
            live.add((c["id"], ext_id))
            desc = (f"[From SimplifyJobs {x['kind']} list] Category: {x.get('category')}. Terms: {', '.join(x.get('terms') or []) or '-'}. "
                    f"Degrees: {', '.join(x.get('degrees') or []) or '-'}. Sponsorship (Simplify): {x.get('sponsorship')}. "
                    "No full description available — judge from title, company, and terms.")
            cur = con.execute("INSERT OR IGNORE INTO jobs (company_id, ext_id, title, location, url, description, posted_at) VALUES (?,?,?,?,?,?,?)",
                              (c["id"], ext_id, x["title"], ", ".join(x.get("locations") or []), x["url"].replace("?embed=true", ""),
                               desc, str(x.get("date_posted"))))
            new += cur.rowcount
        # Simplify postings that dropped off the active list are closed.
        for r in con.execute("SELECT id, company_id, ext_id FROM jobs WHERE ext_id LIKE 'simplify:%' AND status IN ('new','surfaced')").fetchall():
            if (r["company_id"], r["ext_id"]) not in live:
                con.execute("UPDATE jobs SET status='closed' WHERE id=?", (r["id"],))
    console.print(f"simplify: {new} new postings ({len(postings)} matching SWE/PM/AI in the last {cfg.get('lookback_days', 30)} days)")
    return new


def score_jobs(limit: int = 150) -> int:
    with db.connect() as con:
        rows = con.execute("""SELECT j.*, c.name, c.lane, c.sponsorship AS c_sponsorship, c.note FROM jobs j
                              JOIN companies c ON c.id=j.company_id WHERE j.scored_at IS NULL AND j.status='new' LIMIT ?""", (limit,)).fetchall()
    done = 0
    for r in rows:
        company = {"name": r["name"], "lane": r["lane"], "sponsorship": r["c_sponsorship"], "note": r["note"]}
        try:
            f = agents.score_job(company, r["title"], r["location"], r["description"] or "")
        except LLMError as e:
            console.print(f"[yellow]  score failed for {r['name']} / {r['title']}: {e}")
            continue
        # A company-level confirmed "no sponsorship" beats a silent posting.
        spons = "confirmed_no" if r["c_sponsorship"] == "confirmed_no" else f.sponsorship
        score = f.fit_score if (f.level_ok and f.role_type != "not_target") else min(f.fit_score, 25)
        with db.connect() as con:
            con.execute("""UPDATE jobs SET fit_score=?, role_type=?, resume_version=?, sponsorship=?, nyc=?, why=?, red_flags=?,
                           scored_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (score, f.role_type, f.resume_version, spons, int(f.nyc), f.why, json.dumps(f.red_flags), r["id"]))
        done += 1
    console.print(f"score: {done} postings scored")
    return done


def discover_companies() -> int:
    n = criteria()["discovery"]["new_companies_per_run"]
    with db.connect() as con:
        existing = [r["name"] for r in con.execute("SELECT name FROM companies")]
    try:
        found = agents.discover(existing, n)
    except LLMError as e:
        console.print(f"[yellow]discover failed: {e}")
        return 0
    added = 0
    with db.connect() as con:
        for c in found:
            if c.name.lower() in {e.lower() for e in existing}:
                continue
            board = probe(c.name)
            row = c.model_dump() | {"ats": board[0] if board else "none", "slug": board[1] if board else None,
                                    "verify": bool(board), "note": f"found via {c.evidence_url}"}
            db.upsert_company(con, row, source="discover")
            added += 1
    console.print(f"discover: {added} new companies ({sum(1 for _ in found)} proposed)")
    return added


def _open_roles(con, company_id: int) -> list[str]:
    return [f"{r['title']} ({r['location']})" for r in con.execute(
        "SELECT title, location FROM jobs WHERE company_id=? AND status IN ('new','surfaced') AND fit_score>=? ORDER BY fit_score DESC LIMIT 3",
        (company_id, criteria()["scoring"]["min_fit_to_surface"]))]


def pick_targets(con, limit: int) -> list:
    """Companies not yet researched for outreach.
    Priority: actively pursuing > has a strong open role > healthcare lane > NYC > everything else."""
    return con.execute("""
        SELECT c.*, COALESCE(MAX(j.fit_score),0) best FROM companies c LEFT JOIN jobs j ON j.company_id=c.id AND j.status IN ('new','surfaced')
        WHERE c.contacts_searched_at IS NULL AND c.status NOT IN ('ruled_out','unclassified') AND COALESCE(c.lane,'') NOT IN ('other','non_mission')
        GROUP BY c.id
        ORDER BY (c.status='pursuing') DESC, best DESC, (c.lane='healthcare') DESC, c.nyc DESC, c.id
        LIMIT ?""", (limit,)).fetchall()


def research_and_draft() -> int:
    """Pick today's outreach targets, find people, draft first-touch email + LinkedIn note. Respects the daily cap."""
    o = criteria()["outreach"]
    budget = o["per_day_new_contacts"]
    with db.connect() as con:
        already_today = con.execute("SELECT COUNT(*) n FROM contacts WHERE date(created_at)=date('now')").fetchone()["n"]
        budget -= already_today
        companies = pick_targets(con, max(budget, 0))
    drafted = 0
    for c in companies:
        if budget <= 0:
            break
        c = dict(c)
        try:
            people = agents.find_contacts(c, min(o["contacts_per_company"], budget), already=[])
        except LLMError as e:
            console.print(f"[yellow]  contacts failed for {c['name']}: {e}")
            continue
        with db.connect() as con:
            con.execute("UPDATE companies SET contacts_searched_at=CURRENT_TIMESTAMP WHERE id=?", (c["id"],))
            roles = _open_roles(con, c["id"])
        for p in people[:budget]:
            email, conf = email_tools.find_email(p.name, c["website"])
            with db.connect() as con:
                cur = con.execute("""INSERT OR IGNORE INTO contacts (company_id, name, title, linkedin_url, email, email_confidence, hook, evidence_url, why_this_person)
                                     VALUES (?,?,?,?,?,?,?,?,?)""",
                                  (c["id"], p.name, p.title, p.linkedin_url, email, conf, p.hook, p.evidence_url, p.why_this_person))
                if not cur.rowcount:
                    continue
                contact_id = cur.lastrowid
            try:
                d = agents.draft_outreach(c, p.model_dump(), roles)
            except LLMError as e:
                console.print(f"[yellow]  draft failed for {p.name}: {e}")
                continue
            with db.connect() as con:
                con.executemany("INSERT INTO outreach (contact_id, channel, step, subject, body, resume_version) VALUES (?,?,?,?,?,?)", [
                    (contact_id, "email", 0, d.email_subject, d.email_body, d.resume_version),
                    (contact_id, "linkedin", 0, None, d.linkedin_note, None),
                    (contact_id, "email", 1, f"Re: {d.email_subject}", d.follow_up_1, None),
                    (contact_id, "email", 2, f"Re: {d.email_subject}", d.follow_up_2, None),
                ])
            budget -= 1
            drafted += 1
    console.print(f"outreach: {drafted} people researched + drafted")
    return drafted


def push_gmail_drafts() -> int:
    """Create Gmail drafts for approved first-touch emails and for follow-ups that are due."""
    n = 0
    with db.connect() as con:
        rows = con.execute("""SELECT o.*, ct.email FROM outreach o JOIN contacts ct ON ct.id=o.contact_id
                              WHERE o.channel='email' AND o.status='approved' AND ct.email IS NOT NULL""").fetchall()
    for r in rows:
        did = email_tools.create_gmail_draft(r["email"], r["subject"], r["body"], r["resume_version"])
        with db.connect() as con:
            con.execute("UPDATE outreach SET status='gmail_draft', gmail_draft_id=? WHERE id=?", (did, r["id"]))
        n += 1
    console.print(f"gmail: {n} drafts created (review + send from Gmail)")
    return n


def due_followups(con) -> list:
    """Follow-up drafts whose wait has elapsed since the first touch (any channel), unless they already replied."""
    d1, d2 = criteria()["outreach"]["follow_up_days"]
    return con.execute("""
        SELECT * FROM (
          SELECT f.id, f.step, f.body, ct.name, ct.email, ct.linkedin_url, co.name company,
                 (SELECT MIN(sent_at) FROM outreach x WHERE x.contact_id=f.contact_id AND x.step=0 AND x.status='sent') first_sent,
                 (SELECT MAX(sent_at) FROM outreach x WHERE x.contact_id=f.contact_id AND x.step=1 AND x.status='sent') fu1_sent
          FROM outreach f JOIN contacts ct ON ct.id=f.contact_id JOIN companies co ON co.id=ct.company_id
          WHERE f.step>0 AND f.status='drafted'
            AND NOT EXISTS (SELECT 1 FROM outreach x WHERE x.contact_id=f.contact_id AND x.status='replied'))
        WHERE first_sent IS NOT NULL
          AND ((step=1 AND julianday('now')-julianday(first_sent) >= ?)
            OR (step=2 AND fu1_sent IS NOT NULL AND julianday('now')-julianday(first_sent) >= ?))
        ORDER BY first_sent""", (d1, d2)).fetchall()


def write_digest() -> str:
    min_fit = criteria()["scoring"]["min_fit_to_surface"]
    today = date.today().isoformat()
    L = [f"# Daily digest — {today}\n"]
    with db.connect() as con:
        jobs = con.execute("""SELECT j.*, c.name FROM jobs j JOIN companies c ON c.id=j.company_id
                              WHERE j.status='new' AND j.fit_score>=? ORDER BY j.nyc DESC, j.fit_score DESC""", (min_fit,)).fetchall()
        L.append(f"## Open roles to apply to ({len(jobs)})\nNYC first, then by fit. Mark with `jobagent job <id> applied|skipped`.\n")
        for j in jobs:
            L.append(f"- **[{j['id']}] {j['name']} — {j['title']}** · {j['location']} · fit **{j['fit_score']}** · "
                     f"sponsorship: {j['sponsorship']} · resume: `{j['resume_version']}`\n  {j['why']}\n  {j['url']}")
        con.execute("UPDATE jobs SET status='surfaced' WHERE status='new' AND fit_score>=?", (min_fit,))
        older = con.execute("""SELECT j.id, j.title, j.fit_score, c.name FROM jobs j JOIN companies c ON c.id=j.company_id
                               WHERE j.status='surfaced' AND date(j.scored_at) < date('now') ORDER BY j.fit_score DESC LIMIT 15""").fetchall()
        if older:
            L.append("\n_Still open from earlier digests:_ " + " · ".join(f"[{o['id']}] {o['name']} — {o['title']} ({o['fit_score']})" for o in older))

        drafts = con.execute("""SELECT o.*, ct.name, ct.title, ct.linkedin_url, ct.email, ct.email_confidence, ct.hook, co.name company
                                FROM outreach o JOIN contacts ct ON ct.id=o.contact_id JOIN companies co ON co.id=ct.company_id
                                WHERE o.step=0 AND o.status='drafted' ORDER BY ct.id, o.channel""").fetchall()
        L.append(f"\n## Outreach ready for review ({len(drafts)})\n"
                 "Email: `jobagent approve <id>` then `jobagent gmail` → drafts appear in Gmail. "
                 "LinkedIn: send it yourself, then `jobagent sent <id>`.\n")
        for d in drafts:
            who = f"{d['name']} ({d['title']}, {d['company']})"
            if d["channel"] == "email":
                to = f"{d['email']} (confidence {d['email_confidence']})" if d["email"] else "_no email found — use LinkedIn or add with `jobagent set-email`_"
                L.append(f"### [{d['id']}] ✉️ {who}\nTo: {to} · resume: `{d['resume_version']}` · hook: {d['hook']}\n\n"
                         f"**{d['subject']}**\n\n{d['body']}\n")
            else:
                L.append(f"### [{d['id']}] 🔗 LinkedIn — {who}\n{d['linkedin_url'] or '_search their name on LinkedIn_'}\n\n> {d['body']}\n")

        fus = due_followups(con)
        L.append(f"\n## Follow-ups due ({len(fus)})\n")
        for f in fus:
            L.append(f"- [{f['id']}] {f['name']} @ {f['company']} (step {f['step']}, first sent {f['first_sent'][:10]}): {f['body']}")

        new_cos = con.execute("SELECT * FROM companies WHERE source='discover' AND date(created_at)=date('now')").fetchall()
        if new_cos:
            L.append(f"\n## Newly discovered companies ({len(new_cos)})\n")
            for c in new_cos:
                L.append(f"- **{c['name']}** ({c['lane']}, {c['size']}, NYC: {'yes' if c['nyc'] else 'no'}, sponsorship: {c['sponsorship']}) — {c['why']}")

        s = con.execute("""SELECT
            (SELECT COUNT(*) FROM outreach WHERE step=0 AND status='sent') sent,
            (SELECT COUNT(DISTINCT contact_id) FROM outreach WHERE status='replied') replied,
            (SELECT COUNT(*) FROM jobs WHERE status='applied') applied,
            (SELECT COUNT(*) FROM companies WHERE status!='ruled_out') companies""").fetchone()
        L.append(f"\n## Pipeline\nCompanies tracked: {s['companies']} · Applications: {s['applied']} · "
                 f"First-touch messages sent: {s['sent']} · Replies: {s['replied']}")

    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"digest-{today}.md"
    path.write_text("\n".join(L) + "\n")
    console.print(f"digest: {path}")
    return str(path)
