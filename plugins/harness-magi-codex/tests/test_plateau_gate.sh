#!/usr/bin/env bash
# test_plateau_gate.sh — INV-2: G1..G9 independently block a plateau.
# Design §4.3. Every assert gets its own negative case, plus one positive case that must pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$HERE/../scripts/magi_plateau_gate.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
DOC="$TMP/design.md"; printf 'a design document\n' > "$DOC"
MARKER_DIR="$TMP/.dual-magi"
SHA="$(sha256sum "$DOC" | cut -d' ' -f1)"
pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

# A deterministic Claude transcript with one tool use. Depending on whichever real session was
# most recently active made this test fail when that session happened to contain no tools.
REAL_SID="11111111-2222-4333-8444-666666666666"
NO_TOOL_SID="11111111-2222-4333-8444-777777777777"
export HOME="$TMP/home"
mkdir -p "$HOME/.claude/projects/test"
# message.model must match the meta.model_id the positive cases assert (G6 model provenance).
cat > "$HOME/.claude/projects/test/$REAL_SID.jsonl" <<'JSONL'
{"message":{"model":"claude-fable-5","content":[{"type":"tool_use","name":"Grep","input":{"pattern":"x"}}]}}
JSONL
cat > "$HOME/.claude/projects/test/$NO_TOOL_SID.jsonl" <<'JSONL'
{"message":{"model":"claude-fable-5","content":[{"type":"text","text":"claimed verification without a tool call"}]}}
JSONL
# A transcript whose served model is a TRUNCATION of a plausible requested id (downgrade probe).
TRUNC_SID="11111111-2222-4333-8444-888888888888"
cat > "$HOME/.claude/projects/test/$TRUNC_SID.jsonl" <<'JSONL'
{"message":{"model":"claude-opus-4","content":[{"type":"tool_use","name":"Grep","input":{"pattern":"x"}}]}}
JSONL
# A transcript whose served model is a SUPERSTRING variant of the requested id (…-lite). See the
# residual-gap note in design §4.3 G6: a cheaper suffixed variant is string-indistinguishable from a
# dated/patch snapshot, so substring comparison accepts it. This test pins that DOCUMENTED behavior.
SUPER_SID="11111111-2222-4333-8444-999999999999"
cat > "$HOME/.claude/projects/test/$SUPER_SID.jsonl" <<'JSONL'
{"message":{"model":"claude-opus-4-8-lite","content":[{"type":"tool_use","name":"Grep","input":{"pattern":"x"}}]}}
JSONL

mkmeta() { # mkmeta <prefix> <model_id> <artifact_sha> <num_turns> <session_id> [requested_model]
  python3 - "$1" "$2" "$3" "$4" "$5" "${6:-$2}" <<'PY'
import hashlib, json, sys
prefix, model, sha, turns, sid, requested = sys.argv[1:7]
meta = {"session_id": sid, "model_id": model, "requested_model": requested,
        "model_usage_keys": [model],
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

# G5: one-turn round claiming to have executed commands
P="$TMP/g5"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 1 "$REAL_SID"
denied "G5 num_turns=1 with commands reported" "$P"

# G6: session_id that resolves to no transcript
P="$TMP/g6"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "00000000-dead-beef-0000-000000000000"
denied "G6 session_id with no transcript" "$P"

# G6: meta claims a model the transcript never ran (mislabeled meta, e.g. opus vs fable)
P="$TMP/g6b"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-opus-4-8" "$SHA" 4 "$REAL_SID"
denied "G6 meta model_id absent from Claude transcript" "$P"

# G6: honest model_id but the REQUESTED model was silently downgraded (ran fable, asked opus).
# model_id matches the transcript, so only the requested-vs-run check can catch this.
P="$TMP/g6c"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID" "claude-opus-4-8"
denied "G6 requested model absent from transcript (silent downgrade)" "$P"

# G6: a meta with no requested_model at all cannot certify (fresh adapters always record it)
P="$TMP/g6d"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
python3 -c 'import json,sys; p=sys.argv[1]; m=json.load(open(p)); m.pop("requested_model",None); json.dump(m,open(p,"w"))' "$P.meta.json"
denied "G6 missing requested_model" "$P"

# G6: served id is a TRUNCATION of the requested id (claude-opus-4 for requested claude-opus-4-8).
# This is the ONE fixture that distinguishes the directional served_satisfies from the old
# bidirectional comparator: it is GRANTED under bidirectional (bug) and DENIED under directional
# (fixed). Reverting served_satisfies -> label_consistent fails exactly here. (r15-xfamily-1)
P="$TMP/g6e"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-opus-4" "$SHA" 4 "$TRUNC_SID" "claude-opus-4-8"
denied "G6 truncated served model (downgrade via substring) rejected" "$P"

# G7: REJECT is never a plateau
P="$TMP/g7"; mkfind "$P" "REJECT" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G7 REJECT verdict" "$P"

# G7: neither is REVISE (the bug-hunt found the gate granting plateau on REVISE)
P="$TMP/g7b"; mkfind "$P" "REVISE" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G7 REVISE verdict" "$P"

# G8: a GO-WITH-REVISE carrying an unresolved CRITICAL is not a plateau
P="$TMP/g8"; mkfind "$P" "GO-WITH-REVISE" 3 "PASS" "CRITICAL"; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G8 GO-WITH-REVISE with a CRITICAL finding" "$P"

# G8: HIGH is also blocking; invariant drift must not pass through severity calibration.
P="$TMP/g8b"; mkfind "$P" "GO-WITH-REVISE" 3 "PASS" "HIGH"; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G8 GO-WITH-REVISE with a HIGH finding" "$P"

# G9: a reviewer that self-reports ungrounded cannot plateau
P="$TMP/g9"; mkfind "$P" "GO" 0 "FAIL"; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G9 schema_grounding_verdict=FAIL" "$P"

# G9: PASS with an empty command list is the ungrounded state -- constrained decoding will
# happily emit it. (Code review found this bypassing every assert.)
P="$TMP/g9b"; mkfind "$P" "GO" 0 "PASS"; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
denied "G9 grounding=PASS with zero commands" "$P"

# G9: a non-empty self-report cannot substitute for actual provider transcript tool use.
P="$TMP/g9c"; mkfind "$P" "GO" 3 "PASS"; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$NO_TOOL_SID"
denied "G9 commands claimed with zero transcript tool use" "$P"

# A denial must REVOKE a marker previously granted for the same doc revision.
if [ -n "$REAL_SID" ]; then
  P="$TMP/revoke"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
  "$GATE" "$DOC" "$P" >/dev/null 2>&1
  if ls "$MARKER_DIR"/PLATEAU.* >/dev/null 2>&1; then
      # Re-gate a corrupt round from another campaign directory. Marker scope is doc-canonical.
      Q="$TMP/other-campaign/revoke"; mkdir -p "$(dirname "$Q")"
      mkfind "$Q" "REJECT" 3; mkmeta "$Q" "claude-fable-5" "$SHA" 4 "$REAL_SID"
      "$GATE" "$DOC" "$Q" >/dev/null 2>&1
      ls "$MARKER_DIR"/PLATEAU.* >/dev/null 2>&1 \
          && bad "denial left a previously granted marker in place" \
          || ok "cross-campaign denial revokes the doc-canonical marker"
  else
      bad "setup: valid round did not produce a marker"
  fi
  rm -f "$MARKER_DIR"/PLATEAU.*
fi

# A malformed rerun must revoke an existing marker even when later meta fields would have raised.
if [ -n "$REAL_SID" ]; then
  P="$TMP/revoke-exception-good"; mkfind "$P" "GO" 3
  mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
  "$GATE" "$DOC" "$P" >/dev/null 2>&1
  Q="$TMP/revoke-exception-bad"; mkfind "$Q" "UNPARSEABLE" 3
  mkmeta "$Q" "claude-fable-5" "$SHA" 4 "$REAL_SID"
  python3 -c 'import json,sys; p=sys.argv[1]; m=json.load(open(p)); m["num_turns"]="bad"; json.dump(m,open(p,"w"))' "$Q.meta.json"
  "$GATE" "$DOC" "$Q" >/dev/null 2>&1
  ls "$MARKER_DIR"/PLATEAU.* >/dev/null 2>&1 \
      && bad "malformed verifier exception path left a plateau marker" \
      || ok "malformed verifier exception path revokes plateau marker"
fi

# G2: a managed-deployment model id (us.anthropic.claude-…) must still count as cross-family
if [ -n "$REAL_SID" ]; then
  # model_id carries a managed deployment prefix; requested_model is the bare id the operator passed.
  P="$TMP/g2ok"; mkfind "$P" "GO" 3; mkmeta "$P" "us.anthropic.claude-fable-5" "$SHA" 4 "$REAL_SID" "claude-fable-5"
  if "$GATE" "$DOC" "$P" --orchestrator-family codex >/dev/null 2>&1; then
      ok "G2 accepts a managed-deployment claude model id"
  else
      bad "G2 wrongly refused us.anthropic.claude-fable-5"
  fi
fi

# G6 residual gap (documented, design §4.3): a superstring VARIANT of the requested id is accepted,
# because substring comparison cannot separate a cheaper suffixed variant (…-lite) from a dated
# snapshot (…-20260101). This pins the known behavior so a future tightening updates the doc too.
if [ -n "$SUPER_SID" ]; then
  P="$TMP/g6f"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-opus-4-8-lite" "$SHA" 4 "$SUPER_SID" "claude-opus-4-8"
  if "$GATE" "$DOC" "$P" --orchestrator-family codex >/dev/null 2>&1; then
    ok "G6 superstring variant accepted (documented residual T1 gap)"
  else
    bad "superstring variant denied — comparator changed; update design §4.3 residual-gap note"
  fi
  rm -f "$MARKER_DIR"/PLATEAU.*
fi

# usage: a dangling option value must be a usage error (64), not an unbound-variable exit 1
"$GATE" "$DOC" "$TMP/whatever" --orchestrator-family >/dev/null 2>&1
[ $? -eq 64 ] && ok "dangling --orchestrator-family exits 64" || bad "dangling option did not exit 64"

# POSITIVE: everything valid -> marker written
if [ -n "$REAL_SID" ]; then
  P="$TMP/good"; mkfind "$P" "GO" 3; mkmeta "$P" "claude-fable-5" "$SHA" 4 "$REAL_SID"
  if "$GATE" "$DOC" "$P" --orchestrator-family codex >/dev/null 2>&1; then
    marker_name="$(find "$MARKER_DIR" -maxdepth 1 -type f -name 'PLATEAU.*' -printf '%f\n' -quit)"
    if [[ "$marker_name" =~ ^PLATEAU\.[0-9a-f]{16}\.[0-9a-f]{16}$ ]]; then
      ok "valid round -> canonical doc-id + artifact-sha marker written"
    else
      bad "granted but marker name is invalid: $marker_name"
    fi
  else
    bad "valid round was denied (gate too strict)"
  fi
else
  echo "  skip - positive case (no local transcript to reference)"
fi

echo "test_plateau_gate: $pass passed, $fail failed"
exit $((fail > 0))
