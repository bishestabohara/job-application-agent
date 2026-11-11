---
name: job-autopilot
description: Bishesta's job agent run, invoked headless by scripts/run.sh in two modes. "prepare" builds N application packets (tailored answers + one personalized outreach email per company). "deliver" checks Gmail for replies, sends due emails via the Gmail connector (only when auto-send is enabled), syncs Notion, and sends the daily report.
---

# Job autopilot (unattended)

You run headless when Bishesta starts `./scripts/run.sh`; she monitors via Notion and the report email.
**You are already in the project root.** Run each command on its own — `./ja today`, not `cd … && ./ja today`, and never
chain commands with `&&`, `;` or `|` (the permission list only allows single `./ja …` commands). `/tmp/jobagent/` already
exists; create files there with the Write tool. Subagents get the same rules — include this paragraph in every brief. The prompt
says which mode to run: **`/job-autopilot prepare`** or **`/job-autopilot deliver`**. Work from the project root, use
`./ja <command>`, write temp JSON under `/tmp/jobagent/`. End with a 5-line summary.

**Hard rules (both modes)**
- Use only `profile/` (context.md, criteria.yaml, answers.yaml, resumes) for facts about Bishesta. Never read other
  files on her computer. Never read her other email — only the threads `./ja threads` lists.
- Never invent people, titles, URLs, emails, or hooks. Everything about a contact must come from a page you saw.
- Factual/legal form answers (work authorization, sponsorship, dates, location, EEO) come **only** from
  `profile/answers.yaml`, verbatim in meaning. Sponsorship is always answered truthfully ("Yes" to future sponsorship).
- If anything fails, skip that item and move on — never retry in a loop.

---

# Mode: prepare

**Prepare only** — never send, submit, or contact anyone in this mode. (`scripts/run.sh` submits the applications
after you finish; the deliver run sends the emails once each application is confirmed submitted.)

Each run builds **packets**: one application + one personalized outreach email to someone at that company.
`profile/criteria.yaml → applying.per_run` sets how many (currently 10).

## P1 · Sort new companies by mission (only if needed)
`./ja today` → if `unclassified_companies > 0`: one subagent per batch of 150, in parallel (`general-purpose`, model
`haiku`), `--offset 0, 150, 300…` (cap 8 batches per run). Prompt:

> Run `./ja unclassified --limit 150 --offset <K>` in `<project root>`. For each company pick one lane from what you
> know about it (name + sample roles): `healthcare` (care access, quality, navigation, cost, clinical tools, patient-serving
> insurance), `education` (learning access), `caregiving_aging_mental_health`, `other_human_impact` (a genuine everyday
> benefit to regular people: housing, food, accessibility, public services, climate, financial inclusion), `avoid` (ad-tech,
> trading/quant/HFT, gambling, weapons/defense, engagement-bait consumer apps), or `non_mission` (everything else — generic
> tech, enterprise, B2B). For `avoid`, set `stepping_stone: true` only for companies famous for intern/new-grad ownership and
> growth. Set `nyc: true` if it has a known NYC office. Be decisive. Write a JSON list of `{id, lane, stepping_stone, nyc, why}`
> (`why` ≤ 10 words) to `/tmp/jobagent/classify_<K>.json`, run `./ja classify <file>`, reply only with the counts it prints.

## P2 · This run's queue
`./ja apply-queue` → up to `per_run` roles: recent (≤ 14 days), US/remote, auto-appliable (Greenhouse/Lever/Ashby),
max 2 per company. **Tier 1** (her target lanes + "pursuing" companies) comes first; **tier 2** fills the rest.

## P3 · Build one packet per role — one subagent each, in parallel
Spawn one `general-purpose` subagent per queued role (tier 1: default model; tier 2: model `sonnet`). Give it the role's
JSON from the queue, the project root, and this brief:

> You are preparing ONE application packet for Bishesta Bohara. Read `profile/context.md`, `profile/answers.yaml`, and her
> resume `profile/resumes/<swe_intern|swe_fulltime|pm_intern|pm_fulltime>.md` (SWE vs PM by the role; intern vs full-time
> by the role — new-grad/full-time → `_fulltime`). Use only these files for facts about her — never other files on the computer.
>
> **1 · Check fit.** `./ja apply-fields <job_id>` (form fields). Read the posting at its URL (WebFetch). If it needs 3+ years of
> experience, isn't a SWE/PM/AI-engineering role, or requires US citizenship/clearance: `./ja apply-skip <job_id> "<reason>"`,
> reply "skipped: <reason>", and stop.
>
> **2 · Research** (tier 1: deep — company site, product, mission, recent news/launches/funding, the team this role sits on,
> engineering blog; tier 2: light — the posting + the company's homepage). Find 2–3 **specific** things that connect to her
> real experience (e.g. her EHR record-reconciliation project for healthcare data roles; AI agent work at AIBCG; the edtech
> dashboards at Lavner Education; the NOAA RAG platform).
>
> **3 · Answers.** Build `{field key: value}`:
> - Identity, education, links, work authorization, **sponsorship ("Yes", truthfully)**, dates, relocation, salary, EEO
>   ("decline" option), how-heard → from `answers.yaml`. Option fields must use one of the field's `options` exactly.
>   Graduation: intern → Spring/May 2028; full-time → Spring/May 2027. Skip `file` fields. **Never write or attach a cover
>   letter** — leave every cover-letter field empty, even if it's marked required (then the role falls to "needs you").
> - **Every essay / short answer ("Why this company?", "Tell us about a project", etc.) — write like Bishesta talking,
>   not like a cover letter.** Rules:
>   - **Short:** 50–90 words (unless the form asks for more). 3–5 sentences. Answer the question in the first sentence.
>   - **Simple words.** Write how a real person talks. If a 12-year-old wouldn't know the word, use a simpler one.
>   - **Personal + specific:** one real reason this company matters to her (from `profile/context.md` — e.g. she wants
>     to fix how hard it is for regular people to get care), one specific thing about *this* company you found (a product,
>     a feature, a problem they solve — by name), one thing she actually built that connects (human problem first, then
>     one short line of tech). End with what she wants to work on there.
>   - **Passionate but honest:** show she cares through specifics, not adjectives. No claims she can't back up.
>   - **Never use:** leverage, synergy, cutting-edge, innovative, dynamic, passionate about leveraging, thrilled,
>     I am confident that, I believe I would be a great fit, unique opportunity, fast-paced, drive impact, align with,
>     "I've always been passionate", "In today's world", em-dash chains, lists of buzzwords.
>   - Example of the tone (don't copy it — write a new one for each company):
>     "I want to work at Grow Therapy because finding a therapist who takes your insurance shouldn't take weeks. I've
>     watched people give up halfway. At AIBCG I built AI agents that handle scheduling for small businesses, and in a
>     side project I matched patient records across three different EHR systems. I'd love to work on the provider side
>     of Grow, making it faster for people to book their first session."
> - **EEO / demographic questions:** use `answers.yaml → eeo` (gender Female; race Asian, not Hispanic or Latino;
>   Hispanic/Latino: No; veteran: not a protected veteran). For anything not listed there (disability, sexual orientation,
>   transgender), pick the form's "decline / don't wish to answer" option. Option fields must use the exact option text.
> - **School** fields: "The University of Southern Mississippi" (search boxes: the tool types it and picks the match).
> - **Education dates** (from her resume): start **August 2024**; end = **May 2028** if the resume sent is an intern one,
>   **May 2027** if it's a full-time one. Month/Year dropdowns take "August" / "2024" etc. If the form has a
>   "Still student?" / "Currently attending" box, answer it "Yes" and leave the end date empty (she is a current student);
>   otherwise fill the end date — and if that year isn't offered, leave it empty rather than picking another.
> - **Resume:** `apply-fields` returns `resume_version` (auto-picked: PM vs SWE by role, intern vs full-time by level).
>   Read that resume file (`resume_text`) for her experience — pass `--resume <version>` to `apply-prepare` only if the
>   auto-pick is clearly wrong for the role.
> - Write the JSON to `/tmp/jobagent/answers_<job_id>.json` → `./ja apply-prepare <job_id> <file>`; fix any `problems` once.
>
> **4 · One person + one email.** Find ONE real person at the company with a current role there (search results you
> actually saw): the likely hiring manager or an engineer/PM on this team; a university/early-career recruiter if no one
> better. Get their LinkedIn URL if you see it. Email: only if published on a page you saw, else
> `./ja find-email "<name>" <company website>` with confidence ≥ 80; otherwise `null`. Never guess.
> Write, in her voice (personal, passionate, simple):
> - `email_subject` — short, specific (e.g. "applied to the SWE intern role — your post on …").
> - `email_body` — under 110 words, same plain, personal style as the answers above: their specific work (the hook) → she just applied for <role> → one line on why this
>   mission matters to her → one proof point (human problem first) → ask for a 15-minute chat. Sign "Bishesta" +
>   linkedin.com/in/bishesta-bohara. No attachment, no resume dump, no "I hope this finds you well".
> - `linkedin_note` — under 300 characters, no links. `follow_up_1` / `follow_up_2` — one line each (not sent unless enabled).
> Save `[{"company_id": <id>, "job_id": <job_id>, "contact": {name, title, linkedin_url, hook, evidence_url, why_this_person},
> "drafts": {resume_version, email_subject, email_body, linkedin_note, follow_up_1, follow_up_2}, "email": <str|null>}]`
> to `/tmp/jobagent/outreach_<job_id>.json` → `./ja save-outreach <file>`. If nobody real turns up, skip this step.
>
> Reply with one line: `<company> — <role>: prepared | skipped (<why>) · contact: <name, title | none> · email: yes/no`.

Run all subagents, wait for them, and collect their one-line results. If fewer than `per_run` packets got prepared because
of skips, run `./ja apply-queue` once more and fill the gap (one extra round at most).

## P4 · Summary
Five lines: companies classified · packets prepared (tier 1 / tier 2) · skipped + why · contacts with email / LinkedIn-only ·
failures.

---

# Mode: deliver

The prompt ends with `auto_send=1` or `auto_send=0`. Only with `auto_send=1` may you call Gmail `send_message`.

## D1 · Replies and bounces (always)
`./ja threads` → for each `thread_id`, Gmail `get_thread` with `messageFormat: "METADATA_ONLY"`.
- A message whose sender isn't `boharabishesta12@gmail.com` → `./ja mark-thread <contact_id> replied`.
- A sender like `mailer-daemon` / `postmaster` → `./ja mark-thread <contact_id> bounced`.
Don't read message bodies. **Never reply to anyone** — answering replies is hers; this only updates the tracker.

## D2 · Send (only if `auto_send=1`)
`./ja outbox` → if `blocked` is set, send nothing and note why. (It only lists first outreach emails whose application
is already submitted; follow-ups appear only if `outreach.follow_ups: true`.) Otherwise for each email, in order:
- `kind: "first"` → Gmail `send_message` with `to: [to]`, `subject`, `body` (plain text exactly as given — no edits,
  no Markdown, no attachments).
- `kind: "follow_up_*"` → Gmail `send_message` with `to: [to]`, `subject`, `body`, and `replyThreadId: thread_id`
  so it lands in the same conversation.
- After each successful send → `./ja mark-sent <outreach_id> <response id> <response threadId>`.
- Stop at the first error that isn't about a single recipient (auth, quota) and note it.

With `auto_send=0`: for each outbox email create a Gmail **draft** instead (`create_draft`, same fields; follow-ups
without threading) and do **not** mark it sent.

## D3 · Notion
`./ja notion-changes` → for each of `applications` and `outreach`:
- `create` → Notion `create-pages` with `parent: {data_source_id}` and each entry's `properties` exactly as given
  (batch up to 50 per call, `allow_async: false`). Collect `{kind, tracker_id, page_id}` from the results.
- `update` → Notion `update-page` (`command: "update_properties"`, the entry's `page_id` and `properties`).
Write the collected ids to `/tmp/jobagent/notion_saved.json` (include updated ones too) → `./ja notion-saved <file>`.
Then `./ja notion-report` → `create-pages` under its `data_source_id` with its `properties` and `content`.

## D4 · Report email (only if `auto_send=1`)
`./ja report --no-send` → Gmail `send_message` to `boharabishesta12@gmail.com`, subject `Job agent — <Mon DD>`, the
text as `body`, and append the Notion hub link from `data/notion.yaml`.

## D5 · Summary
Five lines: replies/bounces found · emails sent (or drafted) · Notion rows created/updated · report sent · failures.
