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
SCRUB="$SELF_DIR/magi_scrub.py"
GUARD="$SELF_DIR/magi_campaign_guard.py"
VALIDATOR="$SELF_DIR/magi_validate_findings.py"
CONVERGENCE_GATE="$SELF_DIR/magi_convergence_gate.py"
CANON="$REPO_ROOT/plugins/harness-magi/skills"
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
[ -d "$TEMPLATE_DIR" ] || { echo "fanout: canonical templates not found: $TEMPLATE_DIR" >&2; exit 64; }
[ -f "$DOC_PATH" ] || { echo "fanout: doc not found: $DOC_PATH" >&2; exit 64; }
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
mkdir -p "$OUT_DIR"
if [ "$PRIOR" != "-" ]; then
    python3 "$VALIDATOR" "$PRIOR" "$SCHEMA_FILE" --same-doc "$DOC_PATH" \
        --prior-for-round "$ROUND" --state-dir "$OUT_DIR" || {
        echo "fanout: prior synthesis failed identity/round/schema validation" >&2
        exit 64
    }
fi

command -v codex >/dev/null 2>&1 || { echo "fanout: codex CLI not found" >&2; exit 1; }
command -v timeout >/dev/null 2>&1 || { echo "fanout: timeout utility not found" >&2; exit 1; }
[ -x "$CROSS_CLI_GUARD" ] || {
    echo "fanout: harness-cross-cli is required for provider identity isolation" >&2
    exit 1
}
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
    local p label
    [ -n "$STAGE_DIR" ] || return 0
    for p in "${PERSONAS[@]}"; do
        label="$(artifact_label "$p")"
        rm -f -- \
            "$STAGE_DIR/round_${ROUND}_${label}.json" \
            "$STAGE_DIR/round_${ROUND}_${label}.log" \
            "$STAGE_DIR/.round_${ROUND}_${p}.raw.fifo" \
            "$STAGE_DIR/.round_${ROUND}_${p}.log.fifo" \
            "$STAGE_DIR"/.round_"${ROUND}_${p}".safe.* \
            "$STAGE_DIR"/.round_"${ROUND}_${p}".log.safe.*
    done
    [ "$REVIEW_MODE" = "incremental" ] \
        && rm -f -- "$STAGE_DIR/round_${ROUND}_codex.json"
    rmdir -- "$STAGE_DIR" 2>/dev/null || true
}
_cleanup() {
    local pid status
    for pid in "${PIDS[@]:-}"; do kill -TERM "$pid" 2>/dev/null || true; done
    for pid in "${PIDS[@]:-}"; do wait "$pid" 2>/dev/null || true; done
    [ ${#PROMPTS[@]} -gt 0 ] && rm -f "${PROMPTS[@]}"
    if [ -n "$CLAIM_ID" ] && [ "$CLAIM_FINISHED" -eq 0 ]; then
        status="$(python3 "$GUARD" claim-status "$DOC_PATH" "$CLAIM_ID" 2>/dev/null || true)"
        if [ "$status" = "success" ]; then
            CLAIM_FINISHED=1
        else
            [ ${#PUBLISHED[@]} -gt 0 ] && rm -f -- "${PUBLISHED[@]}"
            python3 "$GUARD" finish "$DOC_PATH" "$CLAIM_ID" failed >/dev/null 2>&1 || true
        fi
    fi
    _cleanup_stage
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
magi_lock_acquire "$DOC_CONTROL_DIR/.review.${ARTIFACT_ID}.lock" || {
    echo "fanout: another fan-out is already running for round $ROUND in $OUT_DIR" >&2
    exit 5
}

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
STAGE_DIR="$OUT_DIR/.claim-$CLAIM_ID"
mkdir -m 700 "$STAGE_DIR"

for p in "${PERSONAS[@]}"; do
    tmpl="$TEMPLATE_DIR/${p}_prompt.md"
    prompt="$(mktemp)"
    PROMPTS+=("$prompt")
    {
        printf 'You are the %s reviewer in a Magi review. Stay strictly in your lane;\n' "${p^^}"
        printf 'do not cover the other reviewers'"'"' perspectives. You cannot see their output.\n\n'
        printf 'PERSONA BRIEF:\n---\n'
        cat "$tmpl"
        printf '\n---\n\nSCHEMA GROUNDING (mandatory): verify every load-bearing claim by RUNNING a\n'
        printf 'command (rg / grep / reading real files). Report each verbatim in\n'
        printf 'verify_commands_executed. Doc-vs-reality drift is a CRITICAL finding. If you ran\n'
        printf 'no verification commands you MUST self-report schema_grounding_verdict "FAIL".\n'
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
        printf 'ROUND: %s\nTARGET DOC: %s\nARTIFACT ID: %s\nARTIFACT SHA256: %s\n\n' \
            "$ROUND" "$DOC_PATH" "$ARTIFACT_ID" "$ARTIFACT_SHA"
        if [ "$PRIOR" != "-" ]; then
            printf 'PRIOR SYNTHESIS (check resolution and classify relationships; do not repeat):\n---\n'
            (
                eval "exec ${MAGI_LOCK_FD}>&-"
                exec python3 "$SCRUB" < "$PRIOR"
            )
            printf '\n---\n\n'
        fi
        printf 'DOCUMENT:\n---\n'
        cat "$DOC_PATH"
        printf '\n---\n\nReturn ONLY a JSON object conforming to the output schema. reviewer="%s", round=%s, artifact_id="%s", artifact_sha="%s".\n' \
            "${p^^}" "$ROUND" "$ARTIFACT_ID" "$ARTIFACT_SHA"
    } > "$prompt"

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
          exec python3 "$SCRUB" < "$raw_fifo" > "$safe_out"
      ) & raw_scrub_pid=$!
      (
          exec 7>&- 8>&-
          eval "exec ${MAGI_LOCK_FD}>&-"
          exec python3 "$SCRUB" --text < "$log_fifo" > "$safe_log"
      ) & log_scrub_pid=$!
      "$CROSS_CLI_GUARD" --isolate-tmux -- \
        timeout --signal=TERM --kill-after=2s "$FANOUT_TIMEOUT_S" \
        codex exec --skip-git-repo-check -s read-only --ephemeral \
        -C "$REPO_ROOT" \
        --output-schema "$SCHEMA_FILE" \
        -o "$raw_fifo" \
        - < "$prompt" > "$log_fifo" 2>&1 & codex_pid=$!
      wait "$codex_pid" || s=$?
      codex_pid=""
      exec 7>&- 8>&-
      wait "$raw_scrub_pid" || scrub_rc=1
      wait "$log_scrub_pid" || scrub_rc=1
      rm -f "$prompt" "$raw_fifo" "$log_fifo"
      trap - INT TERM EXIT
      if [ "$s" -eq 0 ] && [ "$scrub_rc" -eq 0 ]; then
          label="$(artifact_label "$p")"
          mv "$safe_out" "$STAGE_DIR/round_${ROUND}_${label}.json"
          mv "$safe_log" "$STAGE_DIR/round_${ROUND}_${label}.log"
      else
          rm -f "$safe_out" "$safe_log"
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
    if [ ! -s "$out" ] || ! python3 "$VALIDATOR" "$out" "$SCHEMA_FILE" --doc "$DOC_PATH" 2>/dev/null
    then
        echo "fanout: reviewer $p produced no schema-valid output" >&2
        rc=1
        continue
    fi
done

if [ $rc -ne 0 ]; then
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
_cleanup_stage
echo "fanout: ${#PERSONAS[@]} reviewers complete -> $OUT_DIR"
