#!/bin/bash
# guarded-bash.sh — drop-in bash replacement for Kimi sessions.
#
# When HARNESS_KIMI_BASH_GUARD=1 is set, this script intercepts `bash -c '...'`
# invocations and runs Claude harness-core PreToolUse guards before executing
# the real bash. It is intended to add structural safety to Kimi without a
# native hook API.
#
# Safety properties:
#   - Only activates when HARNESS_KIMI_BASH_GUARD=1.
#   - Requires HARNESS_KIMI_REAL_BASH to point to the real bash binary.
#   - Guards are applied only to `-c` command invocations.
#   - Non-`-c` invocations (interactive shells, script files) pass through.
#   - Hooks are run under HARNESS_KIMI_GUARD_ACTIVE=1 so nested bash calls
#     inside the guards do not recurse back into this wrapper.
#
# Limitations:
#   - Can only intercept bash calls that resolve through PATH. If Kimi invokes
#     /bin/bash by absolute path, this wrapper is bypassed — that case is
#     covered by the BASH_ENV layer (guard-env.sh, issue #52). Both layers
#     delegate to the shared guard core (guard-check.sh).
#   - Write/Edit/Read tool guards cannot be enforced at the shell level.
#   - This is defense in depth, not a guarantee.

set -uo pipefail

REAL_BASH="${HARNESS_KIMI_REAL_BASH:-/bin/bash}"
GUARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# If not enabled, or already inside a guard execution, pass through.
if [[ "${HARNESS_KIMI_BASH_GUARD:-0}" != "1" ]]; then
    exec "$REAL_BASH" "$@"
fi

# Extract a `-c` command from bash arguments using getopts, so combined
# options like `-lc 'cmd'` are handled the same as `-c 'cmd'`.
CMD=""
while getopts ":c:" opt; do
    case "$opt" in
        c) CMD="$OPTARG" ;;
        *) ;;
    esac
done

# No -c command: pass through unchanged.
if [[ -z "$CMD" ]]; then
    exec "$REAL_BASH" "$@"
fi

# Delegate hook execution to the shared guard core (also used by guard-env.sh).
GUARD_CHECK="${HARNESS_KIMI_GUARD_CHECK:-$HOME/.kimi-code/bin/guarded-bash-dir/guard-check.sh}"
if [[ -f "$GUARD_CHECK" ]]; then
    HARNESS_KIMI_GUARD_CMD="$CMD" "$REAL_BASH" "$GUARD_CHECK"
    rc=$?
    if [[ $rc -ne 0 ]]; then
        exit $rc
    fi
else
    echo "⚠️  harness-kimi guard: $GUARD_CHECK missing — running UNGUARDED. Run install-kimi-bash-guard.sh." >&2
fi

# ── execute the real bash with original arguments ──
exec "$REAL_BASH" "$@"
