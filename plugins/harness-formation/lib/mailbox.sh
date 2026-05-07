#!/bin/bash
# mailbox.sh - jsonl append-only inter-pane message bus
# Sourced by bin/formation. Not meant to be executed directly.

NJSLYR_HOME="${NJSLYR_HOME:-$HOME/.njslyr7}"
MAILBOX_DIR="$NJSLYR_HOME/mailbox"
MAILBOX_LOG="$MAILBOX_DIR/log.jsonl"
MAILBOX_CURSOR_DIR="$MAILBOX_DIR/cursor"
MAILBOX_LOCK="$MAILBOX_DIR/.lock"

# shellcheck source=redact.sh
_mailbox_redact_lib="$(dirname "${BASH_SOURCE[0]}")/redact.sh"
[[ -f "$_mailbox_redact_lib" ]] && source "$_mailbox_redact_lib"

mailbox_init() {
  mkdir -p "$MAILBOX_DIR" "$MAILBOX_CURSOR_DIR"
  touch "$MAILBOX_LOG"
}

mailbox_send() {
  local from="$1" to="$2" body="$3"
  mailbox_init
  if declare -f is_credential_like >/dev/null && is_credential_like "$body"; then
    echo "mailbox: refusing to send — body matches credential pattern." >&2
    echo "mailbox: reference a SOPS-encrypted file instead (e.g. 'sops -d path/secrets.enc.yaml')." >&2
    return 3
  fi
  local ts seq line
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  (
    flock -x 200
    seq="$(wc -l < "$MAILBOX_LOG" | tr -d ' ')"
    seq=$((seq + 1))
    line=$(jq -cn --arg seq "$seq" --arg ts "$ts" --arg from "$from" \
                 --arg to "$to" --arg body "$body" \
                 '{seq: ($seq|tonumber), ts: $ts, from: $from, to: $to, body: $body}')
    echo "$line" >> "$MAILBOX_LOG"
  ) 200>"$MAILBOX_LOCK"
}

mailbox_read() {
  local self="$1"
  mailbox_init
  local cursor_file="$MAILBOX_CURSOR_DIR/$self.txt"
  local after=0
  [[ -f "$cursor_file" ]] && after="$(cat "$cursor_file")"
  jq -c --arg self "$self" --argjson after "$after" \
        'select(.to == $self and .seq > $after)' "$MAILBOX_LOG"
}

mailbox_mark_read() {
  local self="$1"
  mailbox_init
  local cursor_file="$MAILBOX_CURSOR_DIR/$self.txt"
  local latest
  latest="$(jq -s --arg self "$self" 'map(select(.to == $self)) | (last.seq // 0)' "$MAILBOX_LOG")"
  echo "$latest" > "$cursor_file"
}

mailbox_tail() {
  mailbox_init
  tail -F "$MAILBOX_LOG"
}
