#!/usr/bin/env bash
# magi_fanout_codex.sh — same-family fan-out: N persona reviewers as parallel `codex exec`.
#
# Design: docs/designs/CODEX_MAGI_MIRROR.md §3.2 (INV-3).
#
# This script is the SOLE author of reviewer prompts. The SKILL.md tells the model to run
# this script and nothing else. If the orchestrating model composed prompts ad hoc it could
# run MELCHIOR, read its output, and leak it into BALTHASAR's prompt -- independence would
# degrade silently to sequential contamination with nothing noticing.
#
# All processes start before any output is read. Separate OS processes, not in-session
# role-play: context isolation is structural, not prompted.
#
# Persona templates are NOT copied here. They are read from the canonical harness-magi
# plugin. (The harness-kimi copies have already drifted from their originals -- measured.)
#
# Exit codes:
#   0  all reviewers produced schema-valid output
#   1  a reviewer failed or produced nothing
#   4  autonomous campaign round budget exhausted
#   5  a same-round sibling output already exists (re-run would contaminate)
#  64  usage
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SELF_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
SCHEMA_FILE="$PLUGIN_DIR/schemas/finding.schema.json"
PROVIDER_SCHEMA_FILE="$PLUGIN_DIR/schemas/finding.codex.schema.json"
SCHEMA_PREFLIGHT="$SELF_DIR/magi_codex_schema_preflight.py"
SCRUB="$SELF_DIR/magi_scrub.py"
GUARD="$SELF_DIR/magi_campaign_guard.py"
VALIDATOR="$SELF_DIR/magi_validate_findings.py"
CLASSIFIER="$SELF_DIR/magi_classify_failure.py"
CONVERGENCE_GATE="$SELF_DIR/magi_convergence_gate.py"
VERIFY_CANON="$SELF_DIR/magi_verify_canonical_templates.py"
PROTOCOL="$SELF_DIR/magi_protocol.py"
DEJA="$SELF_DIR/magi_deja_context.py"
CANON="${MAGI_CANONICAL_SKILLS_DIR:-$REPO_ROOT/plugins/harness-magi/skills}"
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

# shellcheck source=magi_target_root.sh
source "$SELF_DIR/magi_target_root.sh"

usage() {
    echo "usage: $0 <doc-path> <round> <out-dir> [--persona-set magi|bug-hunt] [--prior <json|->] [--review-mode full|incremental]" >&2
    exit 64
}
[ $# -ge 3 ] || usage

DOC_PATH="$1"; ROUND="$2"; OUT_DIR="$3"; shift 3
PERSONA_SET="magi"
PRIOR="-"
REVIEW_MODE="full"
PRIOR_BLOCKING_ROOTS="[]"
while [ $# -gt 0 ]; do
    case "$1" in
        --persona-set) [ $# -ge 2 ] || usage; PERSONA_SET="$2"; shift 2 ;;
        --prior) [ $# -ge 2 ] || usage; PRIOR="$2"; shift 2 ;;
        --review-mode) [ $# -ge 2 ] || usage; REVIEW_MODE="$2"; shift 2 ;;
        *) usage ;;
    esac
done

case "$PERSONA_SET" in
    magi)     PERSONAS=(melchior balthasar caspar) ;;
    bug-hunt) PERSONAS=(hornet gnat wasp) ;;
    *) echo "fanout: unknown persona set: $PERSONA_SET" >&2; exit 64 ;;
esac

case "$REVIEW_MODE" in
    full) PHASE="fanout"; OUTPUT_LABEL="" ;;
    incremental)
        [ "$PERSONA_SET" = "bug-hunt" ] || {
            echo "fanout: incremental review requires --persona-set bug-hunt" >&2; exit 64; }
        PHASE="targeted"
        OUTPUT_LABEL="targeted"
        ;;
    *) echo "fanout: unknown review mode: $REVIEW_MODE" >&2; exit 64 ;;
esac

TEMPLATE_DIR="$CANON/$PERSONA_SET/templates"
if [ -d "$TEMPLATE_DIR" ]; then
    python3 "$VERIFY_CANON" "$CANON" "$PERSONA_SET" >/dev/null || {
        echo "fanout: canonical template identity check failed: $CANON ($PERSONA_SET)" >&2
        exit 64
    }
elif [ -n "${MAGI_CANONICAL_SKILLS_DIR:-}" ]; then
    echo "fanout: canonical templates not found: $TEMPLATE_DIR" >&2
    exit 64
fi
[ -f "$DOC_PATH" ] || { echo "fanout: doc not found: $DOC_PATH" >&2; exit 64; }
DOC_PATH="$(realpath "$DOC_PATH")"
MAX_ARTIFACT_BYTES="${MAGI_MAX_ARTIFACT_BYTES:-10485760}"
case "$MAX_ARTIFACT_BYTES" in
    ''|*[!0-9]*) echo "fanout: MAGI_MAX_ARTIFACT_BYTES must be an integer" >&2; exit 64 ;;
esac
[ "$MAX_ARTIFACT_BYTES" -ge 1 ] && [ "$MAX_ARTIFACT_BYTES" -le 10485760 ] || {
    echo "fanout: MAGI_MAX_ARTIFACT_BYTES must tighten the default into 1..10485760" >&2
    exit 64
}
ARTIFACT_BYTES="$(stat -c %s -- "$DOC_PATH")" || {
    echo "fanout: cannot stat review artifact: $DOC_PATH" >&2
    exit 64
}
[ "$ARTIFACT_BYTES" -ge 1 ] || {
    echo "fanout: review artifact must not be empty" >&2
    exit 64
}
[ "$ARTIFACT_BYTES" -le "$MAX_ARTIFACT_BYTES" ] || {
    echo "fanout: review artifact exceeds ${MAX_ARTIFACT_BYTES}-byte limit" >&2
    exit 64
}
case "$ROUND" in ''|*[!0-9]*) echo "fanout: round must be a positive integer: $ROUND" >&2; exit 64 ;; esac
[ "$ROUND" -ge 1 ] || { echo "fanout: round must be at least 1" >&2; exit 64; }
if [ "$ROUND" -gt 1 ] && [ "$PRIOR" = "-" ]; then
    echo "fanout: round $ROUND requires --prior <prior-synthesis.json>" >&2
    exit 64
fi
if [ "$PRIOR" != "-" ] && [ ! -f "$PRIOR" ]; then
    echo "fanout: prior findings not found: $PRIOR" >&2
    exit 64
fi
# gh #57: canonicalize every caller-supplied path before anything is spawned. Reviewers
# launch with the TARGET repository as their cwd (below), not the caller's cwd, so a
# relative DOC_PATH/OUT_DIR/PRIOR would otherwise resolve against the wrong repository.
DOC_PATH="$(realpath "$DOC_PATH")"
mkdir -p "$OUT_DIR"
OUT_DIR="$(realpath "$OUT_DIR")"
if [ "$PRIOR" != "-" ]; then
    PRIOR="$(realpath "$PRIOR")"
fi
# Reviewer verification commands must run in the repository/worktree that owns the
# document, not in the harness checkout: a relative doc path in repo B launched from
# repo A must still ground reviewers in repo B (gh #57). magi_target_root.sh owns the
# derivation (git top-level, GIT_DIR/GIT_WORK_TREE stripped, fail-closed on anything
# other than "not a git repository", document directory as the documented fallback);
# the cross-family and pre-flight arms share it so grounding cannot drift (gh #151).
TARGET_ROOT="$(magi_target_root "$DOC_PATH" fanout)" || exit 64
# The target-root lookup above deliberately ignores ambient repository overrides.
# Keep them from contaminating protocol snapshot git reads and reviewer subprocesses too.
unset GIT_DIR GIT_WORK_TREE
if [ "$PRIOR" != "-" ]; then
    python3 "$VALIDATOR" "$PRIOR" "$SCHEMA_FILE" --same-doc "$DOC_PATH" \
        --prior-for-round "$ROUND" --state-dir "$OUT_DIR" || {
        echo "fanout: prior synthesis failed identity/round/schema validation" >&2
        exit 64
    }
    PRIOR_SHA="$(sha256sum "$PRIOR" | cut -d' ' -f1)"
fi

command -v codex >/dev/null 2>&1 || { echo "fanout: codex CLI not found" >&2; exit 1; }
command -v timeout >/dev/null 2>&1 || { echo "fanout: timeout utility not found" >&2; exit 1; }
[ -x "$CROSS_CLI_GUARD" ] || {
    echo "fanout: harness-cross-cli is required for provider identity isolation" >&2
    exit 1
}
# Provider schema refusal is deterministic and must not consume campaign budget.  Keep this
# before the claim boundary; the richer SSOT schema is still used for every local validation.
python3 "$SCHEMA_PREFLIGHT" "$PROVIDER_SCHEMA_FILE" || exit $?
FANOUT_TIMEOUT_S="${MAGI_FANOUT_TIMEOUT_S:-900}"
case "$FANOUT_TIMEOUT_S" in
    ''|*[!0-9]*) echo "fanout: MAGI_FANOUT_TIMEOUT_S must be an integer" >&2; exit 64 ;;
esac
[ "$FANOUT_TIMEOUT_S" -ge 1 ] && [ "$FANOUT_TIMEOUT_S" -le 900 ] || {
    echo "fanout: MAGI_FANOUT_TIMEOUT_S must tighten the default into 1..900" >&2; exit 64; }

ARTIFACT_SHA="$(sha256sum "$DOC_PATH" | cut -d' ' -f1)"
ARTIFACT_ID="$(printf '%s' "$(realpath "$DOC_PATH")" | sha256sum | cut -c1-16)"
DOC_CONTROL_DIR="$(dirname "$(realpath "$DOC_PATH")")/.dual-magi"
mkdir -p "$DOC_CONTROL_DIR"
REVIEW_WORKSPACE="$TARGET_ROOT"

if [ "$REVIEW_MODE" = "incremental" ]; then
    decision_json="$(python3 "$CONVERGENCE_GATE" evaluate "$DOC_PATH")" || exit $?
    decision_fields="$(
        printf '%s' "$decision_json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if d.get("decision") != "CONTINUE" or d.get("next_mode") != "incremental-fix":
    raise SystemExit(2)
p = d.get("next_persona")
if p not in {"hornet", "gnat", "wasp"}:
    raise SystemExit(2)
print(p)
print(json.dumps(d.get("prior_blocking_roots") or [], separators=(",", ":")))
'
    )" || {
        echo "fanout: convergence evaluator did not authorize incremental review" >&2
        exit 64
    }
    TARGETED_PERSONA="${decision_fields%%$'\n'*}"
    PRIOR_BLOCKING_ROOTS="${decision_fields#*$'\n'}"
    [ "$TARGETED_PERSONA" != "$PRIOR_BLOCKING_ROOTS" ] || {
        echo "fanout: malformed incremental evaluator decision" >&2
        exit 64
    }
    PERSONAS=("$TARGETED_PERSONA")
fi

# Reject a broken launcher/cache generation and CLI releases missing the live interface after
# semantic authorization but before charging the campaign or starting a provider. This is a local
# help probe, not a model launch.
cli_help=""; cli_help_rc=0
cli_help="$(timeout 10 codex exec --help 2>&1)" || cli_help_rc=$?
if [ "$cli_help_rc" -ne 0 ] \
    || ! grep -q -- '--output-schema' <<<"$cli_help" \
    || ! grep -q -- '--output-last-message' <<<"$cli_help" \
    || ! grep -q -- '--ephemeral' <<<"$cli_help"
then
    preflight_tmp="$(mktemp "$OUT_DIR/.round_${ROUND}_fanout.PREFLIGHT_FAILED.XXXXXX")"
    if ! python3 - "$preflight_tmp" "$ROUND" "$ARTIFACT_ID" "$ARTIFACT_SHA" "$cli_help_rc" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps({
    "status": "failed",
    "classification": "cli-interface-preflight",
    "round": int(sys.argv[2]),
    "artifact_id": sys.argv[3],
    "artifact_sha": sys.argv[4],
    "cli_exit_code": int(sys.argv[5]),
}, separators=(",", ":"), sort_keys=True) + "\n")
PY
    then
        rm -f -- "$preflight_tmp"
        echo "fanout: could not write bounded CLI preflight diagnostics" >&2
        exit 1
    fi
    if ! mv -- "$preflight_tmp" "$OUT_DIR/round_${ROUND}_fanout.PREFLIGHT_FAILED.json"; then
        rm -f -- "$preflight_tmp"
        echo "fanout: could not publish bounded CLI preflight diagnostics" >&2
        exit 1
    fi
    echo "fanout: Codex CLI interface preflight failed before campaign claim" >&2
    exit 1
fi
unset cli_help
rm -f -- "$OUT_DIR/round_${ROUND}_fanout.PREFLIGHT_FAILED.json"

artifact_label() {
    if [ -n "$OUTPUT_LABEL" ]; then printf '%s' "$OUTPUT_LABEL"; else printf '%s' "$1"; fi
}

# Prompts hold the FULL document. Track them so no copy is left in TMPDIR on any exit path.
PROMPTS=()
PIDS=()
PUBLISHED=()
CLAIM_ID=""
CLAIM_FINISHED=0
STAGE_DIR=""
_cleanup_stage() {
    local p label failed=0
    set +e
    [ -n "$STAGE_DIR" ] || return 0
    [ -d "$STAGE_DIR" ] || return 0
    for p in "${PERSONAS[@]}"; do
        label="$(artifact_label "$p")"
        rm -f -- \
            "$STAGE_DIR/round_${ROUND}_${label}.json" \
            "$STAGE_DIR/round_${ROUND}_${label}.log" \
            "$STAGE_DIR/.round_${ROUND}_${p}.raw.fifo" \
            "$STAGE_DIR/.round_${ROUND}_${p}.log.fifo" \
            "$STAGE_DIR"/.round_"${ROUND}_${p}".safe.* \
            "$STAGE_DIR"/.round_"${ROUND}_${p}".log.safe.* \
            "$STAGE_DIR/.round_${ROUND}_${p}.scrub-meta.json" \
            "$STAGE_DIR/.round_${ROUND}_${p}.status" \
            "$STAGE_DIR/.round_${ROUND}_${p}.diagnostic.json" || failed=1
    done
    if [ "$REVIEW_MODE" = "incremental" ] \
            && ! rm -f -- "$STAGE_DIR/round_${ROUND}_codex.json"; then failed=1; fi
    rm -rf -- "$STAGE_DIR/protocol" || failed=1
    rm -f -- "$STAGE_DIR/review-artifact" "$STAGE_DIR/prior.json" \
        "$STAGE_DIR/deja-block" || failed=1
    rmdir -- "$STAGE_DIR" || failed=1
    return "$failed"
}
_cleanup() {
    local original_rc=$? pid status status_rc cleanup_failed=0
    set +e
    for pid in "${PIDS[@]:-}"; do kill -TERM "$pid" 2>/dev/null || true; done
    for pid in "${PIDS[@]:-}"; do wait "$pid" 2>/dev/null || true; done
    if [ ${#PROMPTS[@]} -gt 0 ] && ! rm -f "${PROMPTS[@]}"; then cleanup_failed=1; fi
    if [ -n "$CLAIM_ID" ] && [ "$CLAIM_FINISHED" -eq 0 ]; then
        status="$(python3 "$GUARD" claim-status "$DOC_PATH" "$CLAIM_ID")"
        status_rc=$?
        if [ "$status" = "success" ]; then
            CLAIM_FINISHED=1
        else
            [ "$status_rc" -eq 0 ] || cleanup_failed=1
            if [ ${#PUBLISHED[@]} -gt 0 ] && ! rm -f -- "${PUBLISHED[@]}"; then
                cleanup_failed=1
            fi
            if ! python3 "$GUARD" finish "$DOC_PATH" "$CLAIM_ID" failed >/dev/null; then
                echo "fanout: ERROR: failed to finalize claim $CLAIM_ID as failed" >&2
                cleanup_failed=1
            fi
        fi
    fi
    _cleanup_stage || cleanup_failed=1
    [ "$cleanup_failed" -eq 0 ] || {
        echo "fanout: ERROR: cleanup was incomplete for claim ${CLAIM_ID:-unclaimed}" >&2
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

# INV-3: the sibling check below is check-then-spawn, and a reviewer's output file does not
# appear until codex finishes minutes later. Without a lock, two concurrent same-round runs
# both pass the check and clobber each other's outputs -- in the one script whose entire
# purpose is contamination control. Take the lock first.
# shellcheck source=magi_lock.sh
source "$SELF_DIR/magi_lock.sh"
lock_rc=0
magi_lock_acquire "$DOC_CONTROL_DIR/.review.${ARTIFACT_ID}.lock" || lock_rc=$?
case "$lock_rc" in
    0) ;;
    1) echo "fanout: another fan-out is already running for round $ROUND in $OUT_DIR" >&2; exit 5 ;;
    *) echo "fanout: cannot acquire document lock (I/O error) in $DOC_CONTROL_DIR" >&2; exit 2 ;;
esac

# INV-3: refuse to start if a sibling output for this round already exists.
for p in "${PERSONAS[@]}"; do
    label="$(artifact_label "$p")"
    if [ -e "$OUT_DIR/round_${ROUND}_${label}.json" ]; then
        echo "fanout: sibling output already exists for round $ROUND ($p). Refusing: a re-run" >&2
        echo "        would let an existing reviewer's output contaminate its siblings." >&2
        exit 5
    fi
done

# Validate every template before spawning anything: a mid-loop exit 64 would otherwise leave
# already-spawned codex processes orphaned, whose late writes then trip exit 5 on the next run.
for p in "${PERSONAS[@]}"; do
    [ -f "$TEMPLATE_DIR/${p}_prompt.md" ] || {
        echo "fanout: template missing: $TEMPLATE_DIR/${p}_prompt.md" >&2; exit 64; }
done

# Claim only after validation, capability checks, and the execution lock, but before any provider
# process starts. A crash after this boundary is conservatively charged; preflight refusal is not.
claim_line="$(
    python3 "$GUARD" claim "$DOC_PATH" "$ROUND" "$PHASE" "$OUT_DIR" \
        --owner-pid "$$" --adapter-kind "$PHASE" \
        --expected-artifact-sha "$ARTIFACT_SHA"
)" || exit $?
echo "$claim_line"
CLAIM_ID="${claim_line##*CLAIM_ID=}"
[[ "$CLAIM_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || {
    echo "fanout: campaign guard returned an invalid claim id" >&2
    exit 1
}
CLAIM_PROTOCOL_SHA="${claim_line#*PROTOCOL_SHA=}"
CLAIM_PROTOCOL_SHA="${CLAIM_PROTOCOL_SHA%%;*}"
[[ "$CLAIM_PROTOCOL_SHA" =~ ^[0-9a-f]{64}$ ]] || {
    echo "fanout: campaign guard returned an invalid protocol digest" >&2
    exit 1
}
STAGE_DIR="$OUT_DIR/.claim-$CLAIM_ID"
mkdir -m 700 "$STAGE_DIR"

# Freeze every closed protocol input plus the exact document/prior bytes before composing any
# provider prompt. Endpoint digest checks alone permit an ABA checkout mutation.
python3 "$PROTOCOL" snapshot "$STAGE_DIR/protocol" "$CLAIM_PROTOCOL_SHA" >/dev/null || {
    echo "fanout: protocol inputs changed while creating the claim snapshot" >&2
    exit 1
}
SNAPSHOT_PLUGIN="$STAGE_DIR/protocol/plugins/harness-magi-codex"
SNAPSHOT_REPO="$STAGE_DIR/protocol"
TEMPLATE_DIR="$SNAPSHOT_REPO/plugins/harness-magi/skills/$PERSONA_SET/templates"
python3 "$VERIFY_CANON" "$SNAPSHOT_REPO/plugins/harness-magi/skills" \
    "$PERSONA_SET" >/dev/null || {
    echo "fanout: snapshotted canonical template identity check failed" >&2
    exit 1
}
SCHEMA_FILE="$SNAPSHOT_PLUGIN/schemas/finding.schema.json"
PROVIDER_SCHEMA_FILE="$SNAPSHOT_PLUGIN/schemas/finding.codex.schema.json"
SCRUB="$SNAPSHOT_PLUGIN/scripts/magi_scrub.py"
GUARD="$SNAPSHOT_PLUGIN/scripts/magi_campaign_guard.py"
VALIDATOR="$SNAPSHOT_PLUGIN/scripts/magi_validate_findings.py"
CLASSIFIER="$SNAPSHOT_PLUGIN/scripts/magi_classify_failure.py"
DEJA="$SNAPSHOT_PLUGIN/scripts/magi_deja_context.py"
python3 - "$DOC_PATH" "$STAGE_DIR/review-artifact" "$ARTIFACT_SHA" <<'PY'
import hashlib, pathlib, sys
source, target = map(pathlib.Path, sys.argv[1:3])
expected = sys.argv[3]
digest = hashlib.sha256()
with source.open("rb") as reader, target.open("wb") as writer:
    for chunk in iter(lambda: reader.read(1024 * 1024), b""):
        digest.update(chunk)
        writer.write(chunk)
if digest.hexdigest() != expected:
    raise SystemExit("review artifact changed after campaign admission")
PY
SNAPSHOT_DOC="$STAGE_DIR/review-artifact"
SNAPSHOT_PRIOR="-"
if [ "$PRIOR" != "-" ]; then
    cp -- "$PRIOR" "$STAGE_DIR/prior.json"
    SNAPSHOT_PRIOR="$STAGE_DIR/prior.json"
    [ "$(sha256sum "$SNAPSHOT_PRIOR" | cut -d' ' -f1)" = "$PRIOR_SHA" ] || {
        echo "fanout: prior synthesis changed after preflight validation" >&2
        exit 1
    }
fi

# Round-1 fanout freezes one immutable historical selection for the whole campaign.
# Later arms may only validate and render that exact selection.
if [ "$ROUND" -eq 1 ] && [ "$PHASE" = "fanout" ]; then
    python3 "$DEJA" select \
        --target "$DOC_PATH" --magi-state "$OUT_DIR" \
        --target-path-id "$ARTIFACT_ID" --target-sha "$ARTIFACT_SHA" \
        --protocol-sha "$CLAIM_PROTOCOL_SHA" >/dev/null || {
        echo "fanout: Deja selection publication failed" >&2
        exit 1
    }
fi
DEJA_BLOCK="$STAGE_DIR/deja-block"
python3 "$DEJA" render \
    --target "$DOC_PATH" --magi-state "$OUT_DIR" \
    --target-path-id "$ARTIFACT_ID" --target-sha "$ARTIFACT_SHA" \
    --protocol-sha "$CLAIM_PROTOCOL_SHA" --output "$DEJA_BLOCK" || {
    echo "fanout: frozen Deja context validation/render failed" >&2
    exit 1
}

declare -A PERSONA_PROMPTS
for p in "${PERSONAS[@]}"; do
    tmpl="$TEMPLATE_DIR/${p}_prompt.md"
    prompt="$(mktemp)"
    PROMPTS+=("$prompt")
    PERSONA_PROMPTS["$p"]="$prompt"
    {
        printf 'You are the %s reviewer in a Magi review. Stay strictly in your lane;\n' "${p^^}"
        printf 'do not cover the other reviewers'"'"' perspectives. You cannot see their output.\n\n'
        printf 'PERSONA BRIEF:\n---\n'
        cat "$tmpl"
        printf '\n---\n\nSCHEMA GROUNDING (mandatory): verify every load-bearing claim by RUNNING a\n'
        printf 'command (rg / grep / reading real files). Report each verbatim in\n'
        printf 'verify_commands_executed. Doc-vs-reality drift is a CRITICAL finding. If you ran\n'
        printf 'no verification commands you MUST self-report schema_grounding_verdict "FAIL".\n'
        printf 'Your working directory is TARGET REPO ROOT (below); run verification there.\n'
        printf 'Read-only. Never read, print, or decrypt a credential file, *.enc.yaml, or auth.json.\n\n'
        printf 'FAMILY ROUTING REVIEW (mandatory for design docs that lead to implementation):\n'
        printf 'Preferred route is Claude design/planning plateau -> Codex implementation ->\n'
        printf 'Claude adversarial design-intent review -> Codex final fixes/tests. If the\n'
        printf 'document proposes a different route, verify that it is justified. If Claude or\n'
        printf 'Codex is unavailable due to missing CLI, no contract/subscription, capacity, or\n'
        printf 'rate limit, fallback is allowed only if documented as FAMILY_ROUTING with:\n'
        printf 'preferred, actual, missing family/phase/reason, and degraded_until. Do not\n'
        printf 'accept a plateau or irreversible implementation path that silently skips the\n'
        printf 'missing family.\n\n'
        printf 'CONVERGENCE CONTRACT (mandatory): dup_flag must be exactly one of new,\n'
        printf 'duplicate, regression, readiness-gap, or scope-expansion. After round 2, freeze\n'
        printf 'the committed scope. Every REJECT, CRITICAL, or HIGH finding MUST include a\n'
        printf 'stable non-empty root_cause_id and subsystem so repeated roots and subsystem\n'
        printf 'recurrence can be evaluated mechanically. Reuse a prior root_cause_id when the\n'
        printf 'underlying cause is unchanged; do not mint a new id for a rewording.\n'
        printf 'Prioritize unresolved prior blockers, regressions caused by\n'
        printf 'their fixes, and newly discovered unsafe or unimplementable behavior inside that\n'
        printf 'scope. Missing evidence explicitly scheduled for a later phase is readiness-gap.\n'
        printf 'An optional stronger guarantee or new subsystem is scope-expansion. Neither may\n'
        printf 'be REJECT, CRITICAL, or HIGH. If committed behavior itself is unsafe, classify it\n'
        printf 'new or regression instead. Readiness-gap/scope-expansion alone require\n'
        printf 'GO-WITH-REVISE, not REVISE or REJECT. Do not perpetuate review by demanding\n'
        printf 'optional scope.\n\n'
        if [ "$REVIEW_MODE" = "incremental" ]; then
            printf 'INCREMENTAL FIX REVIEW (mandatory boundary): the target is a trusted exact-SHA\n'
            printf 'implementation manifest whose review_packet contains only the previous-target\n'
            printf 'to current-target diff. Review closure of prior blockers named by the guarded\n'
            printf 'history, the affected invariants, and regressions induced by this diff. Do not\n'
            printf 'promote an unrelated unchanged-area observation to a blocker. If the declared\n'
            printf 'surface or risk requires broader review, report that escalation instead.\n\n'
            printf 'PRIOR BLOCKING ROOT IDS: %s\n\n' "$PRIOR_BLOCKING_ROOTS"
        fi
        printf 'ROUND: %s\nTARGET DOC: %s\nTARGET REPO ROOT: %s\nARTIFACT ID: %s\nARTIFACT SHA256: %s\n\n' \
            "$ROUND" "$DOC_PATH" "$TARGET_ROOT" "$ARTIFACT_ID" "$ARTIFACT_SHA"
        if [ "$SNAPSHOT_PRIOR" != "-" ]; then
            printf 'PRIOR SYNTHESIS (check resolution and classify relationships; do not repeat):\n---\n'
            (
                eval "exec ${MAGI_LOCK_FD}>&-"
                exec python3 "$SCRUB" < "$SNAPSHOT_PRIOR"
            )
            printf '\n---\n\n'
        fi
        if [ -s "$DEJA_BLOCK" ]; then
            cat "$DEJA_BLOCK"
            printf '\n'
        fi
        printf 'DOCUMENT:\n---\n'
        cat "$SNAPSHOT_DOC"
        printf '\n---\n\nReturn ONLY a JSON object conforming to the output schema. reviewer="%s", round=%s, artifact_id="%s", artifact_sha="%s".\n' \
            "${p^^}" "$ROUND" "$ARTIFACT_ID" "$ARTIFACT_SHA"
    } > "$prompt"

done

# Prove every complete provider prompt consumed byte-identical historical evidence before
# launching any provider. An absent selection leaves prompt bytes unchanged.
deja_consume_args=(
    consume --target "$DOC_PATH" --magi-state "$OUT_DIR"
    --target-path-id "$ARTIFACT_ID" --target-sha "$ARTIFACT_SHA"
    --protocol-sha "$CLAIM_PROTOCOL_SHA" --phase fanout --round "$ROUND"
    --block "$DEJA_BLOCK"
)
for p in "${PERSONAS[@]}"; do
    deja_consume_args+=(
        --provider "codex:${p^^}" --prompt "${PERSONA_PROMPTS[$p]}"
    )
done
python3 "$DEJA" "${deja_consume_args[@]}" || {
    echo "fanout: Deja prompt-consumption receipt publication failed" >&2
    exit 1
}

for p in "${PERSONAS[@]}"; do
    prompt="${PERSONA_PROMPTS[$p]}"
    # All three launch before any output is read (INV-3).
    # `|| s=$?` so a codex failure does not abort the subshell under the inherited `set -e`
    # (which would skip the rm), while still propagating the real status to `wait`.
    ( s=0; scrub_rc=0; codex_pid=""; raw_scrub_pid=""; log_scrub_pid=""
      # The parent alone owns the document lock. Provider/scrubber descendants must not keep it
      # alive if the parent is killed.
      eval "exec ${MAGI_LOCK_FD}>&-"
      child_cleanup() {
          [ -n "$codex_pid" ] && kill -TERM "$codex_pid" 2>/dev/null || true
          [ -n "$raw_scrub_pid" ] && kill -TERM "$raw_scrub_pid" 2>/dev/null || true
          [ -n "$log_scrub_pid" ] && kill -TERM "$log_scrub_pid" 2>/dev/null || true
          [ -n "$codex_pid" ] && wait "$codex_pid" 2>/dev/null || true
          [ -n "$raw_scrub_pid" ] && wait "$raw_scrub_pid" 2>/dev/null || true
          [ -n "$log_scrub_pid" ] && wait "$log_scrub_pid" 2>/dev/null || true
      }
      trap child_cleanup INT TERM EXIT
      raw_fifo="$STAGE_DIR/.round_${ROUND}_${p}.raw.fifo"
      log_fifo="$STAGE_DIR/.round_${ROUND}_${p}.log.fifo"
      scrub_meta="$STAGE_DIR/.round_${ROUND}_${p}.scrub-meta.json"
      status_file="$STAGE_DIR/.round_${ROUND}_${p}.status"
      safe_out="$(mktemp "$STAGE_DIR/.round_${ROUND}_${p}.safe.XXXXXX")"
      safe_log="$(mktemp "$STAGE_DIR/.round_${ROUND}_${p}.log.safe.XXXXXX")"
      rm -f "$raw_fifo" "$log_fifo"
      mkfifo "$raw_fifo" "$log_fifo"
      # Keep a writer open until codex returns so either scrubber receives EOF even when codex
      # fails before opening its sink. Bytes travel through FIFOs; only scrubbed bytes hit disk.
      exec 7<>"$raw_fifo" 8<>"$log_fifo"
      (
          exec 7>&- 8>&-
          eval "exec ${MAGI_LOCK_FD}>&-"
          exec python3 "$SCRUB" --meta "$scrub_meta" < "$raw_fifo" > "$safe_out"
      ) & raw_scrub_pid=$!
      (
          exec 7>&- 8>&-
          eval "exec ${MAGI_LOCK_FD}>&-"
          exec python3 "$SCRUB" --text < "$log_fifo" > "$safe_log"
      ) & log_scrub_pid=$!
      "$CROSS_CLI_GUARD" --isolate-tmux -- \
        timeout --signal=TERM --kill-after=2s "$FANOUT_TIMEOUT_S" \
        codex exec --skip-git-repo-check -s read-only --ephemeral \
        -C "$TARGET_ROOT" \
        --output-schema "$PROVIDER_SCHEMA_FILE" \
        -o "$raw_fifo" \
        - < "$prompt" > "$log_fifo" 2>&1 & codex_pid=$!
      wait "$codex_pid" || s=$?
      codex_pid=""
      exec 7>&- 8>&-
      wait "$raw_scrub_pid" || scrub_rc=1
      wait "$log_scrub_pid" || scrub_rc=1
      rm -f "$prompt" "$raw_fifo" "$log_fifo"
      trap - INT TERM EXIT
      printf '%s %s\n' "$s" "$scrub_rc" > "$status_file"
      if [ "$scrub_rc" -eq 0 ]; then
          label="$(artifact_label "$p")"
          mv "$safe_out" "$STAGE_DIR/round_${ROUND}_${label}.json"
          mv "$safe_log" "$STAGE_DIR/round_${ROUND}_${label}.log"
      else
          rm -f "$safe_out" "$safe_log"
      fi
      if [ "$s" -ne 0 ] || [ "$scrub_rc" -ne 0 ]; then
          s=1
      fi
      exit "$s" ) &
    PIDS+=("$!")
done

rc=0
for pid in "${PIDS[@]}"; do
    wait "$pid" || rc=1
done

for p in "${PERSONAS[@]}"; do
    label="$(artifact_label "$p")"
    out="$STAGE_DIR/round_${ROUND}_${label}.json"
    validator_error="$STAGE_DIR/.round_${ROUND}_${p}.validator.err"
    if [ ! -s "$out" ] || ! python3 "$VALIDATOR" "$out" "$SCHEMA_FILE" \
        --doc "$DOC_PATH" --reviewer "${p^^}" --round "$ROUND" 2>"$validator_error"
    then
        echo "fanout: reviewer $p produced no schema-valid output" >&2
        rc=1
        continue
    fi
    rm -f -- "$validator_error" || {
        echo "fanout: cannot remove successful validator diagnostic: $validator_error" >&2
        rc=1
    }
done

if [ $rc -ne 0 ]; then
    diagnostics=()
    for p in "${PERSONAS[@]}"; do
        label="$(artifact_label "$p")"
        status_file="$STAGE_DIR/.round_${ROUND}_${p}.status"
        provider_rc=1
        scrub_rc=1
        status_valid=0
        if [ -s "$status_file" ]; then
            read -r provider_rc scrub_rc < "$status_file" || true
        fi
        case "$provider_rc:$scrub_rc" in
            *[!0-9:]*|:*|*:) provider_rc=1; scrub_rc=1 ;;
            *) status_valid=1 ;;
        esac
        diagnostic="$STAGE_DIR/.round_${ROUND}_${p}.diagnostic.json"
        if ! python3 "$CLASSIFIER" \
            --output "$STAGE_DIR/round_${ROUND}_${label}.json" \
            --log "$STAGE_DIR/round_${ROUND}_${label}.log" \
            --scrub-meta "$STAGE_DIR/.round_${ROUND}_${p}.scrub-meta.json" \
            --provider-exit "$provider_rc" --scrub-exit "$scrub_rc" \
            --status-valid "$status_valid" --schema "$SCHEMA_FILE" --doc "$DOC_PATH" \
            --validator-error "$STAGE_DIR/.round_${ROUND}_${p}.validator.err" \
            --reviewer "${p^^}" --round "$ROUND" \
            --claim-id "$CLAIM_ID" --artifact-id "$ARTIFACT_ID" \
            --artifact-sha "$ARTIFACT_SHA" \
            > "$diagnostic"
        then
            if ! python3 - "$diagnostic" "$p" "$ROUND" "$provider_rc" "$scrub_rc" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "reviewer": sys.argv[2].upper(),
    "round": int(sys.argv[3]),
    "classification": "diagnostic-generation-failure",
    "provider_exit_code": int(sys.argv[4]),
    "scrubber_exit_code": int(sys.argv[5]),
}, separators=(",", ":"), sort_keys=True) + "\n")
PY
            then
                echo "fanout: could not write bounded reviewer diagnostics" >&2
                exit 1
            fi
        fi
        diagnostics+=("$diagnostic")
    done
    failure_stage="$STAGE_DIR/round_${ROUND}_fanout.${CLAIM_ID}.FAILED.json"
    if ! python3 - "$failure_stage" "$CLAIM_ID" "$ARTIFACT_ID" "$ARTIFACT_SHA" \
        "$ROUND" "${diagnostics[@]}" <<'PY'
import json, pathlib, sys
output = pathlib.Path(sys.argv[1])
claim_id, artifact_id, artifact_sha, round_number = sys.argv[2:6]
allowed = {
    "reviewer", "round", "classification", "provider_exit_code",
    "scrubber_exit_code", "output_bytes", "log_bytes", "input_bytes",
    "input_parsed_json", "redactions", "identity_field", "diagnostic",
    "diagnostic_truncated", "diagnostic_unavailable",
}
reviewers = []
for path in sys.argv[6:]:
    item = json.loads(pathlib.Path(path).read_text())
    reviewers.append({key: item[key] for key in allowed if key in item})
output.write_text(json.dumps({
    "status": "failed",
    "classification": "reviewer-fanout-failure",
    "round": int(round_number),
    "claim_id": claim_id,
    "artifact_id": artifact_id,
    "artifact_sha": artifact_sha,
    "reviewers": reviewers,
}, separators=(",", ":"), sort_keys=True) + "\n")
PY
    then
        rm -f -- "$failure_stage"
        echo "fanout: could not aggregate bounded reviewer diagnostics" >&2
        exit 1
    fi
    if ! mv -- "$failure_stage" "$OUT_DIR/round_${ROUND}_fanout.${CLAIM_ID}.FAILED.json"; then
        rm -f -- "$failure_stage"
        echo "fanout: could not publish bounded reviewer diagnostics" >&2
        exit 1
    fi
    echo "fanout: clearing claim-scoped staging for failed round $ROUND" >&2
    exit $rc
fi

if [ "$REVIEW_MODE" = "incremental" ]; then
    python3 - \
        "$STAGE_DIR/round_${ROUND}_targeted.json" \
        "$STAGE_DIR/round_${ROUND}_codex.json" <<'PY'
import hashlib, json, pathlib, sys

source = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
payload = json.loads(source.read_text())
payload["reviewer"] = "SYNTHESIS"
payload["source_artifacts"] = [
    {"path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
]
payload["dispositions"] = [
    {
        "source_ref": f"{source.name}#{finding['finding_id']}",
        "disposition": "carried",
        "synthesis_finding_id": finding["finding_id"],
    }
    for finding in payload["findings"]
]
output.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
PY
    if ! python3 "$VALIDATOR" "$STAGE_DIR/round_${ROUND}_codex.json" "$SCHEMA_FILE" \
        --same-doc "$DOC_PATH" --prior-for-round "$((ROUND + 1))" --state-dir "$STAGE_DIR"
    then
        echo "fanout: targeted synthesis envelope failed validation" >&2
        exit 1
    fi
fi

# Publish first while the ledger remains non-authoritative. If cancellation wins or any move/finish
# fails, the EXIT trap removes every exact canonical path already moved. The final guard transition
# is the commit point. A signal immediately after that transition re-reads claim-status and keeps
# the now-authoritative complete publication.
for p in "${PERSONAS[@]}"; do
    label="$(artifact_label "$p")"
    published_json="$OUT_DIR/round_${ROUND}_${label}.json"
    published_log="$OUT_DIR/round_${ROUND}_${label}.log"
    PUBLISHED+=("$published_json")
    PUBLISHED+=("$published_log")
    mv -- "$STAGE_DIR/round_${ROUND}_${label}.json" "$published_json"
    mv -- "$STAGE_DIR/round_${ROUND}_${label}.log" "$published_log"
done
if [ "$REVIEW_MODE" = "incremental" ]; then
    published_synthesis="$OUT_DIR/round_${ROUND}_codex.json"
    PUBLISHED+=("$published_synthesis")
    mv -- "$STAGE_DIR/round_${ROUND}_codex.json" "$published_synthesis"
fi
python3 "$GUARD" finish "$DOC_PATH" "$CLAIM_ID" success >/dev/null
CLAIM_FINISHED=1
PUBLISHED=()

# Historical capture is best-effort and happens only after the normal Magi arm is durable.
deja_capture_args=(
    capture --target "$DOC_PATH" --magi-state "$OUT_DIR"
    --phase fanout --round "$ROUND"
)
for p in "${PERSONAS[@]}"; do
    label="$(artifact_label "$p")"
    deja_capture_args+=(--source "$OUT_DIR/round_${ROUND}_${label}.json")
done
python3 "$DEJA" "${deja_capture_args[@]}" >/dev/null || {
    echo "fanout: warning: Deja capture diagnostics could not be published" >&2
}

if ! _cleanup_stage; then
    echo "fanout: ERROR: final staging cleanup failed for claim $CLAIM_ID" >&2
    exit 1
fi
STAGE_DIR=""
echo "fanout: ${#PERSONAS[@]} reviewers complete -> $OUT_DIR"
