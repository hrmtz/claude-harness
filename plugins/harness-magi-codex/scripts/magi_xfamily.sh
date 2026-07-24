#!/usr/bin/env bash
# magi_xfamily.sh — provider-selectable cross-family reviewer adapter.
#
# Codex is the orchestrator. Claude is the default reviewer; Grok is an explicit fallback when
# Claude is unavailable. Both providers emit the same findings/meta contract and fail closed.
#
# Usage:
#   magi_xfamily.sh [--reviewer claude|grok] <doc> <round> <prior-json|-> <out-prefix>
#
# Exit: 0 complete · 2 fail-closed · 3 lock held · 4 campaign budget exhausted · 64 usage.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SELF_DIR/.." && pwd)"
SCHEMA_FILE="$PLUGIN_DIR/schemas/finding.schema.json"
SCRUB="$SELF_DIR/magi_scrub.py"
GUARD="$SELF_DIR/magi_campaign_guard.py"
VALIDATOR="$SELF_DIR/magi_validate_findings.py"
# shellcheck source=magi_lock.sh
source "$SELF_DIR/magi_lock.sh"

usage() {
    echo "usage: $0 [--reviewer claude|grok] <doc-path> <round> <prior-findings-json|-> <out-prefix>" >&2
    exit 64
}

REVIEWER="claude"
while [ $# -gt 0 ]; do
    case "$1" in
        --reviewer) [ $# -ge 2 ] || usage; REVIEWER="$2"; shift 2 ;;
        --) shift; break ;;
        -*) usage ;;
        *) break ;;
    esac
done
[ $# -eq 4 ] || usage
case "$REVIEWER" in claude|grok) ;; *) usage ;; esac

DOC_PATH="$1"; ROUND="$2"; PRIOR="$3"; OUT_PREFIX="$4"
[ -f "$DOC_PATH" ] || { echo "magi-xfamily: doc not found: $DOC_PATH" >&2; exit 64; }
[ -f "$SCHEMA_FILE" ] || { echo "magi-xfamily: schema not found: $SCHEMA_FILE" >&2; exit 64; }
case "$ROUND" in ''|*[!0-9]*) echo "magi-xfamily: round must be an integer: $ROUND" >&2; exit 64 ;; esac
[ "$ROUND" -ge 1 ] || { echo "magi-xfamily: round must be at least 1" >&2; exit 64; }
if [ "$ROUND" -gt 1 ] && [ "$PRIOR" = "-" ]; then
    echo "magi-xfamily: round $ROUND requires a prior synthesis JSON" >&2
    exit 64
fi
if [ "$PRIOR" != "-" ] && [ ! -f "$PRIOR" ]; then
    echo "magi-xfamily: prior findings not found: $PRIOR" >&2
    exit 64
fi

STATE_DIR="$(dirname "$OUT_PREFIX")"
mkdir -p "$STATE_DIR"
if [ "$PRIOR" != "-" ]; then
    python3 "$VALIDATOR" "$PRIOR" "$SCHEMA_FILE" --same-doc "$DOC_PATH" \
        --prior-for-round "$ROUND" --state-dir "$STATE_DIR" || {
        echo "magi-xfamily: prior synthesis failed identity/round/schema validation" >&2
        exit 64
    }
fi
TIMEOUT_S="${MAGI_XFAMILY_TIMEOUT_S:-900}"
case "$TIMEOUT_S" in
    ''|*[!0-9]*) echo "magi-xfamily: MAGI_XFAMILY_TIMEOUT_S must be an integer" >&2; exit 64 ;;
esac
[ "$TIMEOUT_S" -ge 1 ] && [ "$TIMEOUT_S" -le 900 ] || {
    echo "magi-xfamily: MAGI_XFAMILY_TIMEOUT_S must tighten the default into 1..900" >&2
    exit 64
}
DOC_REAL="$(realpath "$DOC_PATH")"
DOC_LOCK_ID="$(printf '%s' "$DOC_REAL" | sha256sum | cut -c1-16)"
DOC_CONTROL_DIR="$(dirname "$DOC_REAL")/.dual-magi"

PROMPT_FILE=""
RAW_FILE=""
STAGING_FINDINGS=""
STAGING_META=""
PROVIDER_PID=""
CLAIM_ID=""
CLAIM_FINISHED=0
_cleanup() {
    if [ -n "$PROVIDER_PID" ]; then
        kill -TERM "$PROVIDER_PID" 2>/dev/null || true
        wait "$PROVIDER_PID" 2>/dev/null || true
    fi
    [ -n "$PROMPT_FILE" ] && rm -f "$PROMPT_FILE"
    [ -n "$RAW_FILE" ] && rm -f "$RAW_FILE" "${RAW_FILE}.err"
    [ -n "$STAGING_FINDINGS" ] && rm -f "$STAGING_FINDINGS"
    [ -n "$STAGING_META" ] && rm -f "$STAGING_META"
    if [ -n "$CLAIM_ID" ] && [ "$CLAIM_FINISHED" -eq 0 ]; then
        python3 "$GUARD" finish "$DOC_PATH" "$CLAIM_ID" failed >/dev/null 2>&1 || true
    fi
    return 0
}
trap _cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

lock_rc=0
magi_lock_acquire "$DOC_CONTROL_DIR/.review.${DOC_LOCK_ID}.lock" || lock_rc=$?
case "$lock_rc" in
    0) ;;
    1) echo "magi-xfamily: lock held (recursion or concurrent review of this doc)" >&2; exit 3 ;;
    *) echo "magi-xfamily: cannot acquire doc lock (I/O error) in $DOC_CONTROL_DIR" >&2; exit 2 ;;
esac

case "$REVIEWER" in
    claude) MODEL="${MAGI_XFAMILY_CLAUDE_MODEL:-${MAGI_XFAMILY_MODEL:-claude-fable-5}}" ;;
    grok) MODEL="${MAGI_XFAMILY_GROK_MODEL:-grok-4.5}" ;;
esac
FINDINGS_OUT="${OUT_PREFIX}.json"
META_OUT="${OUT_PREFIX}.meta.json"
FAILED_OUT="${OUT_PREFIX}.FAILED.json"
rm -f "$FAILED_OUT"

ARTIFACT_SHA="$(sha256sum "$DOC_PATH" | cut -d' ' -f1)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

_fail_closed() {
    local reason="$1"
    python3 - "$FAILED_OUT" "$reason" "$ROUND" "$ARTIFACT_SHA" "$REVIEWER" <<'PY'
import json, sys
out, reason, rnd, sha, reviewer = sys.argv[1:6]
with open(out, "w") as fh:
    json.dump({"verdict": "UNPARSEABLE", "reason": reason, "round": int(rnd),
               "artifact_sha": sha, "reviewer_family": reviewer}, fh, indent=2)
PY
    echo "magi-xfamily[$REVIEWER]: FAIL-CLOSED ($reason). No plateau may be claimed. -> $FAILED_OUT" >&2
    exit 2
}

command -v "$REVIEWER" >/dev/null 2>&1 || _fail_closed "$REVIEWER CLI not found"

# The execution lock and provider capability check precede accounting. From this point onward a
# failed/abandoned provider attempt remains charged, including timeout or process crash.
claim_line="$(
    python3 "$GUARD" claim "$DOC_PATH" "$ROUND" xfamily "$STATE_DIR" \
        --owner-pid "$$" --adapter-kind xfamily
)" || exit $?
echo "$claim_line"
CLAIM_ID="${claim_line##*CLAIM_ID=}"
[ -n "$CLAIM_ID" ] || _fail_closed "campaign guard returned no claim id"
# Once this attempt is durably charged, stale canonical output from an earlier attempt must not
# masquerade as its result. New bytes remain claim-scoped until successful finish and promotion.
rm -f "$FINDINGS_OUT" "$META_OUT" "$FAILED_OUT"
STAGING_PREFIX="$STATE_DIR/.$(basename "$OUT_PREFIX").claim-${CLAIM_ID}"
STAGING_FINDINGS="${STAGING_PREFIX}.json"
STAGING_META="${STAGING_PREFIX}.meta.json"

PROMPT_FILE="$(mktemp)"
{
    cat <<'HDR'
You are the CROSS-FAMILY reviewer in a dual-magi design review. You are a different model
family from the Codex same-family reviewers. Find shared blind spots; do not merely restate them.

SCHEMA GROUNDING (mandatory): verify every load-bearing claim with the available read-only tools.
Report each tool operation (for example read_file(path) or grep(pattern,path)) verbatim in
verify_commands_executed. Any doc-vs-reality drift is CRITICAL. If you used no verification
tools, set schema_grounding_verdict to "FAIL".

Read-only review. Do not modify files. Do not read, print, or decrypt .env*, *.enc.yaml,
credentials*, auth files, or SSH keys.

FAMILY ROUTING REVIEW: Claude is preferred for design intent. Grok is an explicit fallback when
Claude is unavailable. The adapter provenance records the actual provider; preferred provider and
fallback reason remain an operator note. Do not accept silent same-family substitution.

CONVERGENCE CONTRACT (mandatory): dup_flag must be exactly one of new, duplicate, regression,
readiness-gap, or scope-expansion. After round 2, freeze the committed scope. Prioritize unresolved
prior blockers, regressions introduced by their fixes, and newly discovered unsafe or
unimplementable behavior inside the committed scope. Missing evidence explicitly scheduled for a
later phase is readiness-gap. An optional stronger guarantee or new subsystem is scope-expansion.
Neither readiness-gap nor scope-expansion may be REJECT, CRITICAL, or HIGH. If the committed
behavior itself is unsafe, classify it new or regression instead. Do not perpetuate review by
demanding optional scope. If those are the only findings, verdict must be GO-WITH-REVISE, not
REVISE or REJECT.

Return ONLY a JSON object conforming to the output schema.
HDR
    printf '\nREVIEWER FAMILY: %s\nROUND: %s\n' "$REVIEWER" "$ROUND"
    printf 'TARGET DOC (absolute path): %s\nARTIFACT ID: %s\nARTIFACT SHA256: %s\n' \
        "$DOC_PATH" "$DOC_LOCK_ID" "$ARTIFACT_SHA"
    if [ "$PRIOR" != "-" ] && [ -f "$PRIOR" ]; then
        printf '\nPRIOR FINDINGS (check resolution, do not merely repeat):\n'
        python3 "$SCRUB" < "$PRIOR"
    fi
    printf '\nDOCUMENT CONTENT:\n---\n'
    cat "$DOC_PATH"
    printf '\n---\n'
} > "$PROMPT_FILE"

RAW_FILE="$(mktemp)"
set +e
if [ "$REVIEWER" = "claude" ]; then
    (
        eval "exec ${MAGI_LOCK_FD}>&-"
        exec timeout --signal=TERM --kill-after=2s "$TIMEOUT_S" claude -p \
            --output-format json \
            --json-schema "$(cat "$SCHEMA_FILE")" \
            --model "$MODEL" \
            --safe-mode \
            --strict-mcp-config \
            --tools 'Read,Grep,Glob' \
            --permission-mode dontAsk \
            --allowedTools 'Read' 'Grep' 'Glob' \
            --disallowedTools 'Agent' 'Task' 'Edit' 'Write' 'NotebookEdit' 'Bash'
    ) < "$PROMPT_FILE" > "$RAW_FILE" 2>"${RAW_FILE}.err" &
else
    (
        eval "exec ${MAGI_LOCK_FD}>&-"
        exec timeout --signal=TERM --kill-after=2s "$TIMEOUT_S" grok \
            --prompt-file "$PROMPT_FILE" \
            --cwd "$PWD" \
            --model "$MODEL" \
            --effort high \
            --max-turns 40 \
            --no-memory \
            --no-subagents \
            --disable-web-search \
            --tools 'read_file,grep,list_dir' \
            --disallowed-tools 'search_tool,use_tool,Agent' \
            --deny 'MCPTool' \
            --sandbox read-only \
            --output-format json \
            --json-schema "$(cat "$SCHEMA_FILE")"
    ) > "$RAW_FILE" 2>"${RAW_FILE}.err" &
fi
PROVIDER_PID=$!
wait "$PROVIDER_PID"
rc=$?
PROVIDER_PID=""
set -e

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[ $rc -eq 0 ] || _fail_closed "$REVIEWER exited $rc (timeout=${TIMEOUT_S}s)"

if ! python3 - "$RAW_FILE" "$STAGING_FINDINGS" "$STAGING_META" "$ARTIFACT_SHA" \
        "$STARTED_AT" "$FINISHED_AT" "$SCRUB" "$REVIEWER" "$MODEL" <<'PY'
import glob, hashlib, json, os, re, subprocess, sys

(raw_path, findings_out, meta_out, artifact_sha, started, finished,
 scrub, reviewer, requested_model) = sys.argv[1:10]
env = json.load(open(raw_path))

if reviewer == "claude":
    obj = env.get("structured_output")
    text = env.get("result", "") or ""
    sid = env.get("session_id")
    model_usage = env.get("modelUsage") or {}
    model_keys = sorted(model_usage)
    num_turns = env.get("num_turns")
    denials = env.get("permission_denials", [])
    transcript_matches = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{glob.escape(str(sid))}.jsonl"))
    # model_id must name the model that actually authored the review. modelUsage can also carry
    # an auxiliary model (e.g. a haiku utility turn billed alongside the opus reviewer), and dict
    # order there is arbitrary — picking its first key mislabels the reviewer. The transcript's
    # assistant messages are authoritative; take the last recorded model (the final reviewer).
    transcript_model = None
    if len(transcript_matches) == 1:
        with open(transcript_matches[0], encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = (rec.get("message") or {}).get("model")
                if m:
                    transcript_model = m
    model_id = transcript_model or next(iter(model_usage), None)
else:
    obj = env.get("structuredOutput")
    text = env.get("text", "") or ""
    sid = env.get("sessionId")
    denials = []
    if env.get("stopReason") not in (None, "EndTurn"):
        raise SystemExit(f"grok stopReason={env.get('stopReason')!r}")
    transcript_matches = glob.glob(os.path.expanduser(
        f"~/.grok/sessions/*/{glob.escape(str(sid))}/chat_history.jsonl"
    ))
    model_ids = []
    num_turns = 0
    if transcript_matches:
        with open(transcript_matches[0], encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "assistant":
                    num_turns += 1
                    if rec.get("model_id"):
                        model_ids.append(rec["model_id"])
    model_id = model_ids[-1] if model_ids else requested_model
    model_keys = sorted(set(model_ids or [model_id]))

if not isinstance(obj, dict) or not obj:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if match:
        text = match.group(1)
    obj = json.loads(text.strip())
if not isinstance(obj, dict) or obj.get("verdict") is None:
    raise SystemExit("reviewer output is not a verdict object")
if not sid or len(transcript_matches) != 1:
    raise SystemExit(f"session transcript resolution count={len(transcript_matches)}")

def scrubbed(value):
    proc = subprocess.run([sys.executable, scrub], input=json.dumps(value), text=True,
                          capture_output=True, check=True)
    return json.loads(proc.stdout)

obj = scrubbed(obj)
with open(findings_out, "w") as fh:
    json.dump(obj, fh, indent=2, ensure_ascii=False)

transcript_path = os.path.realpath(transcript_matches[0])
meta = {
    "reviewer_family": reviewer,
    "session_id": sid,
    "model_id": model_id,
    # requested_model is recorded independently of the transcript (from --model / env). The gate
    # compares it against the model the transcript actually ran, which detects a silent
    # same-family downgrade (requested opus, CLI served haiku) — a check model_id alone cannot make
    # because model_id is itself derived from that transcript.
    "requested_model": requested_model,
    "model_usage_keys": model_keys,
    "num_turns": num_turns,
    "permission_denials": denials,
    "artifact_sha": artifact_sha,
    "started_at": started,
    "finished_at": finished,
    "transcript_path": transcript_path,
    "transcript_sha": hashlib.sha256(open(transcript_path, "rb").read()).hexdigest(),
}
meta = scrubbed(meta)
meta["output_sha"] = hashlib.sha256(open(findings_out, "rb").read()).hexdigest()
with open(meta_out, "w") as fh:
    json.dump(meta, fh, indent=2, ensure_ascii=False)
print(f"magi-xfamily[{reviewer}]: verdict={obj.get('verdict')} "
      f"grounding={obj.get('schema_grounding_verdict')} findings={len(obj.get('findings', []))} "
      f"model={model_id}")
PY
then
    _fail_closed "unparseable reviewer output"
fi

if ! python3 "$VALIDATOR" "$STAGING_FINDINGS" "$SCHEMA_FILE" --doc "$DOC_PATH"; then
    _fail_closed "findings violate schema or convergence contract"
fi

if ! python3 "$GUARD" finish "$DOC_PATH" "$CLAIM_ID" success >/dev/null; then
    _fail_closed "claim no longer permits successful output promotion"
fi
CLAIM_FINISHED=1
mv "$STAGING_FINDINGS" "$FINDINGS_OUT"
STAGING_FINDINGS=""
mv "$STAGING_META" "$META_OUT"
STAGING_META=""
rm -f "$FAILED_OUT"
echo "magi-xfamily[$REVIEWER]: wrote $FINDINGS_OUT + $META_OUT"
