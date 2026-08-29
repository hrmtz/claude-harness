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
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
SCHEMA_FILE="$PLUGIN_DIR/schemas/finding.schema.json"
PROVIDER_SCHEMA_FILE="$PLUGIN_DIR/schemas/finding.codex.schema.json"
SCRUB="$SELF_DIR/magi_scrub.py"
GUARD="$SELF_DIR/magi_campaign_guard.py"
VALIDATOR="$SELF_DIR/magi_validate_findings.py"
PROTOCOL="$SELF_DIR/magi_protocol.py"
DEJA="$SELF_DIR/magi_deja_context.py"
VERIFY_XFAMILY="$SELF_DIR/magi_verify_xfamily_artifacts.py"
CROSS_CLI_GUARD="${HARNESS_CROSS_CLI_GUARD:-}"
if [ -z "$CROSS_CLI_GUARD" ]; then
    CROSS_CLI_GUARD="$(command -v harness-cross-cli 2>/dev/null || true)"
fi
if [ -z "$CROSS_CLI_GUARD" ] && [ -x "$REPO_ROOT/plugins/harness-core/bin/harness-cross-cli" ]; then
    CROSS_CLI_GUARD="$REPO_ROOT/plugins/harness-core/bin/harness-cross-cli"
fi
if [ -z "$CROSS_CLI_GUARD" ]; then
    for cache_root in "${CODEX_HOME:-$HOME/.codex}/plugins/cache" "$HOME/.claude/plugins/cache"; do
        [ -d "$cache_root" ] || continue
        while IFS= read -r candidate; do
            CROSS_CLI_GUARD="$candidate"
        done < <(find "$cache_root" -type f \
            -path '*/harness-core/*/bin/harness-cross-cli' -perm -u+x 2>/dev/null | sort)
    done
fi
# shellcheck source=magi_lock.sh
source "$SELF_DIR/magi_lock.sh"
# shellcheck source=magi_target_root.sh
source "$SELF_DIR/magi_target_root.sh"

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
MAX_ARTIFACT_BYTES="${MAGI_MAX_ARTIFACT_BYTES:-10485760}"
case "$MAX_ARTIFACT_BYTES" in
    ''|*[!0-9]*) echo "magi-xfamily: MAGI_MAX_ARTIFACT_BYTES must be an integer" >&2; exit 64 ;;
esac
[ "$MAX_ARTIFACT_BYTES" -ge 1 ] && [ "$MAX_ARTIFACT_BYTES" -le 10485760 ] || {
    echo "magi-xfamily: MAGI_MAX_ARTIFACT_BYTES must tighten the default into 1..10485760" >&2
    exit 64
}
ARTIFACT_BYTES="$(stat -c %s -- "$DOC_PATH")" || {
    echo "magi-xfamily: cannot stat review artifact: $DOC_PATH" >&2
    exit 64
}
[ "$ARTIFACT_BYTES" -ge 1 ] || {
    echo "magi-xfamily: review artifact must not be empty" >&2
    exit 64
}
[ "$ARTIFACT_BYTES" -le "$MAX_ARTIFACT_BYTES" ] || {
    echo "magi-xfamily: review artifact exceeds ${MAX_ARTIFACT_BYTES}-byte limit" >&2
    exit 64
}
[ -f "$SCHEMA_FILE" ] || { echo "magi-xfamily: schema not found: $SCHEMA_FILE" >&2; exit 64; }
[ -f "$PROVIDER_SCHEMA_FILE" ] || {
    echo "magi-xfamily: provider schema not found: $PROVIDER_SCHEMA_FILE" >&2
    exit 64
}
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
# gh #151: canonicalize every caller-supplied path before the provider is spawned. The
# reviewer launches with the TARGET repository as its cwd (below), not the caller's cwd, so
# a relative DOC_PATH/OUT_PREFIX/PRIOR would otherwise resolve against the wrong repository
# — including the absolute TARGET DOC path the prompt hands the reviewer to verify against.
STATE_DIR="$(realpath "$STATE_DIR")"
OUT_PREFIX="$STATE_DIR/$(basename "$OUT_PREFIX")"
DOC_PATH="$(realpath "$DOC_PATH")"
if [ "$PRIOR" != "-" ]; then
    PRIOR="$(realpath "$PRIOR")"
    python3 "$VALIDATOR" "$PRIOR" "$SCHEMA_FILE" --same-doc "$DOC_PATH" \
        --prior-for-round "$ROUND" --state-dir "$STATE_DIR" || {
        echo "magi-xfamily: prior synthesis failed identity/round/schema validation" >&2
        exit 64
    }
    PRIOR_SHA="$(sha256sum "$PRIOR" | cut -d' ' -f1)"
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
# The cross-family round is the MANDATORY independent gate, so its grounding is the one
# nobody double-checks: a reviewer running in the harness checkout still reports
# verify_commands_executed that succeeded — against the wrong tree (gh #151). Ground it in
# the repository that owns the document, exactly as the fan-out arm does.
TARGET_ROOT="$(magi_target_root "$DOC_REAL" magi-xfamily)" || exit 64
# The target-root lookup deliberately ignores ambient repository overrides. Keep them from
# contaminating protocol snapshot git reads and the reviewer subprocess too.
unset GIT_DIR GIT_WORK_TREE

PROMPT_FILE=""
RAW_FILE=""
STAGING_FINDINGS=""
STAGING_META=""
SNAPSHOT_ROOT=""
SNAPSHOT_DOC=""
SNAPSHOT_PRIOR=""
DEJA_BLOCK=""
PUBLISHED=()
PROVIDER_PID=""
CLAIM_ID=""
CLAIM_FINISHED=0
_cleanup() {
    local original_rc=$? cleanup_failed=0 claim_status="" claim_status_rc=0
    set +e
    if [ -n "$PROVIDER_PID" ]; then
        kill -TERM "$PROVIDER_PID" 2>/dev/null || true
        wait "$PROVIDER_PID" 2>/dev/null || true
    fi
    if [ -n "$PROMPT_FILE" ] && ! rm -f "$PROMPT_FILE"; then cleanup_failed=1; fi
    if [ -n "$RAW_FILE" ] && ! rm -f "$RAW_FILE" "${RAW_FILE}.err"; then cleanup_failed=1; fi
    if [ -n "$STAGING_FINDINGS" ] && ! rm -f "$STAGING_FINDINGS"; then cleanup_failed=1; fi
    if [ -n "$STAGING_META" ] && ! rm -f "$STAGING_META"; then cleanup_failed=1; fi
    if [ -n "$CLAIM_ID" ] && [ "$CLAIM_FINISHED" -eq 0 ]; then
        claim_status="$(python3 "$GUARD" claim-status "$DOC_PATH" "$CLAIM_ID")"
        claim_status_rc=$?
        if [ "$claim_status" = "success" ]; then
            CLAIM_FINISHED=1
            PUBLISHED=()
        else
            [ "$claim_status_rc" -eq 0 ] || cleanup_failed=1
            if [ ${#PUBLISHED[@]} -gt 0 ] && ! rm -f -- "${PUBLISHED[@]}"; then
                cleanup_failed=1
            fi
            if ! python3 "$GUARD" finish "$DOC_PATH" "$CLAIM_ID" failed >/dev/null; then
                echo "magi-xfamily: ERROR: failed to finalize claim $CLAIM_ID as failed" >&2
                cleanup_failed=1
            fi
        fi
    fi
    if [ -n "$SNAPSHOT_DOC" ] && ! rm -f "$SNAPSHOT_DOC"; then cleanup_failed=1; fi
    if [ -n "$SNAPSHOT_PRIOR" ] && [ "$SNAPSHOT_PRIOR" != "-" ] \
            && ! rm -f "$SNAPSHOT_PRIOR"; then cleanup_failed=1; fi
    if [ -n "$DEJA_BLOCK" ] && ! rm -f "$DEJA_BLOCK"; then cleanup_failed=1; fi
    if [ -n "$SNAPSHOT_ROOT" ] && ! rm -rf "$SNAPSHOT_ROOT"; then cleanup_failed=1; fi
    [ "$cleanup_failed" -eq 0 ] || {
        echo "magi-xfamily: ERROR: cleanup was incomplete for claim ${CLAIM_ID:-unclaimed}" >&2
    }
    if [ "$cleanup_failed" -ne 0 ] && [ "$original_rc" -eq 0 ]; then
        trap - EXIT
        exit 1
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
    grok) MODEL="${MAGI_XFAMILY_GROK_MODEL:-grok-4.6}" ;;
esac
[ -x "$CROSS_CLI_GUARD" ] || {
    echo "magi-xfamily: harness-cross-cli is required for provider identity isolation" >&2
    exit 2
}
FINDINGS_OUT="${OUT_PREFIX}.json"
META_OUT="${OUT_PREFIX}.meta.json"
FAILED_OUT="${OUT_PREFIX}.FAILED.json"
rm -f "$FAILED_OUT"

ARTIFACT_SHA="$(sha256sum "$DOC_PATH" | cut -d' ' -f1)"
PROTOCOL_SHA="$(python3 "$PROTOCOL" sha)" || {
    echo "magi-xfamily: runtime protocol manifest is invalid or stale" >&2
    exit 2
}
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

_fail_closed() {
    local reason="$1" provider_status="${2:-}" diagnostic_published=0
    if python3 - "$FAILED_OUT" "$reason" "$ROUND" "$ARTIFACT_SHA" "$REVIEWER" \
        "$provider_status" "${RAW_FILE:+${RAW_FILE}.err}" "$SCRUB" <<'PY'
import json, os, stat, subprocess, sys
out, reason, rnd, sha, reviewer, status, stderr_path, scrubber = sys.argv[1:9]
diagnostic = ""
diagnostic_truncated = False
diagnostic_unavailable = ""
try:
    info = os.stat(stderr_path, follow_symlinks=False)
except FileNotFoundError:
    diagnostic_unavailable = "missing"
except OSError as exc:
    diagnostic_unavailable = f"io-error:{type(exc).__name__}"
else:
    if stat.S_ISREG(info.st_mode):
        with open(stderr_path, "rb") as handle:
            raw = handle.read(65537)
        diagnostic_truncated = len(raw) > 65536
        raw = raw[:65536]
        scrubbed = subprocess.run(
            [sys.executable, scrubber],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if scrubbed.returncode == 0:
            diagnostic = scrubbed.stdout.decode("utf-8", errors="replace")
        else:
            diagnostic = "provider stderr scrub failed closed"
payload = {"verdict": "UNPARSEABLE", "reason": reason, "round": int(rnd),
           "artifact_sha": sha, "reviewer_family": reviewer,
           "provider_command": reviewer, "diagnostic": diagnostic,
           "diagnostic_truncated": diagnostic_truncated}
if diagnostic_unavailable:
    payload["diagnostic_unavailable"] = diagnostic_unavailable
if status:
    payload["provider_exit_status"] = int(status)
with open(out, "w") as fh:
    json.dump(payload, fh, indent=2)
PY
    then
        [ -s "$FAILED_OUT" ] && diagnostic_published=1
    else
        echo "magi-xfamily[$REVIEWER]: ERROR: detailed failure diagnostic publication failed" >&2
    fi
    if [ "$diagnostic_published" -eq 0 ]; then
        if python3 - "$FAILED_OUT" "$reason" "$ROUND" "$ARTIFACT_SHA" "$REVIEWER" <<'PY'
import json, sys
out, reason, rnd, sha, reviewer = sys.argv[1:6]
with open(out, "w") as fh:
    json.dump({"verdict":"UNPARSEABLE", "reason":reason, "round":int(rnd),
               "artifact_sha":sha, "reviewer_family":reviewer,
               "diagnostic_unavailable":"publication-failed"}, fh)
PY
        then
            [ -s "$FAILED_OUT" ] && diagnostic_published=1
        fi
    fi
    if [ "$diagnostic_published" -eq 1 ]; then
        echo "magi-xfamily[$REVIEWER]: FAIL-CLOSED ($reason). No plateau may be claimed. -> $FAILED_OUT" >&2
    else
        echo "magi-xfamily[$REVIEWER]: FAIL-CLOSED ($reason). Durable diagnostics unavailable." >&2
    fi
    exit 2
}

command -v "$REVIEWER" >/dev/null 2>&1 || _fail_closed "$REVIEWER CLI not found"

# The execution lock and provider capability check precede accounting. From this point onward a
# failed/abandoned provider attempt remains charged, including timeout or process crash.
claim_line="$(
    python3 "$GUARD" claim "$DOC_PATH" "$ROUND" xfamily "$STATE_DIR" \
        --owner-pid "$$" --adapter-kind xfamily \
        --expected-artifact-sha "$ARTIFACT_SHA" --reviewer-family "$REVIEWER"
)" || exit $?
echo "$claim_line"
CLAIM_ID="${claim_line##*CLAIM_ID=}"
[ -n "$CLAIM_ID" ] || _fail_closed "campaign guard returned no claim id"
CLAIM_PROTOCOL_SHA="${claim_line#*PROTOCOL_SHA=}"
CLAIM_PROTOCOL_SHA="${CLAIM_PROTOCOL_SHA%%;*}"
[[ "$CLAIM_PROTOCOL_SHA" =~ ^[0-9a-f]{64}$ ]] || {
    _fail_closed "campaign guard returned an invalid protocol digest"
}
[ "$CLAIM_PROTOCOL_SHA" = "$PROTOCOL_SHA" ] || {
    _fail_closed "protocol changed between adapter preflight and campaign claim"
}
# Once this attempt is durably charged, stale canonical output from an earlier attempt must not
# masquerade as its result. New bytes remain claim-scoped until successful finish and promotion.
rm -f "$FINDINGS_OUT" "$META_OUT" "$FAILED_OUT"
STAGING_PREFIX="$STATE_DIR/.$(basename "$OUT_PREFIX").claim-${CLAIM_ID}"
STAGING_FINDINGS="${STAGING_PREFIX}.json"
STAGING_META="${STAGING_PREFIX}.meta.json"
SNAPSHOT_ROOT="${STAGING_PREFIX}.protocol"
python3 "$PROTOCOL" snapshot "$SNAPSHOT_ROOT" "$CLAIM_PROTOCOL_SHA" >/dev/null || {
    _fail_closed "protocol inputs changed while creating the claim snapshot"
}
SNAPSHOT_PLUGIN="$SNAPSHOT_ROOT/plugins/harness-magi-codex"
SCHEMA_FILE="$SNAPSHOT_PLUGIN/schemas/finding.schema.json"
PROVIDER_SCHEMA_FILE="$SNAPSHOT_PLUGIN/schemas/finding.codex.schema.json"
SCRUB="$SNAPSHOT_PLUGIN/scripts/magi_scrub.py"
GUARD="$SNAPSHOT_PLUGIN/scripts/magi_campaign_guard.py"
VALIDATOR="$SNAPSHOT_PLUGIN/scripts/magi_validate_findings.py"
VERIFY_XFAMILY="$SNAPSHOT_PLUGIN/scripts/magi_verify_xfamily_artifacts.py"
DEJA="$SNAPSHOT_PLUGIN/scripts/magi_deja_context.py"
python3 - "$DOC_PATH" "${STAGING_PREFIX}.document" "$ARTIFACT_SHA" <<'PY'
import hashlib, pathlib, sys
source, target = map(pathlib.Path, sys.argv[1:3])
digest = hashlib.sha256()
with source.open("rb") as reader, target.open("wb") as writer:
    for chunk in iter(lambda: reader.read(1024 * 1024), b""):
        digest.update(chunk)
        writer.write(chunk)
if digest.hexdigest() != sys.argv[3]:
    raise SystemExit("review artifact changed after campaign admission")
PY
SNAPSHOT_DOC="${STAGING_PREFIX}.document"
SNAPSHOT_PRIOR="-"
if [ "$PRIOR" != "-" ]; then
    cp -- "$PRIOR" "${STAGING_PREFIX}.prior.json"
    SNAPSHOT_PRIOR="${STAGING_PREFIX}.prior.json"
    [ "$(sha256sum "$SNAPSHOT_PRIOR" | cut -d' ' -f1)" = "$PRIOR_SHA" ] || {
        _fail_closed "prior synthesis changed after preflight validation"
    }
fi

DEJA_BLOCK="${STAGING_PREFIX}.deja-block"
python3 "$DEJA" render \
    --target "$DOC_PATH" --magi-state "$STATE_DIR" \
    --target-path-id "$DOC_LOCK_ID" --target-sha "$ARTIFACT_SHA" \
    --protocol-sha "$CLAIM_PROTOCOL_SHA" --output "$DEJA_BLOCK" || {
    _fail_closed "frozen Deja context identity/render validation failed"
}

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
readiness-gap, or scope-expansion. Every REJECT, CRITICAL, or HIGH finding MUST include a stable,
non-empty root_cause_id and subsystem so repeated roots and subsystem recurrence can be evaluated
mechanically. Reuse a prior root_cause_id when the underlying cause is unchanged; do not mint a new
id for a rewording. After round 2, freeze the committed scope. Prioritize unresolved
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
    if [ "$SNAPSHOT_PRIOR" != "-" ]; then
        printf '\nPRIOR FINDINGS (check resolution, do not merely repeat):\n'
        python3 "$SCRUB" < "$SNAPSHOT_PRIOR"
    fi
    if [ -s "$DEJA_BLOCK" ]; then
        printf '\n'
        cat "$DEJA_BLOCK"
    fi
    printf '\nDOCUMENT CONTENT:\n---\n'
    cat "$SNAPSHOT_DOC"
    printf '\n---\n'
} > "$PROMPT_FILE"

# Receipt publication is the launch boundary: a provider must not run if exact prompt
# consumption cannot be proven.
python3 "$DEJA" consume \
    --target "$DOC_PATH" --magi-state "$STATE_DIR" \
    --target-path-id "$DOC_LOCK_ID" --target-sha "$ARTIFACT_SHA" \
    --protocol-sha "$CLAIM_PROTOCOL_SHA" --phase xfamily --round "$ROUND" \
    --block "$DEJA_BLOCK" --provider "$REVIEWER" --prompt "$PROMPT_FILE" || {
    _fail_closed "Deja prompt-consumption receipt publication failed"
}

RAW_FILE="$(mktemp)"
# The Claude CLI has no cwd flag: its reviewer grounding IS the process working directory, so
# the launch subshell must chdir into the target repository. Resolve everything the exec line
# still needs (guard binary, schema bytes) before that chdir — after it, a relative path would
# resolve inside the target repository instead of the caller's cwd.
# Provider output is stricter than the backward-compatible persisted artifact schema:
# all convergence fields are required before a cross-family launch can be charged.
SCHEMA_JSON="$(cat "$PROVIDER_SCHEMA_FILE")"
case "$CROSS_CLI_GUARD" in
    /*) ;;
    *) CROSS_CLI_GUARD="$(cd "$(dirname "$CROSS_CLI_GUARD")" && pwd)/$(basename "$CROSS_CLI_GUARD")" ;;
esac
set +e
if [ "$REVIEWER" = "claude" ]; then
    (
        eval "exec ${MAGI_LOCK_FD}>&-"
        cd "$TARGET_ROOT" || exit 2
        exec "$CROSS_CLI_GUARD" --isolate-tmux -- \
            timeout --signal=TERM --kill-after=2s "$TIMEOUT_S" claude -p \
            --output-format json \
            --json-schema "$SCHEMA_JSON" \
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
        exec "$CROSS_CLI_GUARD" --isolate-tmux -- \
            timeout --signal=TERM --kill-after=2s "$TIMEOUT_S" grok \
            --prompt-file "$PROMPT_FILE" \
            --cwd "$TARGET_ROOT" \
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
            --json-schema "$SCHEMA_JSON"
    ) > "$RAW_FILE" 2>"${RAW_FILE}.err" &
fi
PROVIDER_PID=$!
wait "$PROVIDER_PID"
rc=$?
PROVIDER_PID=""
set -e

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
[ $rc -eq 0 ] || _fail_closed "$REVIEWER exited $rc (timeout=${TIMEOUT_S}s)" "$rc"

if ! python3 - "$RAW_FILE" "$STAGING_FINDINGS" "$STAGING_META" "$ARTIFACT_SHA" \
        "$STARTED_AT" "$FINISHED_AT" "$SCRUB" "$REVIEWER" "$MODEL" "$PROTOCOL_SHA" <<'PY'
import glob, hashlib, json, os, re, stat, subprocess, sys

(raw_path, findings_out, meta_out, artifact_sha, started, finished,
 scrub, reviewer, requested_model, protocol_sha) = sys.argv[1:11]
raw_info = os.stat(raw_path, follow_symlinks=False)
if not stat.S_ISREG(raw_info.st_mode) or raw_info.st_size > 10 * 1024 * 1024:
    raise SystemExit("provider envelope is not a bounded regular file")
env = json.load(open(raw_path))

MAX_TRANSCRIPT_LINE_BYTES = 1024 * 1024

def bounded_jsonl(path, label, digest):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SystemExit(f"{label} transcript open failed: {type(exc).__name__}") from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise SystemExit(f"{label} transcript is not a regular file")
    if info.st_size > 256 * 1024 * 1024:
        os.close(fd)
        raise SystemExit(f"{label} transcript exceeds the size limit")
    with os.fdopen(fd, "rb") as fh:
        line_number = 0
        while True:
            raw = fh.readline(MAX_TRANSCRIPT_LINE_BYTES + 1)
            if not raw:
                return
            digest.update(raw)
            line_number += 1
            if len(raw) > MAX_TRANSCRIPT_LINE_BYTES:
                raise SystemExit(
                    f"{label} transcript line exceeds the size limit at {line_number}"
                )
            try:
                yield line_number, raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SystemExit(
                    f"{label} transcript is not UTF-8 at line {line_number}"
                ) from exc

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
    transcript_digest = hashlib.sha256()
    if len(transcript_matches) == 1:
        for line_number, line in bounded_jsonl(
            transcript_matches[0], "Claude", transcript_digest
        ):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"malformed transcript JSON at {transcript_matches[0]}:"
                    f"{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(rec, dict):
                raise SystemExit(
                    f"transcript record must be an object at "
                    f"{transcript_matches[0]}:{line_number}"
                )
            message = rec.get("message") or {}
            if not isinstance(message, dict):
                raise SystemExit(
                    f"Claude transcript message must be an object at "
                    f"{transcript_matches[0]}:{line_number}"
                )
            m = message.get("model")
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
    transcript_digest = hashlib.sha256()
    if transcript_matches:
        for line_number, line in bounded_jsonl(
            transcript_matches[0], "Grok", transcript_digest
        ):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"malformed transcript JSON at {transcript_matches[0]}:"
                    f"{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(rec, dict):
                raise SystemExit(
                    f"transcript record must be an object at "
                    f"{transcript_matches[0]}:{line_number}"
                )
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

def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

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
    "round": obj.get("round"),
    "artifact_sha": artifact_sha,
    "protocol_sha": protocol_sha,
    "started_at": started,
    "finished_at": finished,
    "transcript_path": transcript_path,
    "transcript_sha": transcript_digest.hexdigest(),
}
meta = scrubbed(meta)
meta["output_sha"] = file_sha256(findings_out)
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
if ! python3 "$VERIFY_XFAMILY" "$DOC_PATH" "$ROUND" \
        "$STAGING_FINDINGS" "$STAGING_META" --reviewer "$REVIEWER"; then
    _fail_closed "cross-family findings/meta provenance validation failed"
fi

# Publish the complete pair while the claim is still non-authoritative. The ledger transition is
# the commit point; cleanup removes both names if promotion or finalization loses a race.
PUBLISHED=("$FINDINGS_OUT" "$META_OUT")
mv "$STAGING_FINDINGS" "$FINDINGS_OUT"
STAGING_FINDINGS=""
mv "$STAGING_META" "$META_OUT"
STAGING_META=""
if ! python3 "$GUARD" finish "$DOC_PATH" "$CLAIM_ID" success >/dev/null; then
    _fail_closed "claim no longer permits successful output promotion"
fi
CLAIM_FINISHED=1
PUBLISHED=()

# Capture only the validated, authoritative finding artifact. Capture remains best-effort.
python3 "$DEJA" capture \
    --target "$DOC_PATH" --magi-state "$STATE_DIR" \
    --phase xfamily --round "$ROUND" --source "$FINDINGS_OUT" >/dev/null || {
    echo "magi-xfamily[$REVIEWER]: warning: Deja capture diagnostics could not be published" >&2
}

rm -f "$SNAPSHOT_DOC"
[ "$SNAPSHOT_PRIOR" = "-" ] || rm -f "$SNAPSHOT_PRIOR"
SNAPSHOT_DOC=""
SNAPSHOT_PRIOR=""
rm -f "$DEJA_BLOCK"
DEJA_BLOCK=""
rm -rf "$SNAPSHOT_ROOT"
SNAPSHOT_ROOT=""
rm -f "$FAILED_OUT"
echo "magi-xfamily[$REVIEWER]: wrote $FINDINGS_OUT + $META_OUT"
