#!/usr/bin/env bash
# test_plateau_gate.sh — INV-2: G1..G7 each independently block a plateau.
# Design §4.3. Every assert gets its own negative case, plus one positive case that must pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/../scripts/magi_plateau_gate.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
DOC="$TMP/design.md"; printf 'a design document\n' > "$DOC"
SHA="$(sha256sum "$DOC" | cut -d' ' -f1)"
pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

# A real session_id so G6 can pass in the positive case; fabricated ones must fail.
REAL_SID="$(basename "$(ls -t ~/.claude/projects/*/*.jsonl 2>/dev/null | head -1)" .jsonl 2>/dev/null || echo "")"

mkmeta() { # mkmeta <prefix> <model_id> <artifact_sha> <num_turns> <session_id>
  python3 - "$1" "$2" "$3" "$4" "$5" <<'PY'
import hashlib, json, sys
prefix, model, sha, turns, sid = sys.argv[1:6]
meta = {"session_id": sid, "model_id": model, "model_usage_keys": [model],
        "num_turns": int(turns), "artifact_sha": sha, "permission_denials": [],
        "output_sha": hashlib.sha256(open(prefix + ".json","rb").read()).hexdigest()}
json.dump(meta, open(prefix + ".meta.json","w"), indent=2)
PY
}
mkfind() { # mkfind <prefix> <verdict> <ncmds> [grounding] [worst-severity]
  python3 - "$1" "$2" "$3" "${4:-PASS}" "${5:-}" <<'PY'
import json, sys
prefix, verdict, n, grounding, sev = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
findings = []
if sev:
    findings = [{"finding_id":"x-1","severity":sev,"title":"a blocking defect","location":"§1",
                 "rationale":"r","required_fix":"f","confidence":"high","dup_flag":"new",
                 "missed_angle":"m"}]
json.dump({"reviewer":"CLAUDE-XFAMILY","round":2,"verdict":verdict,
           "schema_grounding_verdict":grounding,
           "verify_commands_executed":[f"rg -n x {i}" for i in range(n)],
           "findings":findings}, open(prefix + ".json","w"), indent=2)
PY
}
denied() { # denied <case> <prefix>
  if "$GATE" "$DOC" "$2" --orchestrator-family codex >/dev/null 2>&1; then
    bad "$1 -> plateau GRANTED (should be denied)"
  else ok "$1 -> denied"; fi
}

# G1: missing cross-family round
denied "G1 missing findings" "$TMP/g1"

# G1: UNPARSEABLE sentinel must not satisfy an existence check
P="$TMP/g1b"; mkfind "$P" "UNPARSEABLE" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G1 UNPARSEABLE verdict" "$P"

# G2: same-family reviewer masquerading as cross-family
P="$TMP/g2"; mkfind "$P" "GO" 3; mkmeta "$P" "gpt-5.5" "$SHA" 4 "$REAL_SID"
denied "G2 same-family model_id" "$P"

# G3: stale round -- reviewed a different revision of the doc
P="$TMP/g3"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "deadbeef" 4 "$REAL_SID"
denied "G3 artifact_sha mismatch (stale round)" "$P"

# G4: findings swapped after the adapter wrote them
P="$TMP/g4"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
printf '{"verdict":"GO","verify_commands_executed":[],"findings":[]}' > "$P.json"
denied "G4 output_sha mismatch (findings swapped)" "$P"

# G5: zero-turn round claiming to have executed commands
P="$TMP/g5"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 1 "$REAL_SID"
denied "G5 num_turns=1 with commands reported" "$P"

# G6: session_id that resolves to no transcript
P="$TMP/g6"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "00000000-dead-beef-0000-000000000000"
denied "G6 session_id with no transcript" "$P"

# G7: REJECT is never a plateau
P="$TMP/g7"; mkfind "$P" "REJECT" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G7 REJECT verdict" "$P"

# G7: neither is REVISE (the bug-hunt found the gate granting plateau on REVISE)
P="$TMP/g7b"; mkfind "$P" "REVISE" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G7 REVISE verdict" "$P"

# G8: a GO-WITH-REVISE carrying an unresolved CRITICAL is not a plateau
P="$TMP/g8"; mkfind "$P" "GO-WITH-REVISE" 3 "PASS" "CRITICAL"; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G8 GO-WITH-REVISE with a CRITICAL finding" "$P"

# G9: a reviewer that self-reports ungrounded cannot plateau
P="$TMP/g9"; mkfind "$P" "GO" 0 "FAIL"; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G9 schema_grounding_verdict=FAIL" "$P"

# G2: a managed-deployment model id (us.anthropic.claude-…) must still count as cross-family
if [ -n "$REAL_SID" ]; then
  P="$TMP/g2ok"; mkfind "$P" "GO" 3; mkmeta "$P" "us.anthropic.claude-fable-5" "$SHA" 4 "$REAL_SID"
  if "$GATE" "$DOC" "$P" --orchestrator-family codex >/dev/null 2>&1; then
      ok "G2 accepts a managed-deployment claude model id"
  else
      bad "G2 wrongly refused us.anthropic.claude-fable-5"
  fi
fi

# usage: a dangling option value must be a usage error (64), not an unbound-variable exit 1
"$GATE" "$DOC" "$TMP/whatever" --orchestrator-family >/dev/null 2>&1
[ $? -eq 64 ] && ok "dangling --orchestrator-family exits 64" || bad "dangling option did not exit 64"

# POSITIVE: everything valid -> marker written
if [ -n "$REAL_SID" ]; then
  P="$TMP/good"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
  if "$GATE" "$DOC" "$P" --orchestrator-family codex >/dev/null 2>&1; then
    ls "$TMP"/PLATEAU.* >/dev/null 2>&1 && ok "valid round -> plateau granted + marker written" \
                                        || bad "granted but no marker file"
  else
    bad "valid round was denied (gate too strict)"
  fi
else
  echo "  skip - positive case (no local transcript to reference)"
fi

echo "test_plateau_gate: $pass passed, $fail failed"
exit $((fail > 0))
