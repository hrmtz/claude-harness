#!/bin/bash
# uninstall-kimi-scrubber.sh — remove the Kimi session scrubber cron entry.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRUBBER="$HERE/kimi_session_scrub.sh"
CRON_MARKER="# kimi-harness-session-scrubber"

CURRENT=$(crontab -l 2>/dev/null || true)
# Remove both the new inline-marked line and any legacy two-line entry.
NEW=$(printf '%s\n' "$CURRENT" | grep -vF "$CRON_MARKER" | grep -vF "$SCRUBBER" || true)

printf '%s\n' "$NEW" | crontab -
echo "removed kimi-harness scrubber cron entry"
