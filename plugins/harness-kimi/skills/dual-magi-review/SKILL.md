---
name: dual-magi-review
version: 0.1.0-kimi
description: |
  Exact-revision design review for Kimi using three independent Codex reviewers and a mandatory
  Claude cross-family round (Grok fallback), followed by the mechanical G1-G9 plateau gate.
type: prompt
whenToUse: |
  Production-critical or large design documents, implementation plans with irreversible impact,
  and explicit dual-magi requests. Not for small diffs.
disableModelInvocation: false
---

# dual-magi-review — Kimi orchestrator

Kimi controls the workflow; Codex is the same-family review panel and Claude (or explicit Grok
fallback) is the cross-family reviewer. The gate therefore uses `--orchestrator-family codex`.
Do not label Kimi as the artifact-producing reviewer family.

## Resolve the runtime

```bash
KIMI_BASE="${KIMI_CODE_HOME:-${HOME:?set KIMI_CODE_HOME or HOME}/.kimi-code}"
RUNTIME="${HARNESS_MAGI_RUNTIME:-$KIMI_BASE/harness-magi-runtime}"
RUNTIME="$(readlink -f "$RUNTIME")"
test -x "$RUNTIME/scripts/magi_fanout_codex.sh"
test -x "$RUNTIME/scripts/magi_xfamily.sh"
test -x "$RUNTIME/scripts/magi_plateau_gate.sh"
```

The default runtime is a symlink into the `claude-harness` checkout; the checkout must remain
present. Do not run `magi_autorun.py`: it is bound to Codex thread/Stop-hook state and is not a Kimi
continuation primitive. Resolve `RUNTIME` again in each separate Bash tool call; shell variables do
not persist between Kimi tool invocations.

## One bounded campaign

Fix one target document and state directory `${doc_dir}/.dual-magi/`. Number every provider phase
monotonically; never restart at rounds 1/2 inside the same campaign:

1. Set `round=1`. Fan-out rounds are odd; cross-family rounds are the following even number.
2. Run `magi_fanout_codex.sh <doc> "$round" <state> --persona-set magi --prior <prior>`.
   Use `--prior -` only at round 1. At later odd rounds, `<prior>` is the synthesis of the immediately
   preceding cross-family round.
3. Run `python3 "$RUNTIME/scripts/magi_synthesize.py" <doc> "$round" <state>
   <state>/round_<round>_magi_synthesis.json --persona-set magi`. It requires the exact MELCHIOR,
   BALTHASAR, and CASPAR source set, carries every finding with deterministic source-qualified IDs,
   and validates the envelope. The output basename is fixed by the persona set — it records which
   reviewer family produced the round, and contradictory basenames are refused. You may deduplicate
   or resolve entries afterward only if you immediately re-run `magi_validate_findings.py`. Never
   author the three reviewer prompts yourself.
4. Revise the document for fan-out findings when needed. Set `cross_round=$((round + 1))`, then run
   `magi_xfamily.sh --reviewer claude <doc> "$cross_round" <fanout-synthesis>
   <state>/round_<cross_round>_xfamily`. Use `grok` only as an explicit fallback and record the
   reason.
5. Run `magi_plateau_gate.sh <doc> <state>/round_<cross_round>_xfamily
   --orchestrator-family codex --reviewer-family claude|grok`. A nonzero gate blocks
   implementation.
6. Whether the gate passes or fails, synthesize the cross-family round with
   `magi_synthesize.py <doc> "$cross_round" <state>
   <state>/round_<cross_round>_xfamily_synthesis.json --persona-set xfamily` before another provider
   phase so no finding is silently dropped. Never name this output `round_<N>_codex.json`: the
   findings came from Claude/Grok, and a same-family basename mislabels provenance on resume.
   If revision is required, edit the document, set `round=$((round + 2))`, and return to step 2
   using that cross-family synthesis as `<prior>`.

The valid sequence is fan-out 1 → cross-family 2 → fan-out 3 → cross-family 4, continuing until the
gate accepts the current revision or the fixed campaign allowance stops it. Every nonzero provider,
validator, or gate exit blocks implementation.
The default per-campaign allowance is 12 weighted model launches: three fan-out plus mandatory
cross-family pairs without retries. Requirement revision may roll into the fixed global allowance
of 16, but changing the state directory cannot reset history. For a smaller target,
`MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES` may tighten 12 and cannot extend it.

Run every phase through this checked Bash pattern in the same tool call as the command. The failure
line is the handoff record: it contains the exact shell-escaped originating command and status.

```bash
run_checked() {
  phase="$1"; shift
  "$@"; status=$?
  if [ "$status" -ne 0 ]; then
    printf 'MAGI_PHASE_FAILED phase=%s status=%s command:' "$phase" "$status" >&2
    printf ' %q' "$@" >&2
    printf '\n' >&2
    return "$status"
  fi
}

run_checked fanout "$RUNTIME/scripts/magi_fanout_codex.sh" \
  "$doc" "$round" "$state" --persona-set magi --prior "$prior"
status=$?
if [ "$status" -ne 0 ]; then
  printf 'MAGI_HANDOFF_BLOCKED status=%s; do not start the next phase or implementation\n' \
    "$status" >&2
  exit "$status"
fi
```

Use the same `run_checked` wrapper for synthesis, cross-family, validation, and plateau gate calls.
Do not run the next phase after `MAGI_PHASE_FAILED`.

Only the gate may declare plateau. A marker must match the current document revision. Kimi has no
autorun Stop hook; after session loss, inspect the campaign ledger and artifacts before resuming.
Filenames are provenance: `round_<N>_<persona>.json` are raw reviewer outputs, and
`round_<N>_<persona-set>_synthesis.json` are synthesis envelopes — an `_xfamily_synthesis` file
carries Claude/Grok findings, a `_magi_synthesis` file carries same-family findings.
