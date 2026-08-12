#!/usr/bin/env bash
# Verify Kimi bootstrap retries a seed erased during first-session startup.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

PASTE_COUNT=0
tmux() {
  printf 'tmux %s\n' "$*" >> "$LOG"
  case "${1:-}" in
    display-message) printf '0\n' ;;
    load-buffer) cat >/dev/null ;;
    paste-buffer) PASTE_COUNT=$((PASTE_COUNT + 1)) ;;
    capture-pane)
      if [[ "$PASTE_COUNT" -ge 2 ]]; then
        printf 'Session: session_retry_succeeded\n'
      else
        printf 'Session:\nNo session yet\n'
      fi
      ;;
  esac
}
sleep() { :; }

# shellcheck source=/dev/null
source "$HERE/../lib/wake.sh"

FORMATION_KIMI_SEED_ATTEMPTS=3
FORMATION_KIMI_SEED_CONFIRM_CHECKS=1
tmux_send_kimi_bootstrap %42 "Formation bootstrap. test seed"

[[ "$PASTE_COUNT" -eq 2 ]] || {
  echo "FAIL: vanished first seed was not pasted exactly once more" >&2
  exit 1
}
[[ "$(grep -c 'paste-buffer' "$LOG")" -eq 2 ]] || {
  echo "FAIL: unexpected Kimi paste count" >&2
  exit 1
}

echo "test_kimi_seed_submit: 2 passed, 0 failed"
