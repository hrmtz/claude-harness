#!/usr/bin/env bash
# magi_xfamily_claude.sh — cross-family reviewer adapter: Codex orchestrator -> Claude reviewer.
#
# Design: docs/designs/CODEX_MAGI_MIRROR.md §4 (plateau'd v0.5, 5 dual-magi rounds).
#
# Threat model: T1 (accidental skip / buggy script). NOT T2 (adversarial same-UID process).
# Nothing here is forgery-resistant against a process running as this user. Do not claim it is.
#
# Exit codes (contract; asserted by tests/test_docs_match_scripts.py):
#   0  cross-family round completed, findings + meta written
#   2  fail-closed: the round did not produce a usable result (no plateau may be claimed)
#   3  lock held: recursion, or a concurrent review of the same doc
#
# Env:
#   MAGI_XFAMILY_MODEL      default claude-fable-5
#   MAGI_XFAMILY_TIMEOUT_S  default 900
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SELF_DIR/.." && pwd)"
SCHEMA_FILE="$PLUGIN_DIR/schemas/finding.schema.json"
SCRUB="$SELF_DIR/magi_scrub.py"
# shellcheck source=magi_lock.sh
source "$SELF_DIR/magi_lock.sh"

usage() { echo "usage: $0 <doc-path> <round> <prior-findings-json|-> <out-prefix>" >&2; exit 64; }
[ $# -eq 4 ] || usage

DOC_PATH="$1"; ROUND="$2"; PRIOR="$3"; OUT_PREFIX="$4"
[ -f "$DOC_PATH" ] || { echo "magi-xfamily: doc not found: $DOC_PATH" >&2; exit 64; }
[ -f "$SCHEMA_FILE" ] || { echo "magi-xfamily: schema not found: $SCHEMA_FILE" >&2; exit 64; }
# ROUND is embedded in the FAILED sentinel as an integer; reject a non-numeric label here rather
# than crashing the sentinel writer on the failure path (which would silently break fail-closed).
case "$ROUND" in ''|*[!0-9]*) echo "magi-xfamily: round must be an integer: $ROUND" >&2; exit 64 ;; esac

STATE_DIR="$(dirname "$OUT_PREFIX")"
mkdir -p "$STATE_DIR"

# --- single cleanup, single EXIT trap -----------------------------------------
# A second `trap ... EXIT` REPLACES the first (measured). One handler, one trap.
PROMPT_FILE=""
RAW_FILE=""
_cleanup() {
    [ -n "$PROMPT_FILE" ] && rm -f "$PROMPT_FILE"
    # The CLI's stderr goes to "${RAW_FILE}.err" -- remove it too, or one orphan
    # accumulates in TMPDIR on every single run.
    [ -n "$RAW_FILE" ] && rm -f "$RAW_FILE" "${RAW_FILE}.err"
    return 0
}
trap _cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- INV-7: flock. Kernel releases on fd close, including SIGKILL. ------------
# magi_lock_acquire returns 1 for contention and 2 for "cannot open the lock file". Do not
# conflate them: an unwritable state dir is an I/O fault (fail closed), not a recursion guard.
lock_rc=0
magi_lock_acquire "$STATE_DIR/.xfamily.lock" || lock_rc=$?
case "$lock_rc" in
    0) ;;
    1) echo "magi-xfamily: lock held (recursion or concurrent review of this doc)" >&2; exit 3 ;;
    *) echo "magi-xfamily: cannot acquire lock (I/O error) in $STATE_DIR" >&2; exit 2 ;;
esac

MODEL="${MAGI_XFAMILY_MODEL:-claude-fable-5}"
TIMEOUT_S="${MAGI_XFAMILY_TIMEOUT_S:-900}"

FINDINGS_OUT="${OUT_PREFIX}.json"
META_OUT="${OUT_PREFIX}.meta.json"
FAILED_OUT="${OUT_PREFIX}.FAILED.json"
# A failure must never be written at the success path's filename: a downstream
# "does the xfamily file exist?" check would turn fail-closed back into fail-open.
#
# Clear the SUCCESS artifacts too, not just the sentinel. Otherwise a failed re-run leaves
# the previous run's findings+meta in place and the plateau gate happily certifies the OLD
# round -- G3 cannot catch it, because the doc bytes have not changed. That is precisely the
# "stale artifact reused as fresh" T1 failure this whole gate exists to block.
rm -f "$FINDINGS_OUT" "$META_OUT" "$FAILED_OUT"

ARTIFACT_SHA="$(sha256sum "$DOC_PATH" | cut -d' ' -f1)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

_fail_closed() {
    local reason="$1"
    python3 - "$FAILED_OUT" "$reason" "$ROUND" "$ARTIFACT_SHA" <<'PY'
import json, sys
out, reason, rnd, sha = sys.argv[1:5]
json.dump({"verdict": "UNPARSEABLE", "reason": reason, "round": int(rnd),
           "artifact_sha": sha}, open(out, "w"), indent=2)
PY
    echo "magi-xfamily: FAIL-CLOSED ($reason). No plateau may be claimed. -> $FAILED_OUT" >&2
    exit 2
}

# --- prerequisites -------------------------------------------------------------
# Checked AFTER the stale-artifact rm and via _fail_closed: a missing CLI must leave no prior
# round's findings on disk (the gate would certify them) and must write the sentinel the
# fail-closed contract promises.
command -v claude >/dev/null 2>&1 || _fail_closed "claude CLI not found"

# --- build the prompt; deliver on stdin, never in argv -------------------------
# argv is world-readable via /proc/<pid>/cmdline for the whole run.
PROMPT_FILE="$(mktemp)"   # 0600
{
    cat <<'HDR'
You are the CROSS-FAMILY reviewer in a dual-magi design review. You are a different model
family from the same-family reviewers; your job is to cancel their shared training bias by
finding what they all missed. Re-stating their findings adds nothing.

SCHEMA GROUNDING (mandatory): the document makes claims about real CLI behavior, real files,
and real repo state. Verify every load-bearing claim by RUNNING a command. Report each command
verbatim in verify_commands_executed. Any doc-vs-reality drift is a CRITICAL finding. If you
ran no verification commands, you must self-report schema_grounding_verdict as "FAIL".

Read-only review. Do not modify files. Do not read, print, or decrypt any credential file,
any *.enc.yaml, or any auth.json.

Return ONLY a JSON object conforming to the output schema you were given.
HDR
    printf '\nROUND: %s\n' "$ROUND"
    printf 'TARGET DOC (absolute path): %s\n' "$DOC_PATH"
    printf 'ARTIFACT SHA256: %s\n' "$ARTIFACT_SHA"
    if [ "$PRIOR" != "-" ] && [ -f "$PRIOR" ]; then
        printf '\nPRIOR-ROUND FINDINGS (judge whether each is resolved; do not merely restate):\n'
        # Defense in depth: scrub prior findings again before they re-enter a prompt
        # bound for another vendor's API.
        python3 "$SCRUB" < "$PRIOR"
    fi
    printf '\nDOCUMENT CONTENT FOLLOWS (also readable at the path above):\n---\n'
    cat "$DOC_PATH"
    printf '\n---\n'
} > "$PROMPT_FILE"

# --- invoke ------------------------------------------------------------------
# --json-schema takes INLINE JSON only: "@file" fails with "Unrecognized token '@'" (measured).
# --permission-mode dontAsk + explicit --disallowedTools: acceptEdits writes files even when
# Edit/Write are absent from --allowedTools (measured). Allowlist alone is not a denial.
RAW_FILE="$(mktemp)"
set +e
timeout "$TIMEOUT_S" claude -p \
    --output-format json \
    --json-schema "$(cat "$SCHEMA_FILE")" \
    --model "$MODEL" \
    --permission-mode dontAsk \
    --allowedTools 'Read' 'Grep' 'Glob' 'Bash(grep:*)' 'Bash(rg:*)' 'Bash(git log:*)' 'Bash(git show:*)' \
    --disallowedTools 'Edit' 'Write' 'NotebookEdit' \
    < "$PROMPT_FILE" > "$RAW_FILE" 2>"${RAW_FILE}.err"
rc=$?
set -e

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

[ $rc -eq 0 ] || _fail_closed "claude exited $rc (timeout=${TIMEOUT_S}s)"

# --- extract, scrub, persist --------------------------------------------------
# structured_output is the schema-validated object. Fall back to parsing .result for
# older CLIs, which wrap it in a markdown fence.
#
# A parse failure here must go through _fail_closed, not a bare `exit 2`: the contract
# promises a FAILED sentinel for unparseable output, and a partially-written findings file
# must never survive to be certified by the gate.
if ! python3 - "$RAW_FILE" "$FINDINGS_OUT" "$META_OUT" "$ARTIFACT_SHA" "$STARTED_AT" "$FINISHED_AT" "$SCRUB" <<'PY'
import json, re, subprocess, sys

raw_path, findings_out, meta_out, artifact_sha, started, finished, scrub = sys.argv[1:8]
env = json.load(open(raw_path))

obj = env.get("structured_output")
if not isinstance(obj, dict) or not obj:
    # Covers None, {}, and a non-dict (a list would otherwise be written out verbatim).
    txt = env.get("result", "") or ""
    m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    if m:
        txt = m.group(1)
    obj = json.loads(txt.strip())
if not isinstance(obj, dict) or obj.get("verdict") is None:
    raise SystemExit("reviewer output is not a verdict object")

def scrubbed(o):
    p = subprocess.run([sys.executable, scrub], input=json.dumps(o), text=True,
                       capture_output=True, check=True)
    return json.loads(p.stdout)

obj = scrubbed(obj)
with open(findings_out, "w") as f:
    json.dump(obj, f, indent=2, ensure_ascii=False)

# INV-4: the reviewer's self-report is not trusted on its own. session_id is recorded here so
# that magi_plateau_gate.sh G9 can cross-check the claimed commands against the transcript's
# tool_use events. This detects omission and inconsistency -- never semantic truth.
model_usage = env.get("modelUsage") or {}
meta = {
    "session_id": env.get("session_id"),
    "model_id": next(iter(model_usage), None),
    "model_usage_keys": sorted(model_usage),
    "num_turns": env.get("num_turns"),
    "permission_denials": env.get("permission_denials", []),
    "artifact_sha": artifact_sha,
    "started_at": started,
    "finished_at": finished,
}
meta = scrubbed(meta)
# output_sha binds the meta to the exact findings bytes we just wrote (G4).
import hashlib
meta["output_sha"] = hashlib.sha256(open(findings_out, "rb").read()).hexdigest()
with open(meta_out, "w") as f:
    json.dump(meta, f, indent=2, ensure_ascii=False)
print(f"magi-xfamily: verdict={obj.get('verdict')} grounding={obj.get('schema_grounding_verdict')} "
      f"findings={len(obj.get('findings', []))} model={meta['model_id']}")
PY
then
    # Partial artifacts must not survive: the gate would otherwise certify a half-written round.
    rm -f "$FINDINGS_OUT" "$META_OUT"
    _fail_closed "unparseable reviewer output"
fi

echo "magi-xfamily: wrote $FINDINGS_OUT + $META_OUT"
