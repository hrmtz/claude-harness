#!/bin/bash
# kimi_session_scrub.sh — cron/systemd wrapper for kimi_session_scrub.py
#
# Fail-safe: any error exits 0 so cron does not spam the user.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/kimi_session_scrub.py"
LOG="$HOME/.kimi-code/harness-scrub.log"

# Dependency preflight
command -v python3 >/dev/null 2>&1 || exit 0
[ -f "$PY" ] || exit 0

mkdir -p "$(dirname "$LOG")"

exec python3 "$PY" >>"$LOG" 2>&1
