# Magi pre-flight: issue #116 convergence profiles

## Trigger that fired

- The change introduces a shared review-control layer and is expected to take
  multiple engineering hours.
- A faulty implementation could weaken bounded review, evidence validation, or
  the separation between convergence and plateau/shipping authority.

## Persona summaries

### MELCHIOR (technical)

**FLAG.** Keep the shared kernel narrower than profile policy: canonical
evidence normalization, revision delta, blocker mass, affordability arithmetic,
and deterministic dispatch. Put each workflow's transition table in an explicit
profile and preserve the entire serialized #107 decision, including reason-code
precedence. Define evidence identity and pre-flight corroboration/veto semantics
mechanically. Keep launch reservation atomic in the campaign guard, outside the
pure kernel. Add negative tests proving no kernel output can become a plateau
marker.

### BALTHASAR (operational)

**FLAG.** Use intentional green slices so failure late in the work resumes from
the last compatible boundary. Runtime evidence writes should be atomic and
reruns should be idempotent or fail closed. Keep reviewer artifacts isolated,
record exact failing commands, measure baseline test resource/timing rather than
inventing production-scale requirements, and audit launch accounting. Check the
Formation inbox at phase boundaries. Do not create a second state ledger that
could be mistaken for the G1-G9 plateau marker.

### CASPAR (commercial)

**PIVOT.** Use two gated phases. First prove the shared-kernel premise with #107
golden compatibility, implement the one-shot pre-flight aggregator, and exercise
the Dual-Magi policy through tables/fixtures. Proceed to live Dual-Magi
integration only after exact compatibility and a mechanical early-stop or
launch-affordability benefit are demonstrated. Stop if the shared kernel begins
to absorb workflow-specific orchestration or if preserving compatibility would
weaken G1-G9, shipping authority, routing, or the launch ceiling.

## Synthesis

**Convergent concerns**

- The shared kernel must remain a functional, report-only core; orchestration,
  locking, atomic launch reservation, marker creation, and provider routing stay
  outside it.
- #107 compatibility must compare complete decisions and reason codes, not only
  broad action equivalence.
- Evidence identity, artifact completeness, reviewer independence, grounded
  vetoes, corroboration, and unsupported-minority questions require explicit
  schema/policy rules and fail-closed tests.
- The 16-launch fuse and mandatory cross-family reserve must be enforced
  mechanically before fanout.
- Plateau separation requires negative capability tests.
- Work should land in green, resumable slices with targeted tests after each.

**Persona-specific concerns**

- MELCHIOR recommends discriminated per-profile outputs rather than one wide
  nullable schema. This is adopted where it does not break the existing
  Ultramagi schema/output.
- BALTHASAR suggested runtime heartbeat/stage logs and host resource limits.
  Those are not added to the product scope because the implementation runs
  ordinary local tests and the issue excludes unrelated process control.
  Existing Formation phase reports provide operator liveness.
- CASPAR requested a time-box and quantitative business metric. No acceptance
  criterion is dropped, but live integration is gated on Phase 1 proving exact
  compatibility and at least one mechanical benefit in fixtures.

## Narrowed design

### Phase 1: compatibility and policy proof

1. Extract a small pure kernel with explicit profile policy.
2. Preserve `ultramagi-implementation` via a thin adapter.
3. Add golden replay of representative #107 histories, comparing complete
   decision/reason-code outputs.
4. Add table-driven `dual-magi-design` decisions demonstrating repeated-root,
   same-subsystem recurrence, stalled mass, max-cycle, clean-candidate, and
   unaffordable-transition termination.
5. Add a standalone one-shot `magi-preflight` aggregator with complete,
   independent exact-artifact inputs, grounded minority veto, corroboration, and
   question behavior.

Phase 1 passes only if Ultramagi replay is identical and the new profile
fixtures demonstrate earlier terminal decisions or protected launch reserve.

### Phase 2: bounded integration

1. Wire the design profile into Dual-Magi orchestration without changing G1-G9
   or marker authority.
2. Wire the one-shot aggregator into Magi runtime/documentation without any
   second-round launcher.
3. Update required Claude/Kimi/install mirrors, schemas, manifests, protocol
   digests, and drift tests.
4. Run targeted, relevant full, adversarial, and final code review gates.

## Verdict

**PIVOT** — retain every issue acceptance criterion, but gate live orchestration
integration behind a small functional-core proof that preserves #107 exactly
and demonstrates bounded-policy value mechanically.

## Next action

Implement Phase 1 only. If its compatibility or value gate fails, stop and
report rather than broadening or weakening the design.
