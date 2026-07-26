#!/bin/bash
# Nightly local observer for Claude hook wiring drift (gh #186).

set -u

REPO="${HARNESS_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CHECKER="${HARNESS_HOOK_DRIFT_CHECKER:-$REPO/scripts/check_hook_wiring_drift.py}"
LOG="${HARNESS_HOOK_DRIFT_LOG:-$HOME/.local/log/hook_wiring_drift.log}"
DISCORD_CHANNEL="${HARNESS_HOOK_DRIFT_DISCORD_CHANNEL:-claude-harness}"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "$(dirname "$LOG")"
printf 'hook-wiring-drift start=%s\n' "$(date -Iseconds)"
python3 "$CHECKER"
status=$?
printf 'hook-wiring-drift end=%s status=%s\n' "$(date -Iseconds)" "$status"

if [ "$status" -ne 0 ] && [ -n "$DISCORD_CHANNEL" ] && command -v discord-bot >/dev/null 2>&1; then
    if [ "$status" -eq 1 ]; then
        summary="hook wiring DRIFT detected"
    else
        summary="hook wiring checker ERROR"
    fi
    timeout 15 discord-bot post "$DISCORD_CHANNEL" \
        "⚠️ $summary on $(hostname -s 2>/dev/null || printf host) — run scripts/check_hook_wiring_drift.py; see $LOG" \
        >/dev/null 2>&1 || true
fi

exit "$status"
