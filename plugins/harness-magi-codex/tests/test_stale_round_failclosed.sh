#!/usr/bin/env bash
# test_stale_round_failclosed.sh — a failed re-run must not leave the PREVIOUS round certifiable.
#
# Regression for the worst defect the implementation bug-hunt found:
#   run 1 succeeds -> round_N_xfamily.json + .meta.json on disk
#   run 2 on the same doc returns unparseable output -> adapter exits 2
#   ...but it used to leave run 1's artifacts in place, so the plateau gate certified the OLD
#   round. G3 cannot catch it: the doc bytes never changed.
#
# Also asserts the fail-closed contract: unparseable output writes the FAILED sentinel, and the
# sentinel can never satisfy the gate.
#
# Uses a stub `claude` on PATH -- no network, no cost.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="$HERE/../scripts/magi_xfamily_claude.sh"
GATE="$HERE/../scripts/magi_plateau_gate.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

DOC="$TMP/design.md"; printf 'a design document\n' > "$DOC"
MARKER_DIR="$TMP/.dual-magi"
STATE="$TMP/state"; mkdir -p "$STATE"
REAL_SID="11111111-2222-4333-8444-777777777777"
export HOME="$TMP/home"
mkdir -p "$HOME/.claude/projects/test"
cat > "$HOME/.claude/projects/test/$REAL_SID.jsonl" <<'JSONL'
{"message":{"model":"claude-fable-5","content":[{"type":"tool_use","name":"Grep","input":{"pattern":"foo"}}]}}
JSONL

# Stub claude: first invocation emits a valid envelope, second emits prose.
STUB="$TMP/bin"; mkdir -p "$STUB"
cat > "$STUB/claude" <<STUBEOF
#!/usr/bin/env bash
n=\$(cat "$TMP/n" 2>/dev/null || echo 0); echo \$((n+1)) > "$TMP/n"
if [ "\$n" = "0" ]; then
  python3 -c '
import json
print(json.dumps({"structured_output":{"reviewer":"CLAUDE-XFAMILY","round":2,"verdict":"GO",
 "schema_grounding_verdict":"PASS","verify_commands_executed":["rg -n foo"],"findings":[]},
 "session_id":"'"$REAL_SID"'","modelUsage":{"claude-fable-5":{}},"num_turns":4,
 "permission_denials":[],"result":"ok"}))'
else
  echo "I could not complete the review. Here are some thoughts instead."
fi
STUBEOF
chmod +x "$STUB/claude"
export PATH="$STUB:$PATH"

# --- run 1: succeeds ---
"$ADAPTER" "$DOC" 2 - "$STATE/round_2_xfamily" >/dev/null 2>&1
rc1=$?
[ $rc1 -eq 0 ] && [ -s "$STATE/round_2_xfamily.json" ] \
    && ok "run 1 succeeded and wrote findings" || bad "run 1 rc=$rc1 (expected 0 with findings)"

"$GATE" "$DOC" "$STATE/round_2_xfamily" >/dev/null 2>&1 \
    && ok "gate grants plateau on the valid round" || bad "gate refused a valid round"
rm -f "$MARKER_DIR"/PLATEAU.*

# --- run 2: same doc, unparseable output ---
"$ADAPTER" "$DOC" 2 - "$STATE/round_2_xfamily" >/dev/null 2>&1
rc2=$?
[ $rc2 -eq 2 ] && ok "run 2 fails closed (exit 2)" || bad "run 2 rc=$rc2 (expected 2)"

[ -s "$STATE/round_2_xfamily.FAILED.json" ] \
    && ok "FAILED sentinel written for unparseable output" \
    || bad "no FAILED sentinel: the fail-closed contract is broken"

[ -e "$STATE/round_2_xfamily.json" ] \
    && bad "stale success findings survived the failed re-run" \
    || ok "stale success findings removed by the failed re-run"

# THE payload assertion: the gate must not certify anything after a failed re-run.
if "$GATE" "$DOC" "$STATE/round_2_xfamily" >/dev/null 2>&1; then
    bad "gate GRANTED plateau after a failed re-run (stale round certified)"
else
    ok "gate denies plateau after a failed re-run"
fi

# The sentinel alone must never satisfy the gate.
ls "$MARKER_DIR"/PLATEAU.* >/dev/null 2>&1 && bad "a plateau marker exists after a failed round" \
                                      || ok "no plateau marker after a failed round"

echo "test_stale_round_failclosed: $pass passed, $fail failed"
exit $((fail > 0))
