# shellcheck shell=bash
# guard-env.sh — harness-kimi BASH_ENV interception layer (issue #52).
#
# kimi-wrapper.sh exports BASH_ENV pointing at this file. bash sources it at
# the start of EVERY non-interactive shell in the Kimi process tree —
# including `/bin/bash -c` invoked by ABSOLUTE PATH, which bypasses the PATH
# shim (guarded-bash.sh). $BASH_EXECUTION_STRING carries the full `-c`
# command string, so shell constructs (pipes, loops, redirections) are fully
# visible to the guards, and a deny here aborts BEFORE the command runs.
#
# Constraints for this file (it is SOURCED into the guarded shell):
#   - no `set -e/-u/-o`, no shopt changes, no stray variables/functions left
#     behind — the command's shell must be indistinguishable from unguarded.
#   - guard only `-c` invocations (BASH_EXECUTION_STRING set), only when
#     enabled, and never recursively (HARNESS_KIMI_GUARD_ACTIVE sentinel).
#   - fail-open with a loud stderr warning if guard-check.sh is missing:
#     this is a rail against the agent's own mistakes, not a sandbox, and
#     bricking every Bash call is worse than running unguarded.

if [[ -n "${BASH_EXECUTION_STRING:-}" \
      && "${HARNESS_KIMI_BASH_GUARD:-0}" == "1" \
      && "${HARNESS_KIMI_GUARD_ACTIVE:-0}" != "1" ]]; then
    # NOTE: do NOT `export HARNESS_KIMI_GUARD_ACTIVE` into this (the guarded)
    # shell — it would be inherited by the allowed command and every descendant
    # process, permanently disabling the guard for the rest of that process
    # tree (code-review #52 finding). The sentinel is needed ONLY so the
    # guard-check subprocess (a non-interactive bash that re-sources this file)
    # skips re-entry, so pass it inline on that one invocation. Each nested
    # `bash -c` the command spawns is then independently re-guarded, as intended.
    _hk_guard_check="${HARNESS_KIMI_GUARD_CHECK:-$HOME/.kimi-code/bin/guarded-bash-dir/guard-check.sh}"
    if [[ -f "$_hk_guard_check" ]]; then
        if ! HARNESS_KIMI_GUARD_CMD="$BASH_EXECUTION_STRING" \
             HARNESS_KIMI_GUARD_ACTIVE=1 \
             "${HARNESS_KIMI_REAL_BASH:-/bin/bash}" "$_hk_guard_check"; then
            unset _hk_guard_check
            exit 2
        fi
    else
        echo "⚠️  harness-kimi guard: $_hk_guard_check missing — running UNGUARDED. Run install-kimi-bash-guard.sh." >&2
    fi
    unset _hk_guard_check
fi
