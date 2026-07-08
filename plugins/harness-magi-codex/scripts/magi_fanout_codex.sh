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
        printf 'ROUND: %s\nTARGET DOC: %s\nARTIFACT SHA256: %s\n\n' "$ROUND" "$DOC_PATH" "$ARTIFACT_SHA"
        printf 'DOCUMENT:\n---\n'
        cat "$DOC_PATH"
        printf '\n---\n\nReturn ONLY a JSON object conforming to the output schema. reviewer="%s", round=%s.\n' "${p^^}" "$ROUND"
    } > "$prompt"

    # All three launch before any output is read (INV-3).
    # `|| s=$?` so a codex failure does not abort the subshell under the inherited `set -e`
    # (which would skip the rm), while still propagating the real status to `wait`.
    ( s=0
      codex exec --skip-git-repo-check -s read-only --ephemeral \
        -C "$REPO_ROOT" \
        --output-schema "$SCHEMA_FILE" \
        -o "$OUT_DIR/round_${ROUND}_${p}.json" \
        - < "$prompt" > "$OUT_DIR/round_${ROUND}_${p}.log" 2>&1 || s=$?
      rm -f "$prompt"
      exit "$s" ) &
    PIDS+=("$!")
done

rc=0
for pid in "${PIDS[@]}"; do
    wait "$pid" || rc=1
done

for p in "${PERSONAS[@]}"; do
    out="$OUT_DIR/round_${ROUND}_${p}.json"
    if [ ! -s "$out" ] || ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$out" 2>/dev/null; then
        echo "fanout: reviewer $p produced no valid output" >&2
        rc=1
    fi
done

[ $rc -eq 0 ] && echo "fanout: ${#PERSONAS[@]} reviewers complete -> $OUT_DIR"
exit $rc
