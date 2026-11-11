# Job agent

Each time **you** run it, the agent builds **10 packets**. A packet is one application with answers written for that
company and role, plus one personal outreach email to someone at that company. The applications get submitted, the
emails go out once their application is confirmed submitted, and everything is logged in Notion.

```
./scripts/run.sh
  1  ./ja daily                       fetch new postings: company boards + SimplifyJobs intern/new-grad lists   free
  2  claude /job-autopilot prepare    pick 10 recent roles (your target companies first) → one helper each:     your Claude plan
                                      check fit · research · unique answers · find 1 person · write the email
  3  ./ja apply-run                   browser fills + submits them. Solve any CAPTCHA, then press Enter.
  4  claude /job-autopilot deliver    send the emails for submitted applications · note replies · update Notion  your Claude plan
```

- **Tier 1** means your target lanes (healthcare, education, caregiving/mental health) and "pursuing" companies. These
  get deep research and are filled first. **Tier 2** is any other recent intern/new-grad SWE/PM/AI role.
- Rules: posted in the last 14 days, US or remote, at most 2 applications per company, and the helper skips roles
  needing 3+ years.
- **The agent only sends first outreach emails.** It never replies to anyone and sends no follow-ups (`outreach.follow_ups: false`).
  Replies show up in Notion as "Replied", and you answer them yourself.
- Scale up with `applying.per_run` in `profile/criteria.yaml`. The load is roughly 350–500k tokens per 10 packets.

**Notion:** [Job Agent](https://app.notion.com/p/3e9198f7bada817fa790e13e43966ee1) (private) holds **Applications**,
**Outreach** and **Daily Reports**.

## Setup

1. ✅ Gmail + Notion connected in claude.ai
2. (Recommended) a [Hunter.io](https://hunter.io) key in `.env` as `HUNTER_API_KEY=`. Without it, only published
   addresses get emails, and everyone else gets a LinkedIn note (shown in Notion).
3. **First run** with `AUTO_SEND=0`: emails land in your Gmail **Drafts** and nothing is sent.
   ```bash
   cd ~/Desktop/"Job application agent" && ./scripts/run.sh
   ```
4. Happy with the drafts? Set `AUTO_SEND=1` at the top of `scripts/run.sh`. That's your permission for runs to send from your Gmail.

## Monitoring & control

| | |
|---|---|
| Everything at a glance | Notion → Job Agent |
| **Pause everything** | `touch PAUSE` in the project folder. Delete the file to resume. |
| "Needs you" applications | `./ja apply-run --retry`. The forms reopen already filled; solve the CAPTCHA. |
| Someone replied | detected automatically (Outreach → Replied). **You** write the reply. |
| LinkedIn notes | Outreach → "LinkedIn to paste": paste the note, then `./ja sent <id>` |
| Token usage per run | end of `out/run.log` |
| Stop a company | `./ja company "Plaid" ruled_out` |

## Limits & safety (`profile/criteria.yaml`)

- 10 packets per run, at most 2 applications per company, and 15 emails a day.
- An email is sent only after its application is submitted, only to published or Hunter-verified addresses, and never
  with an attachment. The agent never replies to anyone and sends no follow-ups.
- Application facts (visa, sponsorship, dates, EEO) come only from `profile/answers.yaml`. Sponsorship is always
  answered truthfully, and answers are checked against each form's real options before submitting.
- The prepare step can't send anything. The deliver step sends only with `AUTO_SEND=1`, and only what `./ja outbox` allows.
- CAPTCHAs are never bypassed. LinkedIn is never automated.

## Other modes

- `/job-session` in Claude Code: the same work done interactively, where you approve each message.
- `./ja auto`: runs via the Claude API instead (needs `ANTHROPIC_API_KEY`, roughly $2–5 a day).

## Files

`profile/context.md` (goals, voice) · `profile/answers.yaml` (application facts) · `profile/criteria.yaml` (limits) ·
`data/companies.yaml` (seed companies) · `data/notion.yaml` (Notion ids) · `.claude/skills/job-autopilot/` (the playbook).
