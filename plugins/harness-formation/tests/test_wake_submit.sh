#!/usr/bin/env bash
# Verify the single text-injection primitive uses delayed double Enter.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

tmux() {
  printf 'tmux %s\n' "$*" >> "$LOG"
  case "${1:-}" in
    display-message) printf '0\n' ;;
    load-buffer) cat >/dev/null ;;
    paste-buffer) [[ "${FAIL_PASTE:-0}" != "1" ]] ;;
    send-keys) [[ "${FAIL_ENTER:-0}" != "1" ]] ;;
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
tmux_send_submit %42 "mailbox message"
assert_submit_contract tmux_send_submit

if FAIL_PASTE=1 tmux_send_submit %42 "paste failure"; then
  echo "FAIL: tmux_send_submit hid a paste-buffer failure" >&2
  exit 1
else
  paste_failure_rc=$?
fi
[[ "$paste_failure_rc" -eq "$TMUX_SUBMIT_NOT_PASTED" ]] || {
  echo "FAIL: pre-paste failure was misclassified as non-retryable" >&2
  exit 1
}
grep -Fq 'tmux delete-buffer -b ' "$LOG" || {
  echo "FAIL: failed paste left its tmux buffer allocated" >&2
  exit 1
}

if FAIL_ENTER=1 tmux_send_submit %42 "submit failure"; then
  echo "FAIL: tmux_send_submit hid a post-paste submit failure" >&2
  exit 1
else
  submit_failure_rc=$?
fi
[[ "$submit_failure_rc" -eq "$TMUX_SUBMIT_PASTED_UNCONFIRMED" ]] || {
  echo "FAIL: post-paste submit failure was not classified as non-retryable" >&2
  exit 1
}

if declare -F wake_pane >/dev/null || declare -F wake_paste >/dev/null; then
  echo "FAIL: legacy raw/paste wake entrypoints are still exported" >&2
  exit 1
fi
if grep -Eq '^[[:space:]]*tmux send-keys -l' "$HERE/../lib/wake.sh"; then
  echo "FAIL: wake.sh still contains raw prompt text injection" >&2
  exit 1
fi

echo "test_wake_submit: 5 passed, 0 failed"
