# Grok termination and zero-blocker design review recovery

2026-09-10. Two bounded compatibility fixes to unblock the existing shinfutsu
information-boundary review. User explicitly authorized proceeding after the
post-distribution checkpoint and the concrete proposal. Local source and tests
may change; installed artifacts change only after exact review and verification.

## Observed failures

1. The installed Grok adapter accepts `EndTurn` or an absent stop reason. The
   actual CLI returned `end_turn`; its completed run was rejected as UNPARSEABLE
   before structured output validation. Retain that failed receipt and its charge.
2. Design convergence treats three revisions with blocking mass `0,0,0` as
   stalled. With no HIGH-or-worse roots and a still-missing diverse phase, it
   returns `DESIGN_BLOCKER_MASS_STALLED` before requesting that phase. The actual
   pure kernel reproduces this at usage 10/16. Zero blockers are not a plateau.

Source locations: `plugins/harness-magi-codex/scripts/magi_xfamily.sh` provider
envelope parsing and `scripts/magi_convergence_kernel.py` design profile.
Observed shinfutsu candidate is `6fd8f32a648873dd62efb08900b6407ff45f7c7c`, draft
PR46. Its latest same-family implementation review has no HIGH-or-worse finding.
These tool fixes do not approve that candidate or implement its future milestones.

## Smallest repair and invariants

- Accept `end_turn` alongside existing `EndTurn` and absent-value compatibility.
  Unknown or failed endings still fail closed. The normal schema, transcript,
  identity, source digest, protocol and ledger checks still execute afterward.
- Apply design's mass-stall stop only while current blocking roots exist. Clean
  fanout revisions still require the diverse final phase. A budget shortfall,
  repeated blocking root, recurring subsystem or real blocking stagnation keeps
  its existing outcome. Only the existing plateau gate can write a success marker.
- Do not change severity weights, budgets, max cycles, provider permissions,
  model routing, required review phases or prior charged usage. No ledger edit,
  retroactive successful receipt, or waiver replaces a fresh valid review.
- Preserve existing callers and pure kernel interfaces. Reuse the current fake
  provider shell harness and kernel/design-adapter test fixtures. No new service,
  generic provider wrapper or configuration surface is needed.

## Execution and validation

This is a reversible isolated worktree based on `e9a7cf6`, owned by
codex-frost-crane. engine_routes owns the Grok parser and its shell regression;
registry_check owns the design kernel and its existing tests. Root freezes both
runtime files before protocol-bound tests or review. Existing files receive
persistent Sanada backups before edits.

Required checks: both successful Grok ending spellings, unknown ending rejection
without published findings/meta, existing provider failure/provenance checks;
three clean design revisions requiring final diverse review, zero-root budget
shortfall, and unchanged true blocking stops. Run existing convergence,
design-convergence and autorun tests plus manifest validation. Tests use synthetic
providers/ledgers and never mutate another campaign.

Review this frozen design and the exact implementation through separate bounded
same-family and Grok phases. The repaired checkout is the candidate review runtime;
its complete protocol is frozen before either phase, avoiding mixed generations.
This local bootstrap use does not distribute or self-certify the patch. Independent
review artifacts and the mechanical gate are still required before installation.

FAMILY_ROUTING preferred: Claude design, Codex implementation, Claude adversarial
review, Codex final verification. Actual: Codex bounded preparation, Grok exact
design review, Codex implementation alignment, Grok exact implementation review,
Codex final checks. Claude's weekly quota is documented in shinfutsu's
`docs/evidence/information-boundary/claude-availability.json`. Missing Claude
phases remain explicit; degraded_until is both required Grok gates completing.

## Distribution and resuming shinfutsu

After checks/review, land the scoped source through a reviewable PR and update
only the affected existing local marketplace plugin with the normal cachebuster
helper and cache-safe Codex entrypoint. Preserve every byte of the old 0.3.0 and
0.3.1 cached runtimes used by live sessions. Verify the installed source hashes,
selected dispatcher runtime and old-cache manifests before declaring delivery.
Do not change hook permissions or write installed cache files by hand.

Then hard-stop harness mutations at the user's distribution checkpoint. Resume
shinfutsu with the fixed pinned runtime, fresh exact-revision review artifacts
and its existing charged budgets. Preserve the old blocked state as history;
do not relabel it successful. Delivery rollback is the retained prior runtime;
an attempted rollback or extra repair after delivery requires new user direction.

Remaining work is this two-fix package, its required checks and two distinct final
review gates. The existing mechanical ceilings apply; historical shinfutsu usage
is not charged to zero. Operator time and USD are unmeasured. Do not start a
broader harness redesign or retry unchanged known failures to manufacture a gate.
