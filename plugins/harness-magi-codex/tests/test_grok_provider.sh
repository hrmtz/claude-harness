#!/usr/bin/env bash
# Provider fallback regression: Grok adapter provenance is accepted only as Grok.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="$HERE/../scripts/magi_xfamily.sh"
GATE="$HERE/../scripts/magi_plateau_gate.sh"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

DOC="$TMP/design.md"; printf 'a grounded design\n' > "$DOC"
MARKER_DIR="$TMP/.dual-magi"
STATE="$TMP/state"; mkdir -p "$STATE" "$TMP/bin" "$TMP/home"
SID="11111111-2222-4333-8444-555555555555"

cat > "$TMP/bin/grok" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$TMP/grok_args"
mkdir -p "$TMP/home/.grok/sessions/workspace/$SID"
cat > "$TMP/home/.grok/sessions/workspace/$SID/chat_history.jsonl" <<'JSONL'
{"type":"assistant","content":"reviewing","model_id":"grok-4.5","tool_calls":[{"id":"x","name":"read_file","arguments":"{}"}]}
{"type":"tool_result","content":"verified"}
{"type":"assistant","content":"done","model_id":"grok-4.5","tool_calls":[]}
JSONL
python3 - <<'PY'
import json
finding = {"reviewer":"GROK-XFAMILY","round":2,"verdict":"GO",
 "schema_grounding_verdict":"PASS","verify_commands_executed":["rg -n invariant design.md"],
 "findings":[]}
print(json.dumps({"structuredOutput":finding,"text":json.dumps(finding),
 "stopReason":"EndTurn","sessionId":"$SID"}))
PY
STUB
chmod +x "$TMP/bin/grok"

PATH="$TMP/bin:$PATH" HOME="$TMP/home" "$ADAPTER" --reviewer grok \
    "$DOC" 2 - "$STATE/round_2_xfamily" >/dev/null 2>&1
rc=$?
[ $rc -eq 0 ] && ok "Grok adapter completes" || bad "Grok adapter rc=$rc"

grep -qx -- '--sandbox' "$TMP/grok_args" && grep -qx -- 'read-only' "$TMP/grok_args" \
    && grep -qx -- '--tools' "$TMP/grok_args" \
    && grep -qx -- 'read_file,grep,list_dir' "$TMP/grok_args" \
    && grep -qx -- '--disallowed-tools' "$TMP/grok_args" \
    && grep -qx -- 'search_tool,use_tool,Agent' "$TMP/grok_args" \
    && grep -qx -- '--deny' "$TMP/grok_args" \
    && grep -qx -- 'MCPTool' "$TMP/grok_args" \
    && grep -qx -- '--no-subagents' "$TMP/grok_args" \
    && ! grep -qx -- '--always-approve' "$TMP/grok_args" \
    && ok "Grok adapter pins built-in/MCP deny rails and read-only sandbox" \
    || bad "Grok read-only provider rails missing"

python3 - "$STATE/round_2_xfamily.meta.json" <<'PY' >/dev/null 2>&1
import json, sys
m=json.load(open(sys.argv[1]))
assert m["reviewer_family"] == "grok"
assert m["model_id"] == "grok-4.5"
assert m["session_id"] == "11111111-2222-4333-8444-555555555555"
assert "/.grok/sessions/" in m["transcript_path"]
PY
[ $? -eq 0 ] && ok "Grok provenance recorded" || bad "Grok provenance invalid"

HOME="$TMP/home" "$GATE" "$DOC" "$STATE/round_2_xfamily" \
    --orchestrator-family codex --reviewer-family grok >/dev/null 2>&1
[ $? -eq 0 ] && ok "Grok cross-family round can grant plateau" \
              || bad "valid Grok round was denied"

rm -f "$MARKER_DIR"/PLATEAU.*
HOME="$TMP/home" "$GATE" "$DOC" "$STATE/round_2_xfamily" \
    --orchestrator-family codex --reviewer-family claude >/dev/null 2>&1
[ $? -ne 0 ] && ok "Grok artifact cannot masquerade as Claude" \
              || bad "Grok artifact passed Claude gate"

python3 - "$STATE/round_2_xfamily.meta.json" <<'PY'
import json, sys
p=sys.argv[1]; m=json.load(open(p)); m["model_id"]="gpt-5.5"; m["model_usage_keys"]=["gpt-5.5"]
json.dump(m,open(p,"w"),indent=2)
PY
HOME="$TMP/home" "$GATE" "$DOC" "$STATE/round_2_xfamily" \
    --orchestrator-family codex --reviewer-family grok >/dev/null 2>&1
[ $? -ne 0 ] && ok "same-family model id rejected under Grok label" \
              || bad "same-family model passed Grok gate"

python3 - "$STATE/round_2_xfamily.meta.json" <<'PY'
import json, sys
p=sys.argv[1]; m=json.load(open(p)); m["model_id"]="grok-4.5"; m["model_usage_keys"]=["grok-4.5"]
json.dump(m,open(p,"w"),indent=2)
PY
printf '{"type":"assistant","content":"late mutation","model_id":"grok-4.5","tool_calls":[]}\n' \
    >> "$TMP/home/.grok/sessions/workspace/$SID/chat_history.jsonl"
HOME="$TMP/home" "$GATE" "$DOC" "$STATE/round_2_xfamily" \
    --orchestrator-family codex --reviewer-family grok >/dev/null 2>&1
[ $? -ne 0 ] && ok "post-adapter Grok transcript mutation rejected" \
              || bad "mutated Grok transcript passed gate"

# Grok symmetric to claude g6e: requested grok-4.5 but the transcript served the truncated grok-4.
# The directional served_satisfies must deny this downgrade on the fallback path too.
SID2="22222222-3333-4444-8555-666666666666"
TDIR="$TMP/home/.grok/sessions/workspace/$SID2"; mkdir -p "$TDIR"
cat > "$TDIR/chat_history.jsonl" <<'JSONL'
{"type":"assistant","content":"reviewing","model_id":"grok-4","tool_calls":[{"id":"x","name":"read_file","arguments":"{}"}]}
{"type":"assistant","content":"done","model_id":"grok-4","tool_calls":[]}
JSONL
python3 - "$DOC" "$STATE/trunc_3_xfamily" "$SID2" "$TDIR/chat_history.jsonl" <<'PY'
import hashlib, json, sys
doc, prefix, sid, tpath = sys.argv[1:5]
json.dump({"reviewer":"GROK","round":3,"verdict":"GO","schema_grounding_verdict":"PASS",
           "verify_commands_executed":["rg -n x"],"findings":[]}, open(prefix+".json","w"), indent=2)
meta={"reviewer_family":"grok","session_id":sid,"model_id":"grok-4","requested_model":"grok-4.5",
      "model_usage_keys":["grok-4"],"num_turns":2,"permission_denials":[],
      "artifact_sha":hashlib.sha256(open(doc,"rb").read()).hexdigest(),"transcript_path":tpath,
      "transcript_sha":hashlib.sha256(open(tpath,"rb").read()).hexdigest(),
      "output_sha":hashlib.sha256(open(prefix+".json","rb").read()).hexdigest()}
json.dump(meta, open(prefix+".meta.json","w"), indent=2)
PY
HOME="$TMP/home" "$GATE" "$DOC" "$STATE/trunc_3_xfamily" \
    --orchestrator-family codex --reviewer-family grok >/dev/null 2>&1
[ $? -ne 0 ] && ok "Grok truncated served model (grok-4 for requested grok-4.5) rejected" \
              || bad "Grok truncation downgrade passed the gate"

echo "test_grok_provider: $pass passed, $fail failed"
exit $((fail > 0))
