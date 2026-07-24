#!/usr/bin/env bash
# Claude default-reviewer regression (deterministic argv fixture, parallel to test_grok_provider.sh).
#
# Pins the EXACT structural read-only rail the adapter must hand the Claude CLI:
#   --safe-mode --strict-mcp-config --tools Read,Grep,Glob  (surface restriction, NOT permission-mode)
#   --permission-mode dontAsk + allow Read/Grep/Glob + deny Agent/Task/Edit/Write/NotebookEdit/Bash
# and that a valid Claude round grants plateau while its provenance cannot masquerade as Grok.
#
# It stubs the `claude` CLI, so it never spends tokens. The live counterpart that measures the
# rail's actual effect (a Write returns "not enabled in this context") is test_inv6_readonly.sh.
set -uo pipefail
export MAGI_TEST_ALLOW_NEW_CAMPAIGN=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="$HERE/../scripts/magi_xfamily.sh"
GATE="$HERE/../scripts/magi_plateau_gate.sh"
GUARD="$HERE/../scripts/magi_campaign_guard.py"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

DOC="$TMP/design.md"; printf 'a grounded design\n' > "$DOC"
DOC_SHA="$(sha256sum "$DOC" | cut -d' ' -f1)"
DOC_ID="$(printf '%s' "$(realpath "$DOC")" | sha256sum | cut -c1-16)"
MARKER_DIR="$TMP/.dual-magi"
STATE="$TMP/state"; mkdir -p "$STATE" "$TMP/bin" "$TMP/home"
SOURCE="$STATE/round_1_source.json"
printf '{"reviewer":"SOURCE","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["fixture"],"source_artifacts":[],"dispositions":[],"findings":[]}\n' \
  "$DOC_ID" "$DOC_SHA" > "$SOURCE"
SOURCE_SHA="$(sha256sum "$SOURCE" | cut -d' ' -f1)"
PRIOR="$STATE/round_1_codex.json"
printf '{"reviewer":"SYNTHESIS","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["fixture"],"source_artifacts":[{"path":"%s","sha256":"%s"}],"dispositions":[],"findings":[]}\n' \
  "$DOC_ID" "$DOC_SHA" "$(basename "$SOURCE")" "$SOURCE_SHA" > "$PRIOR"
SID="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

cat > "$TMP/bin/claude" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$TMP/claude_args"
if [ -e "/proc/\$\$/fd/9" ]; then
  printf 'present\n' > "$TMP/claude_fd9"
else
  printf 'absent\n' > "$TMP/claude_fd9"
fi
cat > /dev/null  # drain the stdin prompt; argv never carries the doc
if [ "\${STUB_HANG:-}" = 1 ]; then
  : > "\${STUB_READY:?}"
  while :; do sleep 1; done
fi
if [ "\${STUB_FAIL:-}" = 1 ]; then
  exit 7
fi
PROJ="$TMP/home/.claude/projects/-tmp-fixture"
mkdir -p "\$PROJ"
# The model the transcript records is what the CLI "actually ran"; override to simulate a downgrade.
TMODEL="\${STUB_TRANSCRIPT_MODEL:-claude-fable-5}"
cat > "\$PROJ/$SID.jsonl" <<JSONL
{"message":{"model":"\$TMODEL","content":[{"type":"tool_use","name":"Read","input":{"file_path":"design.md"}}]}}
{"message":{"model":"\$TMODEL","content":[{"type":"tool_result","content":"a grounded design","is_error":false}]}}
JSONL
python3 - <<'PY'
import json
finding = {"reviewer":"CLAUDE-XFAMILY","round":2,"artifact_id":"$DOC_ID",
 "artifact_sha":"$DOC_SHA","verdict":"GO",
 "schema_grounding_verdict":"PASS","verify_commands_executed":["read_file design.md"],
 "source_artifacts":[],"dispositions":[],"findings":[]}
env = {"structured_output":finding,"result":json.dumps(finding),
 "session_id":"$SID","modelUsage":{"claude-fable-5":{"inputTokens":10}},
 "num_turns":2,"permission_denials":[]}
print(json.dumps(env))
PY
STUB
chmod +x "$TMP/bin/claude"

seed_campaign() {
    local claim_line claim_id
    claim_line="$(python3 "$GUARD" claim "$DOC" 1 fanout "$STATE")" || return 1
    claim_id="${claim_line##*CLAIM_ID=}"
    python3 "$GUARD" finish "$DOC" "$claim_id" success >/dev/null
}

seed_campaign || exit 1

PATH="$TMP/bin:$PATH" HOME="$TMP/home" "$ADAPTER" --reviewer claude \
    "$DOC" 2 "$PRIOR" "$STATE/round_8_xfamily" >/dev/null 2>&1
rc=$?
[ $rc -eq 0 ] && ok "Claude adapter completes" || bad "Claude adapter rc=$rc"
[ "$(cat "$TMP/claude_fd9" 2>/dev/null)" = absent ] \
    && ok "Claude provider does not inherit review-lock FD9" \
    || bad "Claude provider inherited review-lock FD9"
if find "$STATE" -maxdepth 1 -name '.round_8_xfamily.claim-*' | grep -q .; then
  bad "Claude adapter left claim-scoped staging files"
else
  ok "Claude claim-scoped staging is removed after promotion"
fi

# The default reviewer route must be Claude even with no --reviewer flag.
python3 "$GUARD" new-campaign "$DOC" --operator test --reason 'independent default-route fixture' >/dev/null || exit 1
seed_campaign || exit 1
PATH="$TMP/bin:$PATH" HOME="$TMP/home" "$ADAPTER" \
    "$DOC" 2 "$PRIOR" "$STATE/round_8_default" >/dev/null 2>&1
[ $? -eq 0 ] && [ "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["reviewer_family"])' "$STATE/round_8_default.meta.json" 2>/dev/null)" = claude ] \
    && ok "default (no --reviewer) routes to Claude" || bad "default route is not Claude"

# The exact structural read-only rail — every flag the round-12 fix depends on.
ARGS="$TMP/claude_args"
grep -qx -- '--safe-mode' "$ARGS" \
    && grep -qx -- '--strict-mcp-config' "$ARGS" \
    && grep -qx -- '--tools' "$ARGS" \
    && grep -qx -- 'Read,Grep,Glob' "$ARGS" \
    && grep -qx -- '--permission-mode' "$ARGS" \
    && grep -qx -- 'dontAsk' "$ARGS" \
    && grep -qx -- '--allowedTools' "$ARGS" \
    && grep -qx -- '--disallowedTools' "$ARGS" \
    && grep -qx -- 'Agent' "$ARGS" && grep -qx -- 'Task' "$ARGS" \
    && grep -qx -- 'Edit' "$ARGS" && grep -qx -- 'Write' "$ARGS" \
    && grep -qx -- 'NotebookEdit' "$ARGS" && grep -qx -- 'Bash' "$ARGS" \
    && ! grep -qx -- 'acceptEdits' "$ARGS" \
    && ! grep -qx -- '--dangerously-skip-permissions' "$ARGS" \
    && ! grep -qx -- '--allow-dangerously-skip-permissions' "$ARGS" \
    && ok "Claude adapter pins structural surface restriction + write/agent/shell deny" \
    || bad "Claude structural read-only rail missing or weakened"

python3 - "$STATE/round_8_xfamily.meta.json" <<'PY' >/dev/null 2>&1
import json, sys
m=json.load(open(sys.argv[1]))
assert m["reviewer_family"] == "claude", m["reviewer_family"]
assert m["model_id"] == "claude-fable-5", m["model_id"]          # derived from the transcript
assert m["requested_model"] == "claude-fable-5", m.get("requested_model")  # from --model / env
assert m["model_usage_keys"] == ["claude-fable-5"], m["model_usage_keys"]
assert m["session_id"] == "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
assert "/.claude/projects/" in m["transcript_path"], m["transcript_path"]
PY
[ $? -eq 0 ] && ok "Claude provenance recorded (model_id + requested_model)" || bad "Claude provenance invalid"

# End-to-end silent-downgrade: request opus, but the CLI (stub) runs fable. The adapter records
# requested_model=opus while the transcript shows fable; the gate must deny — no hand-edited meta.
python3 "$GUARD" new-campaign "$DOC" --operator test --reason 'independent downgrade fixture' >/dev/null || exit 1
seed_campaign || exit 1
DG="$STATE/round_8_downgrade"
PATH="$TMP/bin:$PATH" HOME="$TMP/home" MAGI_XFAMILY_CLAUDE_MODEL=claude-opus-4-8 \
    STUB_TRANSCRIPT_MODEL=claude-fable-5 "$ADAPTER" --reviewer claude \
    "$DOC" 2 "$PRIOR" "$DG" >/dev/null 2>&1
dg_req="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("requested_model"))' "$DG.meta.json" 2>/dev/null)"
HOME="$TMP/home" "$GATE" "$DOC" "$DG" --orchestrator-family codex --reviewer-family claude >/dev/null 2>&1
gate_rc=$?
if [ "$dg_req" = "claude-opus-4-8" ] && [ $gate_rc -ne 0 ]; then
  ok "silent same-family downgrade (requested opus, ran fable) denied end-to-end"
else
  bad "silent downgrade not caught (requested=$dg_req, gate did not deny)"
fi

HOME="$TMP/home" "$GATE" "$DOC" "$STATE/round_8_xfamily" \
    --orchestrator-family codex --reviewer-family claude >/dev/null 2>&1
[ $? -eq 0 ] && ok "Claude cross-family round can grant plateau" \
              || bad "valid Claude round was denied"

rm -f "$MARKER_DIR"/PLATEAU.*
HOME="$TMP/home" "$GATE" "$DOC" "$STATE/round_8_xfamily" \
    --orchestrator-family codex --reviewer-family grok >/dev/null 2>&1
[ $? -ne 0 ] && ok "Claude artifact cannot masquerade as Grok" \
              || bad "Claude artifact passed Grok gate"

python3 - "$STATE/round_8_xfamily.meta.json" <<'PY'
import json, sys
p=sys.argv[1]; m=json.load(open(p)); m["model_id"]="gpt-5.5"; m["model_usage_keys"]=["gpt-5.5"]
json.dump(m,open(p,"w"),indent=2)
PY
HOME="$TMP/home" "$GATE" "$DOC" "$STATE/round_8_xfamily" \
    --orchestrator-family codex --reviewer-family claude >/dev/null 2>&1
[ $? -ne 0 ] && ok "same-family model id rejected under Claude label" \
              || bad "same-family model passed Claude gate"

# A cross-family (still-Claude) model the transcript never ran must be rejected by G6's model
# provenance check — the preferred path's anti-substitution rail, symmetric with Grok.
python3 - "$STATE/round_8_xfamily.meta.json" <<'PY'
import json, sys
p=sys.argv[1]; m=json.load(open(p)); m["model_id"]="claude-opus-4-8"; m["model_usage_keys"]=["claude-opus-4-8"]
json.dump(m,open(p,"w"),indent=2)
PY
HOME="$TMP/home" "$GATE" "$DOC" "$STATE/round_8_xfamily" \
    --orchestrator-family codex --reviewer-family claude >/dev/null 2>&1
[ $? -ne 0 ] && ok "Claude model absent from transcript rejected (G6 model provenance)" \
              || bad "meta model_id disagreeing with transcript passed Claude gate"

python3 - "$STATE/round_8_xfamily.meta.json" <<'PY'
import json, sys
p=sys.argv[1]; m=json.load(open(p)); m["model_id"]="claude-fable-5"; m["model_usage_keys"]=["claude-fable-5"]
json.dump(m,open(p,"w"),indent=2)
PY
printf '{"message":{"content":[{"type":"text","text":"late mutation"}]}}\n' \
    >> "$TMP/home/.claude/projects/-tmp-fixture/$SID.jsonl"
HOME="$TMP/home" "$GATE" "$DOC" "$STATE/round_8_xfamily" \
    --orchestrator-family codex --reviewer-family claude >/dev/null 2>&1
[ $? -ne 0 ] && ok "post-adapter Claude transcript mutation rejected" \
              || bad "mutated Claude transcript passed gate"

# A charged retry owns its requested prefix. If its provider then fails, canonical output from an
# older attempt must be gone rather than masquerading as the failed claim's result.
python3 "$GUARD" new-campaign "$DOC" --operator test --reason 'stale-prefix cleanup fixture' >/dev/null || exit 1
seed_campaign || exit 1
STALE="$STATE/round_8_stale"
printf 'stale findings\n' > "$STALE.json"
printf 'stale metadata\n' > "$STALE.meta.json"
PATH="$TMP/bin:$PATH" HOME="$TMP/home" STUB_FAIL=1 "$ADAPTER" --reviewer claude \
    "$DOC" 2 "$PRIOR" "$STALE" >/dev/null 2>&1
stale_rc=$?
if [ $stale_rc -eq 2 ] && [ ! -e "$STALE.json" ] && [ ! -e "$STALE.meta.json" ] \
    && [ -e "$STALE.FAILED.json" ]; then
  ok "charged provider failure clears stale canonical output"
else
  bad "failed claim left stale canonical output (rc=$stale_rc)"
fi

# Requirement-revision cancellation owns the live adapter tree. Even when cancellation races a
# provider invocation at the requested prefix, no findings/meta may reach canonical paths.
CANCEL_DOC="$TMP/cancel-design.md"; printf 'a revised design\n' > "$CANCEL_DOC"
CANCEL_SHA="$(sha256sum "$CANCEL_DOC" | cut -d' ' -f1)"
CANCEL_ID="$(printf '%s' "$(realpath "$CANCEL_DOC")" | sha256sum | cut -c1-16)"
CANCEL_STATE="$TMP/cancel-state"; mkdir -p "$CANCEL_STATE"
CANCEL_SOURCE="$CANCEL_STATE/round_1_source.json"
printf '{"reviewer":"SOURCE","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["fixture"],"source_artifacts":[],"dispositions":[],"findings":[]}\n' \
    "$CANCEL_ID" "$CANCEL_SHA" > "$CANCEL_SOURCE"
CANCEL_SOURCE_SHA="$(sha256sum "$CANCEL_SOURCE" | cut -d' ' -f1)"
CANCEL_PRIOR="$CANCEL_STATE/round_1_codex.json"
printf '{"reviewer":"SYNTHESIS","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["fixture"],"source_artifacts":[{"path":"%s","sha256":"%s"}],"dispositions":[],"findings":[]}\n' \
    "$CANCEL_ID" "$CANCEL_SHA" "$(basename "$CANCEL_SOURCE")" "$CANCEL_SOURCE_SHA" > "$CANCEL_PRIOR"
cancel_seed="$(python3 "$GUARD" claim "$CANCEL_DOC" 1 fanout "$CANCEL_STATE")" || exit 1
cancel_seed_id="${cancel_seed##*CLAIM_ID=}"
python3 "$GUARD" finish "$CANCEL_DOC" "$cancel_seed_id" success >/dev/null || exit 1
CANCELLED="$CANCEL_STATE/round_8_cancelled"
READY="$TMP/cancel-provider-ready"
PATH="$TMP/bin:$PATH" HOME="$TMP/home" STUB_HANG=1 STUB_READY="$READY" \
    "$ADAPTER" --reviewer claude "$CANCEL_DOC" 2 "$CANCEL_PRIOR" "$CANCELLED" \
    >"$TMP/cancel-adapter.log" 2>&1 &
cancel_adapter_pid=$!
for _ in $(seq 1 100); do
  [ -e "$READY" ] && break
  sleep 0.05
done
if [ ! -e "$READY" ]; then
  bad "cancellation fixture provider did not start: $(tr '\n' ' ' < "$TMP/cancel-adapter.log")"
  kill -TERM "$cancel_adapter_pid" 2>/dev/null
  wait "$cancel_adapter_pid" 2>/dev/null
else
  python3 "$GUARD" cancel-revision "$CANCEL_DOC" \
      --expected-artifact-sha "$CANCEL_SHA" --reason 'test requirement revision' \
      --term-timeout-s 1 --kill-timeout-s 1 >/dev/null 2>&1
  cancel_rc=$?
  wait "$cancel_adapter_pid" 2>/dev/null
  cancel_status="$(python3 - "$MARKER_DIR/CAMPAIGN.$CANCEL_ID.json" <<'PY'
import json, sys
ledger = json.load(open(sys.argv[1]))
print(ledger["campaigns"][-1]["launches"][-1]["status"])
PY
)"
  if [ $cancel_rc -eq 0 ] \
      && [ "$cancel_status" = superseded-by-requirement-revision ] \
      && [ ! -e "$CANCELLED.json" ] && [ ! -e "$CANCELLED.meta.json" ] \
      && ! find "$CANCEL_STATE" -maxdepth 1 -name '.round_8_cancelled.claim-*' | grep -q .; then
    ok "superseded xfamily claim cannot promote canonical output"
  else
    bad "superseded claim promotion guard failed (cancel_rc=$cancel_rc status=$cancel_status)"
  fi
fi

echo "test_claude_provider: $pass passed, $fail failed"
exit $((fail > 0))
