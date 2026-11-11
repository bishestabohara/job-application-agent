# Job Application Agent — session handoff

This file holds the context from the previous Claude Code session (2026-09-27 → 2026-10-13). Read it before doing anything.
The owner is **Bishesta Bohara** (she/her). Personal Gmail: `boharabishesta12@gmail.com`.

---

## 1. Who she is and what she wants

- CS + Math at the University of Southern Mississippi (Hattiesburg, MS), GPA 3.762. Started Aug 2024.
  Graduates **May 2028** on her intern resumes and **May 2027** on her new-grad resumes.
- **F-1 international student.** Sponsorship ranks a company higher but is **never** a reason to skip it. Answer "Yes" to
  "will you need sponsorship now or in the future" truthfully, every time.
- Targets: **SWE and PM, internship and new-grad**. **US roles only.** NYC preferred, not required. Company size doesn't matter.
- Mission lanes, in priority order: **healthcare** (main focus) → education → caregiving/aging/mental health → other everyday human impact.
  Avoid ad-tech, trading/HFT, gambling and defense, unless the role is a real stepping stone.
- Full narrative: `profile/context.md`. Machine-readable settings: `profile/criteria.yaml`.

## 2. Rules she gave (follow these)

- **Use only files in `profile/`** for facts about her. **Never read other files on her computer** (Downloads etc.).
  If something is missing, ask her.
- **Writing style** for application answers and emails: short, precise, **simple words**, personal and passionate.
  No buzzwords ("leverage", "cutting-edge", "thrilled", "I believe I'd be a great fit", "align", "fast-paced"),
  no em-dashes, no cover-letter tone. Emails run 70–90 words. Each one has: a specific hook about the person,
  "I just applied for <role>", one real reason the mission matters to her, one proof point (human problem first, then tech),
  and a request for a 15-minute chat. Sign-off: `Bishesta\nlinkedin.com/in/bishesta-bohara`.
  (A `humanize-application` skill may also be available for this.)
- **Never submit a cover letter.**
- **Only first-touch outreach.** Never reply to anyone's emails and no follow-ups (`outreach.follow_ups: false`).
- **Always pick the right one of her 4 resumes** (see §4).
- **Current split of work (her latest instruction): SHE applies to the jobs herself. CLAUDE does the outreach**
  (find a person + verified email, write the message, put it in Gmail Drafts, log it in Notion).
- Start small (about 10 per run) and scale later.

## 3. Hard limits learned in the last session

- Claude Code's safety check **blocks Claude from sending email or submitting applications on her behalf.**
  Don't try to work around it. Claude can **create Gmail drafts**; she presses Send. Submitting runs only as scripts she starts.
- **LinkedIn is never automated** (ban risk). Claude writes the note; she pastes it with Connect → Add a note.
- **CAPTCHAs are never bypassed.**
- Email addresses: only ones **published on a page Claude actually saw**, or **Hunter-found AND Hunter-verified "deliverable"**
  (`./ja find-email` does both). Never guess address patterns, because bounces hurt her Gmail reputation.

## 4. Her answers and resumes

- `profile/answers.yaml` holds every factual form answer: contact details, location **Hattiesburg, Mississippi**,
  school, degree, start/end dates, F-1, sponsorship Yes, relocation yes (NYC preferred), salary "Open / negotiable",
  and EEO (Female; Asian, not Hispanic/Latino; not a protected veteran; decline the rest).
- Resumes are in `profile/resumes/pdf/` (with text copies as `.md`). They're picked automatically by `jobagent.apply.pick_resume(title)`:
  | version | when | uploads as |
  |---|---|---|
  | `swe_intern` | SWE/AI internships, co-ops (grad May 2028) | `Bishesta_Bohara_Resume_SWE_Intern.pdf` |
  | `swe_fulltime` | new-grad / unleveled SWE (grad May 2027) | `Bishesta_Bohara_Resume_SWE_NewGrad.pdf` |
  | `pm_intern` | PM / APM internships | `Bishesta_Bohara_Resume_PM_Intern.pdf` |
  | `pm_fulltime` | new-grad PM | `Bishesta_Bohara_Resume_PM_NewGrad.pdf` |

## 5. The project (what's built)

Python package `jobagent/`, CLI wrapper `./ja <command>` (= `.venv/bin/python -m jobagent`), SQLite tracker `data/jobagent.db`.

- **Job sources (free):** Greenhouse/Lever/Ashby public boards for ~67 tracked companies (`data/companies.yaml`), plus the
  SimplifyJobs GitHub lists (Summer 2027 internships + New Grad). New companies from Simplify are classified by mission lane.
- **Key commands**
  - `./ja daily`: fetch new postings
  - `./ja apply-queue [--limit N]`: recent (≤14 days), US-only, eligible roles; tier 1 (target lanes) first; max 2 per company
  - `./ja status` · `./ja progress [--watch]` · `./ja applications`
  - `./ja find-email "First Last" domain.com`: Hunter lookup + verify (uses `HUNTER_API_KEY` in `.env`; free plan, about 40 lookups left this month)
  - `./ja save-outreach file.json`: store contact + drafts (JSON shape in `.claude/skills/job-autopilot/SKILL.md`)
  - `./ja drafts`: all pending first-touch drafts with full text
  - `./ja sent <outreach ids>` · `./ja skip <ids>` · `./ja replied "<name>"`
  - Applying (she runs these): `./ja apply-fields <job>` · `./ja apply-prepare <job> answers.json` ·
    `./ja apply-check` (fills every field in a hidden browser, **never submits**, lists every field + resume, saves screenshots) ·
    `./ja apply-run [--retry]` (visible browser, submits)
  - `./scripts/run.sh` = full run (fetch → Claude prepares → apply-run → deliver). `./scripts/run.sh deliver` = deliver step only.
    `AUTO_SEND=0` at the top of the script means emails become Gmail drafts.
- **Playbooks:** `.claude/skills/job-autopilot/SKILL.md` (headless prepare/deliver), `.claude/skills/job-session/SKILL.md` (interactive).
- **Notion tracker** (private page "Job Agent"): https://app.notion.com/p/3e9198f7bada817fa790e13e43966ee1
  The Applications, Outreach and Daily Reports databases. Data-source ids are in `data/notion.yaml`.
  Sync: `./ja notion-changes` (rows to create/update in Notion property format) → create/update via the Notion connector →
  `./ja notion-saved file.json` (records `{kind, tracker_id, page_id}`) · `./ja notion-report` for the run's report page.

## 6. Accounts and connectors (check these first)

- Claude Code was last signed in as **elissa@analyticsandsociety.com**. Her own account is **bishesta@analyticsandsociety.com**.
  Check with `claude auth status`. Switch with `claude auth logout` + `claude auth login`.
- claude.ai connectors belong to whichever Claude account is signed in. Gmail and Notion were connected on bishesta@.
  She also connected Gmail on elissa@, then asked how to remove it, so it may be disconnected now.
- Connector tools only appear after the Claude Code session restarts. If Gmail/Notion tools are missing, ask her to connect them
  (claude.ai → Settings → Connectors) on the signed-in account, then reload.
- **Privacy:** both @analyticsandsociety.com Claude accounts belong to her institute's organization. Anything read or written
  through connectors on those accounts can be visible to org admins (conversation export/audit on Team/Enterprise plans).
  She's considering moving this to a **personal** Claude account, connecting her personal Gmail/Notion there either directly
  or through her **own** Composio account (Composio is optional; the built-in claude.ai connectors do the same job).
  Before connecting her personal Gmail, ask which account she wants to use.

## 7. Current state (2026-10-13)

**Applications:** none submitted yet. The 6 prepared ones (Grow Therapy, Commure ×2, Gemini PM, Hippocratic AI ×2) are
`needs_you`. They were checked with `apply-check` (all fields and resumes correct) but she never finished submitting them.
4 more (Vital Lyfe, Ramp ×2, MLB) were left incomplete (felony/citizenship/Kotlin/consent questions, which she must answer herself).

**Outreach drafts ready** (nothing sent). Format: contact id · company · role · person · email · outreach ids (email / LinkedIn):

| cid | Company · Role | Person | Email | ids | Notes |
|---|---|---|---|---|---|
| 13 | Khan Academy · SWE Intern | Matthew Curtis, Sr SWE | matthewcurtis@khanacademy.org | 49 / 50 | good fit |
| 15 | Replit · SWE Intern | Michele Catasta, President & Head of AI | michele@replit.com | 57 / 58 | good fit |
| 11 | DoorDash · SWE Intern (Labs/robotics) | Ashu Rege, VP Autonomy | ashu.rege@doordash.com | 41 / 42 | F-1 CPT OK |
| 9 | Tia · Business Automation Eng | Felicity Yost, CEO | — (LinkedIn only) | 33 / 34 | 1+ yr, internships count |
| 17 | Affirm · SWE Early Career (NYC) | Claire Yang, SWE | — (LinkedIn only) | 65 / 66 | hook = blog post title only; she should skim it |
| 8 | Healthie · Forward Deployed Eng | Cavan Klinsky, CTO | cavan@gethealthie.com | 29 / 30 | posting wants 5–10+ yrs |
| 12 | Healthie · QA Engineer | Michele Martone, QA Eng Mgr | michele.martone@gethealthie.com | 45 / 46 | 3+ yrs, **no visa sponsorship** |
| 10 | Maven Clinic · PM Care Delivery | Jaya Savkar, SVP Product | jaya.savkar@mavenclinic.com | 37 / 38 | 3+ yrs PM |
| 14 | Charlie Health · PM Business Systems | Ashok Balakrishnan, CTO | — (LinkedIn only) | 53 / 54 | 2+ yrs ops |
| 16 | Gallup · Data Eng Intern (SF, on-site M–Th) | Taek Lee, CFO/interim CTO | — | 61 / 62 | weak contact; suggested skipping |
| 1 | Grow Therapy · SWE Intern | Ellen Smalley, Sr SWE | ellen.smalley@growtherapy.com | 1 / 2 | waits on that application |
| 2 | Commure · Scribe AI | Jyothi Bommareddy, SWE | jyothi.bommareddy@commure.com | 5 / 6 | waits on that application |
| 3 | Commure · Voice Agents | Prachi Shah, Head of Product | — | 9 / 10 | waits on that application |
| 4 | Hippocratic AI · FDE Residency | Jue Kotak, recruiter | — | 13 / 14 | waits on that application |
| 5 | Hippocratic AI · ML Engineer | Tanmay Laud, Staff Scientist | tanmay@hippocraticai.com | 17 / 18 | waits on that application |
| 6, 7 | Vital Lyfe, Ramp | — | — | — | apps not done; don't send |

Every email says "I just applied", so **a draft goes to Gmail only after she confirms she has applied to that role.**

## 8. Next steps

1. Confirm the Claude account and connectors (§6).
2. Ask her which roles she has applied to.
3. For each applied role that has an email: create a **Gmail draft** (to, subject, body exactly as stored, plain text, no attachment),
   then `./ja approve <email id>`. After she says she sent it: `./ja sent <id>`.
4. Give her the LinkedIn notes (profile URL + note) for the LinkedIn-only contacts.
5. Sync Notion (`./ja notion-changes` → connector → `./ja notion-saved`).
6. For more jobs: `./ja daily` → `./ja apply-queue` → one research helper per role, as done for the 10 above.

## 9. Manual outreach and applications from the RESUME folder (Oct 13 – Nov 11, 2026)

A second Claude Code workspace, `~/Desktop/RESUME`, handles the hand-written side of the job search. **Read `~/Desktop/RESUME/CLAUDE.md` for the full context.** It has her facts, gaps never to claim, writing style, the cover-letter render commands, and an application log of about 35 companies.

- **Files there:** resumes (SWE/Data/Product, intern + new grad), a tailored NYL resume, cover letters (IXL APM + SWE, Findhelp, CGI, Slate), `outreach/messages.md` (LinkedIn DMs, batches 1–8) and `outreach/referrals.md` (referral playbook + learning log).
- **Skill:** `~/.claude/skills/humanize-application/` (`SKILL.md` + `profile.md`) is used for every answer and message.
- **Changes to the rules in §2** (her newer instructions win):
  - She now asks for cover letters sometimes (e.g. Slate, Nov 11). Write one when she asks.
  - LinkedIn DMs and referral asks are sign-off `Best,\nBishesta Bohara`, "Hi Name," on its own line, short, a personal hook from their profile, "happy to send my resume".
  - Follow-up pings are fine when she asks (e.g. Ross Arroyo at Illumibot).
  - Only target full-time employees and startup founders/early engineers. Skip students, interns, and open-to-work profiles.
- **Graduation date:** she's used May 2027 (Illumibot) and Fall 2027 (Southern Farm Bureau Life) in recent outreach. Not final. Flag mismatches.
- **Visa flags seen:** Corgi and Greenboard ("US citizen/visa only"), Shopify/CapTech (no sponsorship), NYL (excludes CPT/OPT). Blue (YC S25) sponsors.
