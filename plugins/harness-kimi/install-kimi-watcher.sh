#!/bin/bash
# install-kimi-watcher.sh — install the detective wire.jsonl watcher (gh #53).
#
# Per-user crontab, every minute. Mirrors install-kimi-scrubber.sh.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHER="$HERE/kimi_wire_watcher.sh"
CRON_MARKER="# kimi-harness-wire-watcher"

if [ ! -f "$WATCHER" ]; then
    echo "error: watcher not found: $WATCHER" >&2
    exit 1
fi
chmod +x "$WATCHER"

if ! command -v discord-bot >/dev/null 2>&1; then
    echo "[harness-kimi] note: discord-bot not on PATH — GAP alerts will be logged" >&2
    echo "  to $HOME/.kimi-code/harness-guard/watcher.log only." >&2
fi

CRON_LINE="* * * * * $WATCHER $CRON_MARKER"

CURRENT=$(crontab -l 2>/dev/null || true)
NEW=$(printf '%s\n' "$CURRENT" | grep -vF "$CRON_MARKER" || true)
NEW="$NEW
$CRON_LINE
"
printf '%s\n' "$NEW" | crontab -

echo "installed crontab entry:"
echo "$CRON_LINE"
echo ""
echo "Detective 2nd wall active. GAP alerts → discord-bot ($CRON_MARKER) + watcher.log."
echo "To disable temporarily: touch ~/.kimi-code/harness-watch.disabled"
echo "To uninstall: ./uninstall-kimi-watcher.sh"
