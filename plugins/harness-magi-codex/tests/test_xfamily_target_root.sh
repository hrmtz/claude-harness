#!/usr/bin/env bash
# gh #151 regression: the CROSS-FAMILY arms (Claude, Grok) and the Codex pre-flight must ground
# their reviewer in the git top-level that OWNS the document -- not the caller's cwd (Claude
# passed no cwd flag at all, Grok passed --cwd "$PWD") and not merely the document's own
# directory (pre-flight passed -C "$(dirname "$BRIEF")").
#
# Sibling of test_fanout_target_root.sh, which pins the same contract for the fan-out arm (gh
# #57/#139). Cross-family is the mandatory independent gate, so a reviewer grounded in the wrong
# tree is worse there: its verify_commands_executed still succeed, against the wrong repository.
#
# Every reviewer here is a stub on PATH; no model launch is ever spent.
set -uo pipefail
export MAGI_TEST_ALLOW_NEW_CAMPAIGN=1
unset TMUX TMUX_PANE

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="$HERE/../scripts/magi_xfamily.sh"
PREFLIGHT="$HERE/../scripts/magi_preflight_codex.sh"
GUARD="$HERE/../scripts/magi_campaign_guard.py"
DEJA="$HERE/../scripts/magi_deja_context.py"
PROTOCOL="$HERE/../scripts/magi_protocol.py"
TMP="$(realpath "$(mktemp -d)")"; trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { echo "  ok   - $1"; pass=$((pass+1)); }
bad() { echo "  FAIL - $1"; fail=$((fail+1)); }

# Repo A is the caller's cwd; repo B owns the documents. The documents live in repoB/docs so a
# fix that stops at the document's directory is distinguishable from one that reaches the
# repository top-level.
mkdir -p "$TMP/bin" "$TMP/home" "$TMP/codex-state" "$TMP/repoA" "$TMP/repoB/docs" \
    "$TMP/repoB/state" "$TMP/repoB/state2" "$TMP/repoB/state3" "$TMP/deja"
export DEJA_REVIEW_STATE_ROOT="$TMP/deja"
git init -q "$TMP/repoA"
git init -q "$TMP/repoB"
printf 'a design owned by repo B\n' > "$TMP/repoB/docs/design.md"
printf 'a second design owned by repo B\n' > "$TMP/repoB/docs/design2.md"
printf 'a design reviewed by the fallback provider\n' > "$TMP/repoB/docs/design3.md"
printf 'supporting implementation notes\n' > "$TMP/repoB/docs/supporting.md"
SID="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

# --- stub claude: record cwd + repo-B visibility, then emit a valid round-2 envelope ---------
cat > "$TMP/bin/claude" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$PWD" >> "\$STUB_CWD_LOG"
if [ -f docs/supporting.md ]; then
  printf 'visible\n' >> "\$STUB_VIS_LOG"
else
  printf 'missing\n' >> "\$STUB_VIS_LOG"
fi
prompt="\$(cat)"
printf '%s\n' "\$prompt" | sed -n 's/^TARGET DOC (absolute path): //p' | head -n 1 \
  >> "\$STUB_DOC_LOG"
artifact_id="\$(printf '%s\n' "\$prompt" | sed -n 's/^ARTIFACT ID: //p' | head -n 1)"
artifact_sha="\$(printf '%s\n' "\$prompt" | sed -n 's/^ARTIFACT SHA256: //p' | head -n 1)"
round="\$(printf '%s\n' "\$prompt" | sed -n 's/^ROUND: //p' | head -n 1)"
PROJ="$TMP/home/.claude/projects/-tmp-fixture"
mkdir -p "\$PROJ"
cat > "\$PROJ/$SID.jsonl" <<JSONL
{"message":{"model":"claude-fable-5","content":[{"type":"tool_use","name":"Read","input":{"file_path":"docs/design.md"}}]}}
{"message":{"model":"claude-fable-5","content":[{"type":"tool_result","content":"a design owned by repo B","is_error":false}]}}
JSONL
python3 - "\$artifact_id" "\$artifact_sha" "\$round" <<'PY'
import json, sys
artifact_id, artifact_sha, round_ = sys.argv[1:4]
finding = {"reviewer": "CLAUDE-XFAMILY", "round": int(round_), "artifact_id": artifact_id,
           "artifact_sha": artifact_sha, "verdict": "GO",
           "schema_grounding_verdict": "PASS",
           "verify_commands_executed": ["read_file docs/design.md"],
           "source_artifacts": [], "dispositions": [], "findings": []}
print(json.dumps({"structured_output": finding, "result": json.dumps(finding),
                  "session_id": "$SID",
                  "modelUsage": {"claude-fable-5": {"inputTokens": 10}},
                  "num_turns": 2, "permission_denials": []}))
PY
STUB
chmod +x "$TMP/bin/claude"

# --- stub grok: record the --cwd it was handed, then emit a valid round-2 envelope -----------
cat > "$TMP/bin/grok" <<STUB
#!/usr/bin/env bash
prompt_file=""
while [ \$# -gt 0 ]; do
  case "\$1" in
    --cwd) printf '%s\n' "\$2" >> "\$STUB_CWD_LOG"; shift 2 ;;
    --prompt-file) prompt_file="\$2"; shift 2 ;;
    *) shift ;;
  esac
done
[ -n "\$prompt_file" ] || exit 64
prompt="\$(cat "\$prompt_file")"
artifact_id="\$(printf '%s\n' "\$prompt" | sed -n 's/^ARTIFACT ID: //p' | head -n 1)"
artifact_sha="\$(printf '%s\n' "\$prompt" | sed -n 's/^ARTIFACT SHA256: //p' | head -n 1)"
round="\$(printf '%s\n' "\$prompt" | sed -n 's/^ROUND: //p' | head -n 1)"
mkdir -p "$TMP/home/.grok/sessions/workspace/$SID"
cat > "$TMP/home/.grok/sessions/workspace/$SID/chat_history.jsonl" <<'JSONL'
{"type":"assistant","content":"reviewing","model_id":"grok-4.6","tool_calls":[{"id":"x","name":"read_file","arguments":"{}"}]}
{"type":"tool_result","content":"verified"}
{"type":"assistant","content":"done","model_id":"grok-4.6","tool_calls":[]}
JSONL
python3 - "\$artifact_id" "\$artifact_sha" "\$round" <<'PY'
import json, sys
artifact_id, artifact_sha, round_ = sys.argv[1:4]
finding = {"reviewer": "GROK-XFAMILY", "round": int(round_), "artifact_id": artifact_id,
           "artifact_sha": artifact_sha, "verdict": "GO",
           "schema_grounding_verdict": "PASS",
           "verify_commands_executed": ["read_file docs/design.md"],
           "source_artifacts": [], "dispositions": [], "findings": []}
print(json.dumps({"structuredOutput": finding, "text": json.dumps(finding),
                  "stopReason": "EndTurn", "sessionId": "$SID"}))
PY
STUB
chmod +x "$TMP/bin/grok"

# A prior synthesis + its source artifact, so the adapter can run round 2 for <doc>.
seed_round_1() {
    local doc="$1" state="$2" doc_sha doc_id source_path source_sha
    doc_sha="$(sha256sum "$doc" | cut -d' ' -f1)"
    doc_id="$(printf '%s' "$(realpath "$doc")" | sha256sum | cut -c1-16)"
    source_path="$state/round_1_source.json"
    printf '{"reviewer":"SOURCE","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["fixture"],"source_artifacts":[],"dispositions":[],"findings":[]}\n' \
        "$doc_id" "$doc_sha" > "$source_path"
    source_sha="$(sha256sum "$source_path" | cut -d' ' -f1)"
    printf '{"reviewer":"SYNTHESIS","round":1,"artifact_id":"%s","artifact_sha":"%s","verdict":"GO","schema_grounding_verdict":"PASS","verify_commands_executed":["fixture"],"source_artifacts":[{"path":"%s","sha256":"%s"}],"dispositions":[],"findings":[]}\n' \
        "$doc_id" "$doc_sha" "round_1_source.json" "$source_sha" > "$state/round_1_codex.json"
    local claim_line claim_id
    claim_line="$(python3 "$GUARD" claim "$doc" 1 fanout "$state")" || return 1
    claim_id="${claim_line##*CLAIM_ID=}"
    python3 "$GUARD" finish "$doc" "$claim_id" success >/dev/null || return 1
    python3 "$DEJA" select --target "$doc" --magi-state "$state" \
        --target-path-id "$doc_id" --target-sha "$doc_sha" \
        --protocol-sha "$(python3 "$PROTOCOL" sha)" >/dev/null
}

# Run the adapter from repo A with RELATIVE repo-B paths, exactly as a caller in another
# repository would.
run_adapter_from_repoA() {
    local log="$1" reviewer="$2" doc_rel="$3" prior_rel="$4" out_rel="$5"
    shift 5
    : > "$log"
    env PATH="$TMP/bin:$PATH" HOME="$TMP/home" STUB_CWD_LOG="$log" \
        STUB_VIS_LOG="$TMP/vis.log" STUB_DOC_LOG="$TMP/doc.log" "$@" \
        bash -c 'cd "$1" && shift && "$@"' _ "$TMP/repoA" \
        "$ADAPTER" --reviewer "$reviewer" "$doc_rel" 2 "$prior_rel" "$out_rel"
}

# --- 1. Claude arm ---------------------------------------------------------------------------
seed_round_1 "$TMP/repoB/docs/design.md" "$TMP/repoB/state" || exit 1
: > "$TMP/vis.log"
run_adapter_from_repoA "$TMP/cwd.log" claude ../repoB/docs/design.md \
    ../repoB/state/round_1_codex.json ../repoB/state/round_2_claude >/dev/null 2>&1
rc=$?
[ $rc -eq 0 ] && ok "relative cross-repo Claude round completes" \
              || bad "Claude adapter rc=$rc"
if [ "$(sort -u "$TMP/cwd.log")" = "$TMP/repoB" ]; then
  ok "Claude reviewer runs in the repo B worktree, not the caller's cwd"
else
  bad "Claude reviewer cwd is not the repo B root: $(sort -u "$TMP/cwd.log" | tr '\n' ' ')"
fi
[ "$(sort -u "$TMP/vis.log")" = "visible" ] \
    && ok "Claude reviewer can see repo B supporting files" \
    || bad "repo B supporting files not visible from the Claude reviewer cwd"
[ "$(sort -u "$TMP/doc.log")" = "$TMP/repoB/docs/design.md" ] \
    && ok "prompt carries the canonicalized absolute TARGET DOC" \
    || bad "TARGET DOC was not canonicalized: $(sort -u "$TMP/doc.log" | tr '\n' ' ')"
[ -f "$TMP/repoB/state/round_2_claude.json" ] \
    && [ -f "$TMP/repoB/state/round_2_claude.meta.json" ] \
    && ok "artifacts published to the canonical repo B out-prefix" \
    || bad "findings/meta pair missing from the repo B state dir"

# --- 2. Claude arm with an inherited GIT_DIR/GIT_WORK_TREE ------------------------------------
seed_round_1 "$TMP/repoB/docs/design2.md" "$TMP/repoB/state2" || exit 1
run_adapter_from_repoA "$TMP/cwd.log" claude ../repoB/docs/design2.md \
    ../repoB/state2/round_1_codex.json ../repoB/state2/round_2_leak \
    GIT_DIR="$TMP/repoA/.git" GIT_WORK_TREE="$TMP/repoA" >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ] && [ "$(sort -u "$TMP/cwd.log")" = "$TMP/repoB" ]; then
  ok "inherited GIT_DIR/GIT_WORK_TREE does not re-narrow Claude grounding"
else
  bad "GIT_DIR leak broke Claude grounding (rc=$rc cwd=$(sort -u "$TMP/cwd.log" | tr '\n' ' '))"
fi

# --- 3. Grok fallback arm --------------------------------------------------------------------
seed_round_1 "$TMP/repoB/docs/design3.md" "$TMP/repoB/state3" || exit 1
run_adapter_from_repoA "$TMP/cwd.log" grok ../repoB/docs/design3.md \
    ../repoB/state3/round_1_codex.json ../repoB/state3/round_2_grok >/dev/null 2>&1
rc=$?
[ $rc -eq 0 ] && ok "relative cross-repo Grok round completes" || bad "Grok adapter rc=$rc"
if [ "$(sort -u "$TMP/cwd.log")" = "$TMP/repoB" ]; then
  ok "Grok reviewer is handed --cwd of the repo B worktree, not the caller's cwd"
else
  bad "Grok --cwd is not the repo B root: $(sort -u "$TMP/cwd.log" | tr '\n' ' ')"
fi

# --- 4. Non-git document falls back to its own directory --------------------------------------
mkdir -p "$TMP/nogit/state"
printf 'an unversioned design\n' > "$TMP/nogit/design.md"
seed_round_1 "$TMP/nogit/design.md" "$TMP/nogit/state" || exit 1
run_adapter_from_repoA "$TMP/cwd.log" claude ../nogit/design.md \
    ../nogit/state/round_1_codex.json ../nogit/state/round_2_claude >/dev/null 2>&1
rc=$?
if [ $rc -eq 0 ] && [ "$(sort -u "$TMP/cwd.log")" = "$TMP/nogit" ]; then
  ok "non-git target falls back to the document directory as the Claude cwd"
else
  bad "non-git Claude fallback broken (rc=$rc cwd=$(sort -u "$TMP/cwd.log" | tr '\n' ' '))"
fi

# --- 5. Codex pre-flight ---------------------------------------------------------------------
# The pre-flight reviewers are sandboxed with a read-only root, so the assertion is made where
# the grounding is chosen: the -C the runner hands `codex exec`. A bwrap shim records it and
# then execs the real bubblewrap, leaving reviewer isolation intact.
REAL_BWRAP="$(command -v bwrap)"
cat > "$TMP/bin/bwrap" <<STUB
#!/usr/bin/env bash
for arg in "\$@"; do
  if [ "\${prev:-}" = "-C" ]; then printf '%s\n' "\$arg" >> "\$STUB_CWD_LOG"; fi
  prev="\$arg"
done
exec "$REAL_BWRAP" "\$@"
STUB
chmod +x "$TMP/bin/bwrap"
cat > "$TMP/bin/codex" <<'STUB'
#!/usr/bin/env python3
import json, pathlib, re, sys
args = sys.argv[1:]
output = pathlib.Path(args[args.index("-o") + 1])
prompt = sys.stdin.read()


def field(name):
    match = re.search(rf"^{name}: (.+)$", prompt, re.M)
    if not match:
        raise SystemExit(9)
    return match.group(1)


output.write_text(json.dumps({
    "schema": "magi-preflight-review/v1",
    "reviewer": field("REVIEWER"),
    "round": 1,
    "brief": {
        "canonical_path": field("BRIEF_CANONICAL_PATH"),
        "artifact_id": field("BRIEF_ARTIFACT_ID"),
        "sha256": field("BRIEF_SHA256"),
    },
    "verdict": "PROCEED",
    "findings": [],
}) + "\n")
STUB
chmod +x "$TMP/bin/codex"

printf 'change\nrisk boundary\nrollback path\n' > "$TMP/repoB/docs/brief.md"
: > "$TMP/preflight-cwd.log"
STUB_CWD_LOG="$TMP/preflight-cwd.log" PATH="$TMP/bin:$PATH" HOME="$TMP/home" \
    CODEX_HOME="$TMP/codex-state" \
    bash -c 'cd "$1" && shift && "$@"' _ "$TMP/repoA" \
    bash "$PREFLIGHT" "$TMP/repoB/docs/brief.md" "$TMP/repoB/preflight-out" \
    >"$TMP/preflight.out" 2>"$TMP/preflight.err"
rc=$?
[ $rc -eq 0 ] && ok "cross-repo Codex pre-flight completes" \
              || bad "pre-flight rc=$rc: $(tr '\n' ' ' < "$TMP/preflight.err")"
if [ "$(sort -u "$TMP/preflight-cwd.log")" = "$TMP/repoB" ] \
    && [ "$(wc -l < "$TMP/preflight-cwd.log")" -eq 3 ]; then
  ok "all three pre-flight reviewers are grounded in the repo B top-level"
else
  bad "pre-flight -C is not the repo B root: $(sort -u "$TMP/preflight-cwd.log" | tr '\n' ' ')"
fi

# A brief outside any git worktree keeps the documented directory fallback.
mkdir -p "$TMP/nogit-brief"
printf 'change\nrisk boundary\nrollback path\n' > "$TMP/nogit-brief/brief.md"
: > "$TMP/preflight-cwd.log"
STUB_CWD_LOG="$TMP/preflight-cwd.log" PATH="$TMP/bin:$PATH" HOME="$TMP/home" \
    CODEX_HOME="$TMP/codex-state" \
    bash -c 'cd "$1" && shift && "$@"' _ "$TMP/repoA" \
    bash "$PREFLIGHT" "$TMP/nogit-brief/brief.md" "$TMP/nogit-brief/out" \
    >/dev/null 2>"$TMP/preflight-nogit.err"
rc=$?
if [ $rc -eq 0 ] && [ "$(sort -u "$TMP/preflight-cwd.log")" = "$TMP/nogit-brief" ]; then
  ok "non-git brief falls back to the brief directory as the reviewer cwd"
else
  bad "non-git pre-flight fallback broken (rc=$rc cwd=$(sort -u "$TMP/preflight-cwd.log" | tr '\n' ' '))"
fi

echo "test_xfamily_target_root: $pass passed, $fail failed"
exit $((fail > 0))
