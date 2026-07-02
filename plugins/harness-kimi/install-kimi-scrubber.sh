#!/bin/bash
# install-kimi-scrubber.sh — install a cron job that periodically scrubs Kimi sessions.
#
# Uses per-user crontab as the default backend (universal, no systemd needed).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRUBBER="$HERE/kimi_session_scrub.sh"
CRON_MARKER="# kimi-harness-session-scrubber"

if [ ! -f "$SCRUBBER" ]; then
    echo "error: scrubber not found: $SCRUBBER" >&2
    exit 1
fi
chmod +x "$SCRUBBER"

# Build the cron line (every minute). Marker is appended inline so uninstall
# can reliably remove the whole line.
CRON_LINE="* * * * * $SCRUBBER $CRON_MARKER"

# Get current crontab or empty.
CURRENT=$(crontab -l 2>/dev/null || true)

# Strip any existing kimi-harness scrubber lines (handles both old two-line
# format and new inline format).
NEW=$(printf '%s\n' "$CURRENT" | grep -vF "$CRON_MARKER" || true)

# Append fresh entry.
NEW="$NEW
$CRON_LINE
"

printf '%s\n' "$NEW" | crontab -
echo "installed crontab entry:"
echo "$CRON_LINE"
echo ""
echo "To disable temporarily: touch ~/.kimi-code/harness-scrub.disabled"
echo "To uninstall: ./uninstall-kimi-scrubber.sh"
