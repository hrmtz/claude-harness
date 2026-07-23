#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$HERE/../hooks/formation_suggest.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

payload() {
  jq -nc --arg prompt "$1" '{
    hook_event_name: "UserPromptSubmit",
    prompt: $prompt,
    session_id: "formation-suggest-test"
  }'
}

# The plugin must work from an isolated cache-like directory with no sibling
# harness-core checkout.
mkdir -p "$TMP/plugin/hooks"
cp "$HOOK" "$TMP/plugin/hooks/formation_suggest.sh"
ISOLATED_HOOK="$TMP/plugin/hooks/formation_suggest.sh"
LOG="$TMP/formation.log"

default_out="$(
  payload "裏のClaudeに長時間タスクをやらせて" \
    | FORMATION_SUGGEST_LOG="$LOG" \
      bash "$ISOLATED_HOOK"
)"
test "$(printf '%s' "$default_out" | jq -r '.hookSpecificOutput.hookEventName')" = "UserPromptSubmit"
test "$(printf '%s' "$default_out" | jq -r '.hookSpecificOutput.additionalContext')" = "formation skill"
grep -q 'mode=active' "$LOG"

shadow_out="$(
  payload "別セッションのClaudeで並行して" \
    | FORMATION_SUGGEST_MODE=shadow FORMATION_SUGGEST_LOG="$LOG" \
      bash "$ISOLATED_HOOK"
)"
test -z "$shadow_out"
grep -q 'mode=shadow' "$LOG"

quiet_out="$(
  payload "通常の短い質問です" \
    | FORMATION_SUGGEST_MODE=active FORMATION_SUGGEST_LOG="$LOG" \
      bash "$ISOLATED_HOOK"
)"
test -z "$quiet_out"

echo "formation_suggest tests: PASS"
