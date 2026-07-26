#!/bin/bash
# mailbox.sh - jsonl append-only inter-pane message bus
# Sourced by bin/formation. Not meant to be executed directly.

FORMATION_HOME="${FORMATION_HOME:-$HOME/.formation}"
MAILBOX_LOG="${FORMATION_MAILBOX:-$FORMATION_HOME/mailbox/log.jsonl}"
MAILBOX_DIR="$(dirname "$MAILBOX_LOG")"
MAILBOX_CURSOR_DIR="$MAILBOX_DIR/cursor"
# Every writer must lock this exact path. The old mailbox.sh and mailbox-send
# used two different lock files for the same JSONL, permitting duplicate seqs.
MAILBOX_LOCK="${MAILBOX_LOG}.lock"
MAILBOX_SEQ_STATE="${MAILBOX_LOG}.seq"

# shellcheck source=redact.sh
_mailbox_redact_lib="$(dirname "${BASH_SOURCE[0]}")/redact.sh"
[[ -f "$_mailbox_redact_lib" ]] && source "$_mailbox_redact_lib"

mailbox_init() {
  mkdir -p "$MAILBOX_DIR" "$MAILBOX_CURSOR_DIR"
  touch "$MAILBOX_LOG"
}

mailbox_append() {
  local from="$1" to="$2" body="$3"
  # session_id: 4th arg > env FORMATION_SESSION_ID > empty.
  # Lets readers distinguish same-codename agents across sessions.
  local session_id="${4:-${FORMATION_SESSION_ID:-}}"
  local force="${5:-0}"
  mailbox_init
  if [[ "$force" != "1" ]] &&
     declare -f is_credential_like >/dev/null &&
     is_credential_like "$body"; then
    echo "mailbox: refusing to send — body matches credential pattern." >&2
    echo "mailbox: reference a SOPS-encrypted file instead (e.g. 'sops exec-env path/secrets.enc.yaml <cmd>')." >&2
    return 3
  fi
  local ts seq line
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  (
    flock -x 200
    # Ignore malformed historical rows and derive seq from the largest valid
    # numeric value rather than line count.
    local log_max state_max=0 state_tmp
    log_max="$(jq -Rsc '[splits("\n") | fromjson? | .seq? | numbers] | max // 0' "$MAILBOX_LOG")"
    if [[ -f "$MAILBOX_SEQ_STATE" ]]; then
      state_max="$(cat "$MAILBOX_SEQ_STATE" 2>/dev/null || echo 0)"
      [[ "$state_max" =~ ^[0-9]+$ ]] || state_max=0
    fi
    if [[ "$state_max" -gt "$log_max" ]]; then seq="$state_max"; else seq="$log_max"; fi
    seq=$((seq + 1))
    line=$(jq -cn --arg seq "$seq" --arg ts "$ts" --arg from "$from" \
                 --arg to "$to" --arg body "$body" --arg sid "$session_id" \
                 '{seq: ($seq|tonumber), ts: $ts, from: $from, to: $to, body: $body,
                   session_id: (if $sid == "" then null else $sid end)}')
    echo "$line" >> "$MAILBOX_LOG"
    state_tmp="${MAILBOX_SEQ_STATE}.$$"
    printf '%s\n' "$seq" > "$state_tmp"
    mv "$state_tmp" "$MAILBOX_SEQ_STATE"
    printf '%s\n' "$seq"
  ) 200>"$MAILBOX_LOCK"
}

mailbox_send() {
  mailbox_append "$@" >/dev/null
}

mailbox_read() {
  local self="$1"
  local pane_alias="${2:-}"
  mailbox_init
  local cursor_file="$MAILBOX_CURSOR_DIR/$self.txt"
  local after=0
  [[ -f "$cursor_file" ]] && after="$(cat "$cursor_file")"
  [[ "$after" =~ ^[0-9]+$ ]] || after=0
  jq -Rrc --arg self "$self" --arg pane "$pane_alias" --argjson after "$after" \
        'fromjson?
         | select((.to == $self or ($pane != "" and .to == $pane))
                  and ((.seq? | type) == "number") and .seq > $after)' "$MAILBOX_LOG"
}

mailbox_mark_read() {
  local self="$1"
  local through="${2:-}"
  local pane_alias="${3:-}"
  mailbox_init
  local cursor_file="$MAILBOX_CURSOR_DIR/$self.txt"
  if [[ -z "$through" ]]; then
    through="$(jq -Rsc --arg self "$self" --arg pane "$pane_alias" \
      '[splits("\n") | fromjson?
        | select(.to == $self or ($pane != "" and .to == $pane))
        | .seq? | numbers] | max // 0' "$MAILBOX_LOG")"
  fi
  local cursor_tmp="${cursor_file}.$$"
  printf '%s\n' "$through" > "$cursor_tmp"
  mv "$cursor_tmp" "$cursor_file"
}

mailbox_tail() {
  mailbox_init
  tail -F "$MAILBOX_LOG"
}
