#!/bin/bash
# uninstall-kimi-watcher.sh — remove the Kimi wire.jsonl watcher cron entry.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHER="$HERE/kimi_wire_watcher.sh"
CRON_MARKER="# kimi-harness-wire-watcher"

CURRENT=$(crontab -l 2>/dev/null || true)
NEW=$(printf '%s\n' "$CURRENT" | grep -vF "$CRON_MARKER" | grep -vF "$WATCHER" || true)

printf '%s\n' "$NEW" | crontab -
echo "removed kimi-harness wire-watcher cron entry"
