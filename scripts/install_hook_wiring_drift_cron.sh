#!/bin/bash
# Install the nightly, host-local hook wiring drift observer (gh #186).

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$REPO/scripts/run_hook_wiring_drift_cron.sh"
MARKER="# harness_hook_wiring_drift_nightly"
LOG="$HOME/.local/log/hook_wiring_drift.log"
BACKUP="$HOME/sanada_backup_persistent/hook_wiring_drift_cron_$(date +%Y%m%d_%H%M%S)"

test -x "$RUNNER" || {
    printf 'error: runner is not executable: %s\n' "$RUNNER" >&2
    exit 1
}

mkdir -p "$BACKUP" "$(dirname "$LOG")"
crontab -l > "$BACKUP/crontab.before" 2>/dev/null || true

cron_line="35 4 * * * /usr/bin/flock -n /tmp/harness_hook_wiring_drift.lock bash $RUNNER >> $LOG 2>&1 $MARKER"
current="$(crontab -l 2>/dev/null || true)"
without_old="$(printf '%s\n' "$current" | grep -vF "$MARKER" || true)"
printf '%s\n%s\n' "$without_old" "$cron_line" | crontab -

printf 'installed crontab entry:\n%s\n' "$cron_line"
