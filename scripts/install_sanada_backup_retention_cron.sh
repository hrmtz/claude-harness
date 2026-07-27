#!/bin/bash
# Install the approved Sanada retention policy from the durable primary checkout.

set -euo pipefail

INVOKED_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL_REPO="$(
    git -C "$INVOKED_REPO" worktree list --porcelain 2>/dev/null \
        | sed -n '/^worktree /{s///;p;q;}'
)"
[ -n "$CANONICAL_REPO" ] || {
    printf 'error: cannot resolve canonical checkout from %s\n' "$INVOKED_REPO" >&2
    exit 1
}
CANONICAL_REPO="$(cd "$CANONICAL_REPO" && pwd -P)"
RUNNER="$CANONICAL_REPO/scripts/sanada_backup_retention.py"
MARKER="# sanada_backup_retention_daily"
LOG="$HOME/.local/log/sanada_backup_retention_cron.log"
BACKUP="$HOME/sanada_backup_persistent/sanada_retention_cron_$(date +%Y%m%d_%H%M%S)"
CRONTAB_BIN="${HARNESS_CRONTAB_BIN:-crontab}"

test -x "$RUNNER" || {
    printf 'error: runner is not executable: %s\n' "$RUNNER" >&2
    exit 1
}

mkdir -p "$BACKUP" "$(dirname "$LOG")"
"$CRONTAB_BIN" -l > "$BACKUP/crontab.before" 2>/dev/null || true

cron_line="50 4 * * * /usr/bin/flock -n /tmp/sanada_backup_retention.lock python3 $RUNNER --apply >> $LOG 2>&1 $MARKER"
current="$("$CRONTAB_BIN" -l 2>/dev/null || true)"
without_old="$(printf '%s\n' "$current" | grep -vF "$MARKER" || true)"
printf '%s\n%s\n' "$without_old" "$cron_line" | "$CRONTAB_BIN" -

printf 'installed crontab entry:\n%s\n' "$cron_line"
