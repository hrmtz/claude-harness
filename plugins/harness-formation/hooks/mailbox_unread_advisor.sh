#!/usr/bin/env bash
# mailbox_unread_advisor.sh — unread Formation mailを context に注入する。
#
# UserPromptSubmit / SessionStart / PostToolUse 共用。advisory only、全 failure
# は無言で fail-open。session ごとの high-water と30分 cooldownで連発を防ぐ。
#
# PostToolUse (gh #282 の gap A: 自律 turn 中の orchestrator は次の user prompt
# まで新着に気づけない) では tool 境界ごとに formation subprocess を起動しない
# よう、probe 間隔を FORMATION_MAILBOX_ADVISOR_PROBE_INTERVAL 秒 (default 20)
# に絞る。UserPromptSubmit / SessionStart は user 駆動で低頻度なので毎回 probe。

set -uo pipefail

payload=$(head -c 65536 2>/dev/null || true)

event="$(printf '%s' "$payload" |
  jq -r '.hook_event_name // empty' 2>/dev/null || true)"

session="$(printf '%s' "$payload" |
  jq -r '.session_id // empty' 2>/dev/null || true)"
[[ -n "$session" ]] || session="ppid-$PPID"
session="${session//[^a-zA-Z0-9_-]/_}"

state_dir="${XDG_RUNTIME_DIR:-/tmp}/claude-mailbox-advisor"
state_file="$state_dir/$session"
umask 077
mkdir -p "$state_dir" 2>/dev/null || exit 0

last_seq=0
last_epoch=0
last_probe=0
if [[ -f "$state_file" ]]; then
  read -r last_seq last_epoch last_probe <"$state_file" 2>/dev/null || true
fi
[[ "$last_seq" =~ ^[0-9]+$ ]] || last_seq=0
[[ "$last_epoch" =~ ^[0-9]+$ ]] || last_epoch=0
[[ "${last_probe:-}" =~ ^[0-9]+$ ]] || last_probe=0
now="$(date +%s 2>/dev/null)" || exit 0
[[ "$now" =~ ^[0-9]+$ ]] || exit 0

probe_interval="${FORMATION_MAILBOX_ADVISOR_PROBE_INTERVAL:-20}"
[[ "$probe_interval" =~ ^[0-9]+$ ]] || probe_interval=20
if [[ "$event" == "PostToolUse" &&
      $((now - last_probe)) -lt "$probe_interval" ]]; then
  exit 0
fi

write_state() {
  local state_tmp="${state_file}.$$"
  printf '%s %s %s\n' "$1" "$2" "$3" >"$state_tmp" 2>/dev/null || return 1
  mv "$state_tmp" "$state_file" 2>/dev/null || {
    rm -f "$state_tmp" 2>/dev/null || true
    return 1
  }
}

formation_bin="$(command -v formation 2>/dev/null || true)"
if [[ -z "$formation_bin" && -n "${HOME:-}" &&
      -x "${HOME}/.local/bin/formation" ]]; then
  formation_bin="${HOME}/.local/bin/formation"
fi
[[ -n "$formation_bin" ]] || exit 0

count_line="$(timeout 3 "$formation_bin" inbox --count 2>/dev/null)" || exit 0
read -r count max_seq oldest_ts extra <<<"$count_line"
[[ -z "${extra:-}" &&
   "${count:-}" =~ ^[0-9]+$ &&
   "${max_seq:-}" =~ ^[0-9]+$ &&
   -n "${oldest_ts:-}" ]] || exit 0

if [[ "$count" -eq 0 ]]; then
  [[ "$event" == "PostToolUse" ]] &&
    write_state "$last_seq" "$last_epoch" "$now"
  exit 0
fi

if [[ "$max_seq" -le "$last_seq" &&
      $((now - last_epoch)) -le 1800 ]]; then
  [[ "$event" == "PostToolUse" ]] &&
    write_state "$last_seq" "$last_epoch" "$now"
  exit 0
fi

msg="📬 formation inbox ${count} 件未読 (最古 ${oldest_ts}) — \`formation inbox\` で読むこと (advisory、gh claude-harness#232)"
if [[ "$event" == "SessionStart" ]]; then
  hook_output="$msg"
elif [[ "$event" == "PostToolUse" ]]; then
  hook_output="$(jq -n --arg msg "$msg" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: $msg
    }
  }' 2>/dev/null)" || exit 0
else
  hook_output="$(jq -n --arg msg "$msg" '{
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: $msg
    }
  }' 2>/dev/null)" || exit 0
fi

write_state "$max_seq" "$now" "$now" || exit 0

printf '%s\n' "$hook_output"

exit 0
