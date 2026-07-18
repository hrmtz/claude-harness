#!/usr/bin/env bash
# test_inv7_lock.sh — INV-7: the flock guard.
#
# Design §4.1/§4.4. Tests BOTH sides: an unheld lock proceeds, a held lock is refused.
# A test that only checked the refusal side would PASS on a guard that refuses everything --
# exactly the ordering bug a dual-magi round found in the hand-rolled predecessor.
#
# The adapter locks a realpath(doc)-digest under <doc-dir>/.dual-magi, independent of out-prefix.
set -uo pipefail
export MAGI_TEST_ALLOW_NEW_CAMPAIGN=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="$HERE/../scripts/magi_xfamily_claude.sh"
GUARD="$HERE/../scripts/magi_campaign_guard.py"
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

# 5. the adapter exits 3 when its OWN document lock path is held, and never reaches the CLI
STATE="$TMP/state"; mkdir -p "$STATE"
DOC="$TMP/design.md"; printf '%s\n' 'a design' > "$DOC"
DOC2="$TMP/design-two.md"; printf '%s\n' 'another design' > "$DOC2"
seed_xfamily() {
    local doc="$1" state="$2" control id sha prior source source_sha claim_line claim_id
    mkdir -p "$state"
    control="$(dirname "$(realpath "$doc")")/.dual-magi"
    id="$(printf '%s' "$(realpath "$doc")" | sha256sum | cut -c1-16)"
    if [ -f "$control/CAMPAIGN.$id.json" ]; then
        python3 "$GUARD" new-campaign "$doc" --operator test --reason 'independent lock fixture' \
            >/dev/null || return 1
    fi
    claim_line="$(python3 "$GUARD" claim "$doc" 1 fanout "$state")" || return 1
    claim_id="${claim_line##*CLAIM_ID=}"
    python3 "$GUARD" finish "$doc" "$claim_id" success >/dev/null || return 1
    sha="$(sha256sum "$doc" | cut -d' ' -f1)"
    source="$state/round_1_source.json"
    printf '{"reviewer":"SOURCE","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["fixture"],"source_artifacts":[],"dispositions":[],"findings":[]}\n' \
        "$id" "$sha" > "$source"
    source_sha="$(sha256sum "$source" | cut -d' ' -f1)"
    prior="$state/round_1_codex.json"
    printf '{"reviewer":"SYNTHESIS","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["fixture"],"source_artifacts":[{"path":"%s","sha256":"%s"}],"dispositions":[],"findings":[]}\n' \
        "$id" "$sha" "$(basename "$source")" "$source_sha" > "$prior"
    printf '%s\n' "$prior"
}
doc_lock() {
    local real lock_id
    real="$(realpath "$1")"; lock_id="$(printf '%s' "$real" | sha256sum | cut -c1-16)"
    printf '%s/.dual-magi/.review.%s.lock\n' "$(dirname "$real")" "$lock_id"
}
DOC_LOCK="$(doc_lock "$DOC")"
mkdir -p "$(dirname "$DOC_LOCK")"
PRIOR="$(seed_xfamily "$DOC" "$STATE")" || exit 1
h=$(hold "$DOC_LOCK" 10); sleep 0.7
log="$TMP/adapter.log"
MAGI_XFAMILY_TIMEOUT_S=5 timeout 20 setsid "$ADAPTER" "$DOC" 2 "$PRIOR" "$STATE/out" \
    >"$log" 2>&1 </dev/null
rc=$?
release "$h"
[ "$rc" -eq 3 ] && ok "adapter exits 3 under held lock" || bad "adapter exit was $rc, expected 3"
grep -q "lock held" "$log" && ok "adapter reported the lock, did not invoke the CLI" \
                           || bad "adapter did not report a held lock: $(cat "$log")"
[ -e "$STATE/out.json" ] && bad "adapter wrote findings despite the lock" \
                         || ok "no findings written under a held lock"

# 6. Same doc + a different out-prefix still shares the document lock.
OTHER_STATE="$TMP/other-campaign"; PRIOR="$(seed_xfamily "$DOC" "$OTHER_STATE")" || exit 1
h=$(hold "$DOC_LOCK" 10); sleep 0.7
MAGI_XFAMILY_TIMEOUT_S=5 timeout 20 setsid "$ADAPTER" "$DOC" 2 "$PRIOR" "$OTHER_STATE/out" \
    >/dev/null 2>&1 </dev/null
[ $? -eq 3 ] && ok "same doc in another campaign is mutually excluded" \
              || bad "same doc escaped the lock via another out-prefix"

# 7. Different docs may proceed even when they share one output directory.
PRIOR2="$(seed_xfamily "$DOC2" "$STATE")" || exit 1
rc_other=0
env -i PATH=/usr/bin:/bin HOME="$HOME" timeout 15 \
    "$ADAPTER" "$DOC2" 2 "$PRIOR2" "$STATE/other-doc" >/dev/null 2>&1 </dev/null || rc_other=$?
release "$h"
[ "$rc_other" -eq 2 ] && ok "different doc proceeds past its independent lock" \
                       || bad "different doc collided with lock (rc=$rc_other)"

# 8. adapter proceeds PAST the lock when it is free (it must not self-block on the first call).
#    The `claude not found` check sits AFTER the lock acquire, so reaching exit 2 with a
#    stripped PATH proves the lock was taken, not that the guard aborted. (Arg validation
#    happens BEFORE the lock, so a missing-doc exit 64 would prove nothing.)
PRIOR="$(seed_xfamily "$DOC" "$STATE")" || exit 1
rc2=0
env -i PATH=/usr/bin:/bin HOME="$HOME" timeout 15 \
    "$ADAPTER" "$DOC" 2 "$PRIOR" "$STATE/out2" >/dev/null 2>&1 </dev/null || rc2=$?
if [ "$rc2" -eq 2 ]; then
    ok "unlocked adapter acquires the lock and reaches the prereq check (exit 2)"
elif [ "$rc2" -eq 3 ]; then
    bad "unlocked adapter exited 3: the guard blocks its own first call"
else
    bad "unlocked adapter exit was $rc2, expected 2"
fi

# 9. A provider failure after prompt creation must leave TMPDIR empty via the single EXIT trap.
PROMPT_TMP="$TMP/prompt-tmp"; FAIL_BIN="$TMP/fail-bin"
mkdir -p "$PROMPT_TMP" "$FAIL_BIN" "$TMP/cleanup-state"
PRIOR="$(seed_xfamily "$DOC" "$TMP/cleanup-state")" || exit 1
cat > "$FAIL_BIN/claude" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$FAIL_BIN/claude"
TMPDIR="$PROMPT_TMP" PATH="$FAIL_BIN:/usr/bin:/bin" MAGI_XFAMILY_TIMEOUT_S=5 \
    timeout 15 "$ADAPTER" "$DOC" 2 "$PRIOR" "$TMP/cleanup-state/out" >/dev/null 2>&1 </dev/null
cleanup_rc=$?
if [ "$cleanup_rc" -eq 2 ] && ! find "$PROMPT_TMP" -mindepth 1 -print -quit | grep -q .; then
    ok "provider failure cleans prompt/raw temp files through EXIT trap"
else
    bad "provider failure cleanup failed (rc=$cleanup_rc)"
fi

echo "test_inv7_lock: $pass passed, $fail failed"
exit $((fail > 0))
