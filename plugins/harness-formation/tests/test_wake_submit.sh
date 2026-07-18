#!/usr/bin/env bash
# Verify every text-injection path uses delayed double Enter without real tmux.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$(mktemp)"
PAYLOAD="$(mktemp)"
trap 'rm -f "$LOG" "$PAYLOAD"' EXIT

tmux() {
  printf 'tmux %s\n' "$*" >> "$LOG"
  case "${1:-}" in
    list-panes) printf '%%42\n' ;;
    display-message) printf '0\n' ;;
  esac
}
sleep() { printf 'sleep %s\n' "$1" >> "$LOG"; }

# shellcheck source=/dev/null
source "$HERE/../lib/wake.sh"

assert_submit_contract() {
  local label="$1"
  local expected actual
  expected=$'sleep 0.4\ntmux send-keys -t %42 Enter\nsleep 0.5\ntmux send-keys -t %42 Enter'
  actual="$(tail -n 4 "$LOG")"
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: $label did not end with delayed double-submit" >&2
    printf 'actual:\n%s\n' "$actual" >&2
    exit 1
  fi
}

: > "$LOG"
wake_pane %42 "check inbox"
assert_submit_contract wake_pane

printf 'pasted note' > "$PAYLOAD"
: > "$LOG"
wake_paste %42 "$PAYLOAD"
assert_submit_contract wake_paste

: > "$LOG"
tmux_send_submit %42 "mailbox message"
assert_submit_contract tmux_send_submit

echo "test_wake_submit: 3 passed, 0 failed"
