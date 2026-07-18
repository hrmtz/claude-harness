#!/usr/bin/env bash
# magi_lock.sh — flock(2) helper for the cross-family recursion/concurrency guard (INV-7).
#
# Design: docs/designs/CODEX_MAGI_MIRROR.md §4.1, §4.4
#
# Why flock and not a hand-rolled mkdir+pid+stale protocol: the kernel releases the
# lock when the holding fd closes, including on SIGKILL. That removes TOCTOU, stale
# locks, PID reuse, the rmdir-on-nonempty bug, and races between stale recoverers --
# every defect a dual-magi round found in the hand-rolled version.
#
# Usage (source, do not exec):
#   source magi_lock.sh
#   magi_lock_acquire "$DOC_CONTROL_DIR/.xfamily.${DOC_LOCK_ID}.lock" || exit 3
#
# Known hole (documented, not fixed here): a child process inherits fd 9 and could
# flock it. The real rail is that the reviewer's allowlist omits Bash(codex:*) /
# Bash(claude:*). See §4.4 and tests/test_inv7_lock.sh.

MAGI_LOCK_FD=9

# magi_lock_acquire <lock-file>
#   returns 0 on acquisition, 1 if another live holder has it (caller should exit 3)
magi_lock_acquire() {
    local lock_file="$1"
    [ -n "$lock_file" ] || { echo "magi_lock: lock file path required" >&2; return 1; }
    mkdir -p "$(dirname "$lock_file")" || { echo "magi_lock: cannot create $(dirname "$lock_file")" >&2; return 2; }
    # Distinguish "cannot open the lock file" from "another process holds it": otherwise an
    # unwritable path reports itself as contention and the caller exits 3 with the wrong reason.
    # shellcheck disable=SC3023  # bash supports exec with a variable fd via eval
    if ! eval "exec ${MAGI_LOCK_FD}>\"\$lock_file\"" 2>/dev/null; then
        echo "magi_lock: cannot open lock file: $lock_file" >&2
        return 2
    fi
    flock -n "$MAGI_LOCK_FD"
}
