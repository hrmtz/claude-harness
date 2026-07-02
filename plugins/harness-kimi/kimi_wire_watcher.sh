#!/bin/bash
# kimi_wire_watcher.sh — cron/systemd wrapper for kimi_wire_watcher.py (gh #53).
#
# Fail-safe: any error exits 0 so cron does not spam the user.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/kimi_wire_watcher.py"
LOG="$HOME/.kimi-code/harness-guard/watcher.log"

command -v python3 >/dev/null 2>&1 || exit 0
[ -f "$PY" ] || exit 0

mkdir -p "$(dirname "$LOG")"

exec python3 "$PY" >>"$LOG" 2>&1
