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
#   5  a same-round sibling output already exists (re-run would contaminate)
#  64  usage
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SELF_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
SCHEMA_FILE="$PLUGIN_DIR/schemas/finding.schema.json"
SCRUB="$SELF_DIR/magi_scrub.py"
CANON="$REPO_ROOT/plugins/harness-magi/skills"

usage() { echo "usage: $0 <doc-path> <round> <out-dir> [--persona-set magi|bug-hunt]" >&2; exit 64; }
[ $# -ge 3 ] || usage

DOC_PATH="$1"; ROUND="$2"; OUT_DIR="$3"; shift 3
PERSONA_SET="magi"
while [ $# -gt 0 ]; do
    case "$1" in
        --persona-set) [ $# -ge 2 ] || usage; PERSONA_SET="$2"; shift 2 ;;
        *) usage ;;
    esac
done

case "$PERSONA_SET" in
    magi)     PERSONAS=(melchior balthasar caspar) ;;
    bug-hunt) PERSONAS=(hornet gnat wasp) ;;
    *) echo "fanout: unknown persona set: $PERSONA_SET" >&2; exit 64 ;;
esac

TEMPLATE_DIR="$CANON/$PERSONA_SET/templates"
[ -d "$TEMPLATE_DIR" ] || { echo "fanout: canonical templates not found: $TEMPLATE_DIR" >&2; exit 64; }
[ -f "$DOC_PATH" ] || { echo "fanout: doc not found: $DOC_PATH" >&2; exit 64; }
command -v codex >/dev/null 2>&1 || { echo "fanout: codex CLI not found" >&2; exit 1; }
mkdir -p "$OUT_DIR"

ARTIFACT_SHA="$(sha256sum "$DOC_PATH" | cut -d' ' -f1)"

# Prompts hold the FULL document. Track them so no copy is left in TMPDIR on any exit path.
PROMPTS=()
_cleanup() { [ ${#PROMPTS[@]} -gt 0 ] && rm -f "${PROMPTS[@]}"; return 0; }
trap _cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# INV-3: the sibling check below is check-then-spawn, and a reviewer's output file does not
# appear until codex finishes minutes later. Without a lock, two concurrent same-round runs
# both pass the check and clobber each other's outputs -- in the one script whose entire
# purpose is contamination control. Take the lock first.
# shellcheck source=magi_lock.sh
source "$SELF_DIR/magi_lock.sh"
magi_lock_acquire "$OUT_DIR/.fanout.round_${ROUND}.lock" || {
    echo "fanout: another fan-out is already running for round $ROUND in $OUT_DIR" >&2
    exit 5
}

# INV-3: refuse to start if a sibling output for this round already exists.
for p in "${PERSONAS[@]}"; do
    if [ -e "$OUT_DIR/round_${ROUND}_${p}.json" ]; then
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

PIDS=()
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
        printf 'ROUND: %s\nTARGET DOC: %s\nARTIFACT SHA256: %s\n\n' "$ROUND" "$DOC_PATH" "$ARTIFACT_SHA"
        printf 'DOCUMENT:\n---\n'
        cat "$DOC_PATH"
        printf '\n---\n\nReturn ONLY a JSON object conforming to the output schema. reviewer="%s", round=%s.\n' "${p^^}" "$ROUND"
    } > "$prompt"

    # All three launch before any output is read (INV-3).
    # `|| s=$?` so a codex failure does not abort the subshell under the inherited `set -e`
    # (which would skip the rm), while still propagating the real status to `wait`.
    ( s=0; scrub_rc=0
      raw_fifo="$OUT_DIR/.round_${ROUND}_${p}.raw.fifo"
      log_fifo="$OUT_DIR/.round_${ROUND}_${p}.log.fifo"
      safe_out="$(mktemp "$OUT_DIR/.round_${ROUND}_${p}.safe.XXXXXX")"
      safe_log="$(mktemp "$OUT_DIR/.round_${ROUND}_${p}.log.safe.XXXXXX")"
      rm -f "$raw_fifo" "$log_fifo"
      mkfifo "$raw_fifo" "$log_fifo"
      # Keep a writer open until codex returns so either scrubber receives EOF even when codex
      # fails before opening its sink. Bytes travel through FIFOs; only scrubbed bytes hit disk.
      exec 7<>"$raw_fifo" 8<>"$log_fifo"
      ( exec 7>&- 8>&-; python3 "$SCRUB" < "$raw_fifo" > "$safe_out" ) & raw_scrub_pid=$!
      ( exec 7>&- 8>&-; python3 "$SCRUB" --text < "$log_fifo" > "$safe_log" ) & log_scrub_pid=$!
      codex exec --skip-git-repo-check -s read-only --ephemeral \
        -C "$REPO_ROOT" \
        --output-schema "$SCHEMA_FILE" \
        -o "$raw_fifo" \
        - < "$prompt" > "$log_fifo" 2>&1 || s=$?
      exec 7>&- 8>&-
      wait "$raw_scrub_pid" || scrub_rc=1
      wait "$log_scrub_pid" || scrub_rc=1
      rm -f "$prompt" "$raw_fifo" "$log_fifo"
      if [ "$s" -eq 0 ] && [ "$scrub_rc" -eq 0 ]; then
          mv "$safe_out" "$OUT_DIR/round_${ROUND}_${p}.json"
          mv "$safe_log" "$OUT_DIR/round_${ROUND}_${p}.log"
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
    out="$OUT_DIR/round_${ROUND}_${p}.json"
    if [ ! -s "$out" ] || ! python3 - "$SCHEMA_FILE" "$out" <<'PY' 2>/dev/null
import json, jsonschema, sys
schema = json.load(open(sys.argv[1]))
instance = json.load(open(sys.argv[2]))
jsonschema.validate(instance=instance, schema=schema)
PY
    then
        echo "fanout: reviewer $p produced no schema-valid output" >&2
        rc=1
        continue
    fi
done

if [ $rc -ne 0 ]; then
    # Remove this run's partial outputs. We hold the round lock, so these files are ours.
    # Leaving them behind would make the INV-3 sibling check reject every subsequent retry
    # (exit 5) forever -- a permanent dead-end after one transient codex timeout.
    echo "fanout: clearing partial round-$ROUND outputs so a retry is possible" >&2
    for p in "${PERSONAS[@]}"; do rm -f "$OUT_DIR/round_${ROUND}_${p}.json"; done
    exit $rc
fi

echo "fanout: ${#PERSONAS[@]} reviewers complete -> $OUT_DIR"
