---
name: ultramagi
version: 0.1.0-kimi
description: |
  End-to-end design-to-implementation rigor loop for Kimi. Gate the design with dual-magi, build
  only after exact-revision plateau, adversarially bug-hunt the implementation, then run final
  executable review and tests. Use for hard-to-reverse, production-critical changes.
type: prompt
whenToUse: |
  Canonical migrations, public launches, security boundaries, scoring/ranking changes, and explicit
  ultramagi requests. Not for small diffs or design-only work.
disableModelInvocation: false
---

# ultramagi — Kimi orchestrator

Before writing a design, classify the request as one task or an Epic. It is an Epic when it has
multiple independently mergeable/verifiable/rollbackable outcomes, cannot be described by one
invariant plus one acceptance-test/rollback pair, crosses ordered phases with separate failure
gates, or would require multiple task-level implementation plans.

For an Epic, first create a parent Epic in the repository's native issue tracker containing the
global invariant, non-goals, ordered dependency-aware slices, acceptance criteria, rollback
boundaries, and a status checkbox/link for every slice. If no remote tracker is configured, create
`docs/designs/<EPIC>-EPIC.md` with those fields and record the remote Epic as pending. Split the
work into vertical, independently mergeable slices, preferably one pull request and one observable
acceptance test each. Keep umbrella coordination separate from implementable detail in
`docs/designs/<EPIC>/<NN>-<SLICE>.md`.

Select only the first unblocked slice for the loop below. Never plateau-review or implement the
whole umbrella Epic as one task. After each gated slice, update the Epic with gate/test evidence
and dependency changes, then select the next unblocked slice. Do not create an Epic when the
request is already one independently mergeable slice.

1. State the slice invariant and inherited Epic invariant, then write the slice design locally.
2. Invoke the Kimi `dual-magi-review` skill. Do not code until its mechanical plateau marker matches
   the current design revision.
3. Implement reversible, repo-baked changes. Record actual family routing when Kimi codes directly.
4. Freeze the implementation under review as a stable artifact (for example, a generated patch
   file). Record its exact SHA; editing code requires regenerating the artifact.
5. Run the shared runtime's odd-numbered `magi_fanout_codex.sh` phase with
   `--persona-set bug-hunt` against that artifact. Run
   `magi_synthesize.py ... <state>/round_<round>_bug-hunt_synthesis.json --persona-set bug-hunt`
   to require all three bug-hunt reviewers and carry and validate every source finding before
   proceeding.
6. Run the following even-numbered `magi_xfamily.sh --reviewer claude` phase against the same
   implementation artifact, using the bug-hunt synthesis as prior. Use Grok only as an explicit
   fallback. Then run `magi_plateau_gate.sh` on that exact artifact and reviewer family.
7. Any blocking finding or nonzero gate requires a fix, regenerated artifact, cross-family
   synthesis, and the next odd/even round pair. Never reuse a marker after code changes.
8. Only after the implementation marker matches the current artifact, run final executable tests
   and inspect the final diff against both the design and implementation artifacts.

Preferred routing remains Claude design intent → Codex implementation/executable review → Claude
adversarial review → Codex fixes/tests. Kimi may control or implement reversible work, but must not
silently remove the Codex/Claude review gates. Kimi has no Codex autorun Stop hook; durable campaign
state supports inspection/resume but not acknowledgement-free continuation.

The design campaign defaults to 12 weighted model launches (three fan-out plus mandatory
cross-family pairs without retries). Requirement revisions share a fixed global allowance of 16.
`MAGI_MAX_AUTONOMOUS_MODEL_LAUNCHES` may tighten the default for smaller targets, never extend it.

Whenever actual routing differs from the preferred route, write this literal handoff before that
phase:

```text
FAMILY_ROUTING
preferred: Claude design -> Codex implementation -> Claude review -> Codex fixes/tests
actual: <families and phases that actually ran>
missing: <family / phase / CLI, subscription, capacity, or rate-limit reason>
degraded_until: <exact review or executable gate that must pass before ship>
```

Do not use a fallback for convenience. A fallback is allowed only when the preferred family is
unavailable because its CLI, contract/subscription, capacity, or rate limit blocks the phase.
Without the complete handoff, or before `degraded_until` is satisfied, do not claim plateau or
cross an irreversible boundary.

Never claim plateau yourself, never treat unprocessed reviewer output or a failed gate as advisory,
and never cross an irreversible boundary without both exact-revision design and implementation
gates.
