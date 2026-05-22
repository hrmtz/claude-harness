#!/usr/bin/env bash
# vastai_create_30min_check.sh — PostToolUse Bash hook
#
# When AI runs `vastai create instance`, auto-schedule a +30 min cron entry
# that checks instance status. If still NOT running (= image fail / stuck host
# / scheduler stall), discord-bot notify + emit AI-context reminder.
#
# Override via VASTAI_CHECK_MINUTES env var (default 15).
#
# Why structural: AI behavioral rule "10 min check after create" repeatedly
# fails under cognitive load (= 2026-05-22 incident: 3.5h stuck on image error,
# user had to intervene). Hook rail makes it impossible to skip — cron fires
# regardless of AI session state.
#
# Self-removing: each scheduled entry tags itself + greps itself out of crontab
# after firing once (= per existing 12h_hook_audit pattern).

set -uo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null || true)
[ "$TOOL_NAME" != "Bash" ] && exit 0

CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)
[ -z "$CMD" ] && exit 0

# Trigger: vastai create instance (= ignore destroy, show, search)
echo "$CMD" | grep -qE '\bvastai\s+create\s+instance\b' || exit 0

# Extract new_contract IDs from tool output stdout
OUTPUT=$(echo "$INPUT" | jq -r '.tool_response.stdout // ""' 2>/dev/null || true)
# Patterns: 'new_contract': N  OR  "new_contract": N
IDS=$(echo "$OUTPUT" | grep -oE "['\"]new_contract['\"]:\s*[0-9]+" | grep -oE '[0-9]+' | sort -u)
[ -z "$IDS" ] && exit 0

mkdir -p "$HOME/.local/log"
SCHEDULED=()
CHECK_MIN="${VASTAI_CHECK_MINUTES:-30}"

for id in $IDS; do
  tag="vastai_check_${id}"
  # Skip if already scheduled (= idempotent on retry)
  if crontab -l 2>/dev/null | grep -q "$tag"; then
    continue
  fi
  # Fire +CHECK_MIN from now
  fire_ts=$(date -d "+$CHECK_MIN minutes" '+%M %H %d %m')
  read fmin fhr fdom fmon <<< "$fire_ts"
  # Check cmd: if not running → discord notify + log
  check_cmd="(s=\$(vastai show instance $id --raw 2>/dev/null | jq -r '.actual_status // \"missing\"'); cur=\$(vastai show instance $id --raw 2>/dev/null | jq -r '.cur_state // \"?\"'); if [ \"\$s\" != \"running\" ]; then discord-bot post PRS-LLM \"⚠️ vastai $id +${CHECK_MIN}min status=\$s cur=\$cur — STUCK? destroy + re-contract if image fail or host overcommit\" 2>/dev/null; else discord-bot post PRS-LLM \"✅ vastai $id +${CHECK_MIN}min running OK\" 2>/dev/null; fi; (crontab -l 2>/dev/null | grep -v '$tag') | crontab -) >> $HOME/.local/log/vastai_30min.log 2>&1 # $tag"
  (crontab -l 2>/dev/null; echo "$fmin $fhr $fdom $fmon * $check_cmd") | crontab -
  SCHEDULED+=("$id")
done

if [ ${#SCHEDULED[@]} -gt 0 ]; then
  cat >&2 <<EOF
⏱ vastai_create_30min_check: scheduled +${CHECK_MIN} min auto-check for instances: ${SCHEDULED[*]}
  - cron entry will fire once + self-remove (tag=vastai_check_<id>)
  - Discord notify on stuck (status != running)
  - log: $HOME/.local/log/vastai_30min.log
  - if STUCK at +${CHECK_MIN}min: destroy + re-contract from different offer, host overcommit pattern
EOF
fi
exit 0
