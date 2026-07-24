---
name: magi
description: Run a one-shot, three-perspective pre-flight before a high-cost, long-running, irreversible, or new-layer change. Launch independent MELCHIOR, BALTHASAR, and CASPAR reviewers concurrently, bind their structured artifacts to one exact brief, and use the deterministic gate to emit PROCEED, PIVOT, or ABORT. Never use this as a multi-round review or shipping authority.
---

# Magi one-shot pre-flight

Resolve the installed `harness-magi-codex` plugin root with
`scripts/resolve-root.sh`; it supports native/symlink installs and the legacy
`--copy` ownership marker. Never assume the user's project root contains it.

## Protocol

1. Write a brief of at most 200 lines covering the change, driver, resource
   envelope, reversibility, and collision risks.
2. Read [review-contract.md](references/review-contract.md).
3. Use the bundled runner to launch MELCHIOR, BALTHASAR, and CASPAR
   concurrently as isolated processes:
   - MELCHIOR: architecture, silent failures, hidden per-unit costs.
   - BALTHASAR: recovery, monitoring, resource peaks, concurrent operations.
   - CASPAR: alternatives, ROI, scope cuts, pre-commit cut lines.
4. Run:

   ```bash
   MAGI_ROOT="$(scripts/resolve-root.sh)" || exit 2
   "$MAGI_ROOT/scripts/magi_preflight_codex.sh" \
     /absolute/path/to/brief.md /absolute/path/to/output-directory
   ```

   The runner starts all three providers before collecting output. It uses
   `bubblewrap` to hide sibling staged files in a private mount/PID namespace,
   publishes three scrubbed artifacts, commits `preflight-run.json` last, and
   invokes the deterministic evaluator, which reconstructs each prompt digest.
5. Follow the emitted `PROCEED`, `PIVOT`, or `ABORT`. Preserve every
   `QUESTION` in `questions`; do not silently convert it into consensus.

## Hard boundaries

- Exactly one reviewer round is permitted. A `PIVOT` narrows the implementation
  plan; it does not launch another Magi round.
- A grounded minority `CRITICAL`, security, data-loss, or irreversibility
  finding is a veto. Majority prose cannot erase it.
- The evaluator is report-only. `authorizes_shipping` is always `false`.
- Magi never invokes the Dual-Magi G1-G9 gate and never creates a plateau
  marker.
- Exit `0` means a complete deterministic decision, exit `2` means unsafe or
  incomplete evidence and emits fail-closed `ABORT`, and exit `64` means usage
  error.
