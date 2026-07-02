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

# Never exec ourselves: if REAL_BASH resolves back to this shim (e.g. a nested
# guarded launch left HARNESS_KIMI_REAL_BASH pointing at guard_dir/bash), that
# would be an infinite exec loop (code-review #52 finding). Fall back to the
# system bash on the default PATH.
_self="$GUARD_DIR/bash"
if [[ "$REAL_BASH" == "$_self" || "$REAL_BASH" -ef "$_self" ]] 2>/dev/null; then
    REAL_BASH="$(command -v -p bash 2>/dev/null || echo /bin/bash)"
fi

# If not enabled, or already inside a guard execution, pass through.
if [[ "${HARNESS_KIMI_BASH_GUARD:-0}" != "1" ]]; then
    exec "$REAL_BASH" "$@"
fi

# Extract the `-c` command string. getopts mishandles this: options that
# precede -c (`bash -o pipefail -c ...`, `bash --norc -c ...`) make it return
# CMD="" (pass-through, unguarded) or the literal "-c" (code-review #52
# finding). Parse manually the way bash resolves -c: skip leading options
# (accounting for -o/+o/--rcfile/--init-file which take an argument), and take
# the word after `-c` / a combined short cluster ending in `c` (e.g. -lc).
CMD=""
args=("$@")
n=${#args[@]}
i=0
while (( i < n )); do
    a="${args[$i]}"
    case "$a" in
        --) break ;;                       # end of options; rest are operands
        -o|+o|--rcfile|--init-file)        # option that consumes the next word
            i=$(( i + 2 )); continue ;;
        --*) ;;                            # other long option, no operand here
        -*c)                               # short cluster ending in c → next is cmd
            CMD="${args[$(( i + 1 ))]:-}"; break ;;
        -?*) ;;                            # other short cluster, no -c
        *) break ;;                        # first operand (e.g. `bash script.sh`)
    esac
    i=$(( i + 1 ))
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
