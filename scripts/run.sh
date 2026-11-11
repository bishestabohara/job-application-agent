#!/bin/zsh
# One run of the job agent — start it yourself:  ./scripts/run.sh   (logs to out/run.log)
#   ./scripts/run.sh deliver   → only step 4 (e.g. after reconnecting Gmail/Notion)
#   1. ./ja daily                     fetch new postings (company boards + SimplifyJobs)          script, free
#   2. claude /job-autopilot prepare  build 10 packets: tailored application + 1 personal email   your Claude plan
#   3. ./ja apply-run                 submit them in a browser — solve any CAPTCHA that pops up   script, you're here
#   4. claude /job-autopilot deliver  send emails for submitted applications · update Notion     your Claude plan
#
# ─── AUTO-SEND SWITCH ─────────────────────────────────────────────────────────────────────────────
# 0 = emails are created as Gmail drafts for you to send.   1 = Claude sends them via your Gmail connector.
# Turning this on gives the morning run standing permission to send email from your account (within the
# caps in profile/criteria.yaml). Set it yourself when you're ready.
AUTO_SEND=0
# ──────────────────────────────────────────────────────────────────────────────────────────────────

cd "$(dirname "$0")/.." || exit 1
mkdir -p out /tmp/jobagent
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

SCRATCH=(Read "Edit(//tmp/jobagent/**)" "Edit(//private/tmp/jobagent/**)" "Write(//tmp/jobagent/**)" "Write(//private/tmp/jobagent/**)" "Bash(ls:*)" "Bash(cat /tmp/jobagent/:*)" "Bash(./ja --help)" "Bash(./ja * --help)")
PREPARE_TOOLS=("${SCRATCH[@]}" WebSearch WebFetch Agent
  "Bash(./ja today)" "Bash(./ja unclassified:*)" "Bash(./ja classify:*)" "Bash(./ja apply-queue:*)"
  "Bash(./ja apply-fields:*)" "Bash(./ja apply-prepare:*)" "Bash(./ja apply-skip:*)" "Bash(./ja save-outreach:*)"
  "Bash(./ja find-email:*)" "Bash(./ja status)")

DELIVER_TOOLS=("${SCRATCH[@]}"
  "Bash(./ja threads)" "Bash(./ja mark-thread:*)" "Bash(./ja outbox)" "Bash(./ja notion-changes)" "Bash(./ja notion-saved:*)"
  "Bash(./ja notion-report)" "Bash(./ja report --no-send)"
  mcp__claude_ai_Gmail__get_thread mcp__claude_ai_Gmail__create_draft
  mcp__claude_ai_Notion__notion-create-pages mcp__claude_ai_Notion__notion-update-page)
if [[ "$AUTO_SEND" == 1 ]]; then
  DELIVER_TOOLS+=(mcp__claude_ai_Gmail__send_message "Bash(./ja mark-sent:*)")
fi

{
  echo "=== $(date) ==="
  if [[ -f PAUSE ]]; then echo "PAUSE file present — skipping run"; exit 0; fi
  echo "Preparing packets (this takes a while — Claude is researching and writing)…"

  if [[ "$1" != "deliver" ]]; then   # ./scripts/run.sh deliver → only send emails + update Notion
  ./ja daily
  claude -p "/job-autopilot prepare" --allowedTools "${PREPARE_TOOLS[@]}" --output-format json > out/run-prepare.json
  echo "Checking every field + resume before submitting (nothing is submitted in this step)…"
  ./ja apply-check --no-all-fields
  ./ja apply-run --limit 50    # everything prepared this run; you solve CAPTCHAs, then press Enter
  fi
  claude -p "/job-autopilot deliver auto_send=$AUTO_SEND" --allowedTools "${DELIVER_TOOLS[@]}" --output-format json > out/run-deliver.json

  # Token usage for this run (from Claude Code's own run summary)
  for f in out/run-prepare.json out/run-deliver.json; do
    python3 -c "import json,sys; d=json.load(open('$f')); u=d.get('usage',{}); print('$f', 'tokens in/out/cache-read:', u.get('input_tokens'), u.get('output_tokens'), u.get('cache_read_input_tokens'), '| turns:', d.get('num_turns'))" 2>/dev/null
  done
} 2>&1 | tee -a out/run.log

osascript -e 'display notification "Run done — check Notion" with title "Job agent"' 2>/dev/null
