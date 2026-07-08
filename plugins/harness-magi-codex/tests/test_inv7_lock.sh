#!/usr/bin/env bash
# test_inv7_lock.sh — INV-7: the flock guard.
#
# Design §4.1/§4.4. Tests BOTH sides: an unheld lock proceeds, a held lock is refused.
# A test that only checked the refusal side would PASS on a guard that refuses everything --
# exactly the ordering bug a dual-magi round found in the hand-rolled predecessor.
#
# The adapter locks "$STATE_DIR/.xfamily.lock" where STATE_DIR = dirname(out-prefix).
# A holder must lock that exact path; locking a different file lets the adapter through
# and it will invoke a real (slow) claude call. (Learned the hard way.)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="$HERE/../scripts/magi_xfamily_claude.sh"
source "$HERE/../scripts/magi_lock.sh"

TMP="$(mktemp -d)"
pass=0; fail=0
ok()   { echo "  ok   - $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL - $1"; fail=$((fail+1)); }

HOLDER="$TMP/holder.sh"
# `exec sleep`, not `sleep`: a forked child INHERITS fd 9 and keeps the flock alive after the
# parent shell dies, so killing the shell would not release the lock. Exec'ing makes the pid we
# track the very process that holds the fd. (This is the same inherited-fd hole the design
# documents in §4.1 -- it is real, and it bit this test first.)
cat > "$HOLDER" <<'EOF'
exec 9>"$1"; flock -n 9 || exit 1; echo locked; exec sleep "${2:-10}"
EOF

# NB: no setsid here. setsid forks, so $! would be the setsid pid, not the holder's --
# `kill $!` would then miss the real holder, leaving the lock held for every later case
# and making the guard look broken when it is not.
hold() {  # hold <lockfile> <secs> ; echoes the holder's pid
    bash "$HOLDER" "$1" "$2" >/dev/null 2>&1 </dev/null &
    echo $!
}
release() { kill -TERM "$1" 2>/dev/null; wait "$1" 2>/dev/null; }

cleanup() { pkill -f "$HOLDER" 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

LK="$TMP/plain.lock"

# 1. unheld lock -> acquired (the "first legitimate call proceeds" side)
if timeout 10 bash -c 'source "$1"; magi_lock_acquire "$2"' _ "$HERE/../scripts/magi_lock.sh" "$LK"; then
    ok "unheld lock is acquired"
else
    bad "unheld lock was refused (guard aborts every call)"
fi

# 2. held lock -> a concurrent process is refused
h=$(hold "$LK" 8); sleep 0.7
if timeout 10 bash -c 'source "$1"; magi_lock_acquire "$2"' _ "$HERE/../scripts/magi_lock.sh" "$LK" 2>/dev/null; then
    bad "concurrent acquire succeeded (no mutual exclusion)"
else
    ok "concurrent acquire refused"
fi

# 3. SIGKILL the holder -> kernel releases the fd; no stale lock, no recovery code needed
kill -9 "$h" 2>/dev/null; wait "$h" 2>/dev/null; sleep 0.4
if timeout 10 bash -c 'source "$1"; magi_lock_acquire "$2"' _ "$HERE/../scripts/magi_lock.sh" "$LK" 2>/dev/null; then
    ok "lock auto-released after SIGKILL (no stale lock)"
else
    bad "stale lock survived SIGKILL"
fi

# 4. recursion: a descendant that opens its OWN fd is refused
out=$(timeout 10 bash -c '
  exec 9>"$1"; flock -n 9 || { echo outer-failed; exit 1; }
  bash -c '"'"'exec 7>"$1"; if flock -n 7; then echo child-acquired; else echo child-refused; fi'"'"' _ "$1"
' _ "$TMP/rec.lock")
[ "$out" = "child-refused" ] && ok "descendant with its own fd is refused (recursion guard)" \
                             || bad "descendant acquired the lock: '$out'"

# 5. the adapter exits 3 when its OWN lock path is held, and never reaches the CLI
STATE="$TMP/state"; mkdir -p "$STATE"
h=$(hold "$STATE/.xfamily.lock" 10); sleep 0.7
log="$TMP/adapter.log"
MAGI_XFAMILY_TIMEOUT_S=5 timeout 20 setsid "$ADAPTER" "$HERE/../README.md" 1 - "$STATE/out" \
    >"$log" 2>&1 </dev/null
rc=$?
release "$h"
[ "$rc" -eq 3 ] && ok "adapter exits 3 under held lock" || bad "adapter exit was $rc, expected 3"
grep -q "lock held" "$log" && ok "adapter reported the lock, did not invoke the CLI" \
                           || bad "adapter did not report a held lock: $(cat "$log")"
[ -e "$STATE/out.json" ] && bad "adapter wrote findings despite the lock" \
                         || ok "no findings written under a held lock"

# 6. adapter proceeds PAST the lock when it is free (it must not self-block on the first call).
#    The `claude not found` check sits AFTER the lock acquire, so reaching exit 2 with a
#    stripped PATH proves the lock was taken, not that the guard aborted. (Arg validation
#    happens BEFORE the lock, so a missing-doc exit 64 would prove nothing.)
rc2=0
env -i PATH=/usr/bin:/bin HOME="$HOME" timeout 15 \
    "$ADAPTER" "$HERE/../README.md" 1 - "$STATE/out2" >/dev/null 2>&1 </dev/null || rc2=$?
if [ "$rc2" -eq 2 ]; then
    ok "unlocked adapter acquires the lock and reaches the prereq check (exit 2)"
elif [ "$rc2" -eq 3 ]; then
    bad "unlocked adapter exited 3: the guard blocks its own first call"
else
    bad "unlocked adapter exit was $rc2, expected 2"
fi

echo "test_inv7_lock: $pass passed, $fail failed"
exit $((fail > 0))
