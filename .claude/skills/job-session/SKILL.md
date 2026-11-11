---
name: job-session
description: Bishesta's supervised daily job-search session — score new postings, pick today's applications, research people at mission-fit companies, and draft email + LinkedIn outreach for her approval, within daily quotas. Use when she says "job session", "let's do today's outreach", "daily jobs", or runs /job-session.
---

# Daily job session

You are running Bishesta's daily job search with her, live. She supervises; you do the legwork. Aim: **~15–20 minutes of
her time**, ending with ≤ N applications done and ≤ N personal messages sent (quotas in `profile/criteria.yaml → daily_session`).

**Hard rules**
- Never send an email, LinkedIn message, or application yourself. You draft; she sends. The only "send-like" step is
  creating Gmail *drafts* with the Gmail connector's `create_draft`.
- Never invent a person, title, URL, email, or hook. Everything about a contact must come from a page you actually saw
  this session. If research comes back thin, skip that person.
- Stay inside the quotas unless she explicitly asks for more.
- Keep chat output compact: tables and short drafts, not walls of JSON. Don't paste tool output back at her.

All commands run from the project root via `./ja <command>`. Write temporary JSON to the scratchpad directory.

## 0 · Refresh (free, ~1 min)

```bash
./ja daily      # pulls postings (skip if the morning launchd run already did — check `out/daily.log` date)
./ja today      # quotas left, roles ready, drafts pending, follow-ups due, target pool size
```
Tell her in 2–3 lines what today looks like (e.g. "12 new postings, 3 follow-ups due, 5 applications + 8 people left today").

## 0b · Sort new companies by mission (before scoring)

If `unclassified_companies > 0` (new companies from the SimplifyJobs lists): one subagent per batch of 150, in parallel
(`general-purpose`, model `haiku`), with `--offset 0, 150, 300…` (cap at 8 batches per run; the rest wait for tomorrow). Prompt:

> Run `./ja unclassified --limit 150 --offset <K>` in `<project root>`. For each company decide its mission lane from
> what you know about it (name + sample roles): `healthcare` (care access, quality, navigation, cost, clinical tools,
> health insurance that serves patients), `education` (learning access), `caregiving_aging_mental_health`,
> `other_human_impact` (a genuine everyday benefit to regular people: housing, food, accessibility, public services,
> climate, financial inclusion), or `non_mission` (ads, trading/quant, defense, gambling, generic enterprise/B2B,
> consulting, engagement-optimized consumer). For `non_mission`, set `stepping_stone: true` only for companies known for
> strong new-grad/intern ownership and growth (e.g. top tech/product companies). Set `nyc: true` if it has a known NYC
> office. Be decisive; unknown small companies with unclear missions → `non_mission`. Write a JSON list of
> `{id, lane, stepping_stone, nyc, why}` (`why` ≤ 10 words, empty for non_mission) to `<scratch>/classify_<K>.json`, run
> `./ja classify <file>`, and reply only with the counts it prints.

## 1 · Score new postings (delegate — keeps this session light)

If `unscored_jobs > 0`, spawn **one subagent per batch of 15** in parallel (`general-purpose`, model `sonnet`), each with
`--offset 0`, `15`, `30`, … Prompt each with:

> Read `profile/context.md` and `profile/criteria.yaml` in `<project root>`. Run `./ja queue --limit 15 --offset <K>`.
> For every posting, write a score object: `job_id`, `fit_score` (0-100), `role_type`
> (swe_intern|swe_fulltime|pm_intern|pm_fulltime|not_target), `level_ok` (true only if a student graduating 2027/2028 can
> realistically get it — interns, new grad, junior, "I", or unleveled with ≤2 yrs required), `resume_version`
> (swe_intern|swe_fulltime|pm_intern|pm_fulltime), `nyc`, `sponsorship` (likely|unlikely|confirmed_no|unknown — confirmed_no
> only if stated), `stepping_stone`, `why` (≤2 sentences, specific), `red_flags` (list).
> Weigh in order: level → mission lane (healthcare first; company size irrelevant) → skills fit → NYC → sponsorship (boost only).
> Senior / 3+ yrs / non-SWE-PM roles score < 30. Postings marked "From SimplifyJobs" have no full description —
> judge from title, company, terms and degrees; don't penalize the missing text. Save the JSON list to `<scratchpad>/scores_<K>.json`, then run
> `./ja save-scores <that file>`. Reply with only: count saved, and the top 3 as "job_id · company · title · score".

## 2 · Applications (quota: `applications.left`)

Run `./ja today` again and show `roles_to_apply` as a table: id · company · title · location · fit · sponsorship · resume.
NYC first. Flag deadline-sensitive ones (e.g. Duolingo) and `confirmed_no` sponsorship without hiding them.

She picks up to the quota. For each pick:
- Give the resume file: `profile/resumes/pdf/<resume_version>.pdf`.
- If the application has short-answer questions ("why this company?", "tell us about a project"), draft answers in her
  voice: a real, specific reason; lead project answers with the human problem, then the tech. Keep them tight.
- She submits it herself. Then: `./ja job <id> applied`. Roles she passes on: `./ja job <id> skipped`.

## 3 · Follow-ups due (always do these — they're the cheapest replies)

For each item in `followups_due`: show the drafted follow-up, let her tweak it (save edits with `./ja edit <id> <file>`),
she sends it in the original thread / LinkedIn chat, then `./ja sent <id>`. If someone already replied:
`./ja replied "<name>"` and celebrate briefly.

## 4 · New outreach (quota: `new_people.left`, ~2 people per company)

1. `./ja targets --limit <ceil(left / 2)>` → today's companies (already prioritized: pursuing → strong open role →
   healthcare → NYC).
2. **Research in parallel** — one subagent per company (`general-purpose`, model `sonnet`), prompt:

   > Research people at **<company>** (<website>, lane: <lane>; why it fits: <why/note>; open roles: <open_roles>;
   > skip: <already_contacted>) for Bishesta, a CS+Math student seeking SWE/PM internships or new-grad roles in
   > mission-driven healthcare/education/caregiving. Use WebSearch/WebFetch. Find up to 2 real people with a current role
   > there. Preference: startup (<200) → founder/CTO/head of eng or product; larger → an eng manager or engineer on a
   > patient/learner-facing team, or a PM; a university recruiter last. Bonus: Southern Miss alumni, Grace Hopper / SWE /
   > WiCyS / Rewriting the Code members, people who've written or spoken publicly about the mission.
   > For each: `name`, `title`, `linkedin_url` (only if you actually saw it, else ""), `hook` (one specific, recent,
   > public thing they wrote/built/said), `evidence_url` (where you saw the hook or role), `why_this_person`, and `email`
   > only if publicly listed (else null). Never guess names, URLs, or emails. Return only a JSON list (may be empty).

3. **You draft** each person's messages (don't delegate — voice matters), following *Voice* below:
   `email_subject`, `email_body`, `linkedin_note`, `follow_up_1`, `follow_up_2`, `resume_version`.
4. Show her each person as a compact card: who + why + hook (with link), then the email and LinkedIn note.
   She approves, edits, or skips. Apply edits before saving.
5. Save approved ones — a JSON list of `{"company_id", "contact": {name,title,linkedin_url,hook,evidence_url,why_this_person},
   "drafts": {resume_version,email_subject,email_body,linkedin_note,follow_up_1,follow_up_2}, "email": <str|null>}` →
   `./ja save-outreach <file>` (returns outreach ids). Companies where nobody good turned up: `./ja no-contacts <ids>`.
6. Sending:
   - **Email** (has address): Gmail connector `create_draft` (to, subject, body — plain text, no attachment) → it's in
     her Gmail Drafts; she presses send. Then `./ja approve <email_id>` so the tracker knows it's queued.
   - **LinkedIn**: give her the profile URL and the note to paste into "Add a note". Prefer LinkedIn when there's no email.
   - After she confirms what she actually sent: `./ja sent <ids>` (starts the 5/12-day follow-up clock).
     Skipped channel for that person: `./ja skip <id>`.

## 5 · Top up the company pool (only when low)

If `untouched_target_companies < daily_session.discover_when_targets_below`, spawn one subagent (`general-purpose`,
model `sonnet`) to find `discover_batch` new companies: real, operating, genuine everyday human impact (healthcare ≥ half,
then education, caregiving/aging/mental health); any size; favor intern/new-grad hiring, NYC presence, visa sponsorship
signals; exclude everything already tracked (`sqlite3 data/jobagent.db "select name from companies"`). Output a JSON list of
`{name, website, lane, size (startup|mid|large), nyc, sponsorship, why, evidence_url}` → `./ja add-companies <file>`.

## 6 · Wrap up (3 lines)

Applications done / quota · people contacted / quota · follow-ups sent · anything to watch tomorrow (deadlines, replies).

---

## Voice (for every message you draft)

- Goal is **a 15-minute conversation**, not a job. Mention internship/new-grad interest lightly, at most once. Never "refer me".
- Personal, passionate, simple. No "I hope this finds you well", no corporate filler, no flattery.
- Structure: their specific hook → one line on why this mission matters to her (a real reason from `profile/context.md`)
  → one concrete proof point, human problem first then tech (the Clinic Reconciliation Engine — EHR record matching, 95% —
  is the strongest hook for healthcare; the AI agents work at AIBCG for AI/automation teams; Lavner Education for edtech)
  → a clear, easy ask.
- Email: under 150 words, short specific subject, sign off "Bishesta" + `linkedin.com/in/bishesta-bohara`. No resume dump.
- LinkedIn note: under 300 characters (count them), no links.
- Follow-up 1 (~day 5): 2–3 sentences, adds one new small thing (a relevant link, a thought on their work). Follow-up 2
  (~day 12): 2 sentences, gracious, no guilt, leaves the door open.
- Resume: SWE vs PM by the person's function; intern (grad 2028) by default, full-time (grad 2027) for new-grad roles.
