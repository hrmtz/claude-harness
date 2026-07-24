# Magi brief: issue #116 convergence profiles

## Change

Extract the bounded, report-only implementation convergence behavior introduced
by issue #107 into a shared pure decision kernel. Preserve the
`ultramagi-implementation` behavior and reason codes, then add:

- `dual-magi-design`: bounded design correction before the existing exact-
  revision cross-family G1-G9 plateau gate.
- `magi-preflight`: deterministic one-shot aggregation of three independent
  reviewer artifacts into `PROCEED`, `PIVOT`, or `ABORT`.

The kernel may report a next action or terminal decision only. It must always
emit `authorizes_shipping: false`, cannot create a plateau marker, cannot replace
G1-G9, cannot change provider/family routing, and cannot increase the global
16-launch ceiling.

## Driver

Issue #107 bounded Ultramagi implementation correction, but Dual-Magi can still
revise until the launch fuse and Magi lacks mechanical completeness, minority
veto, and disagreement rules. Issue #116 requires purpose-specific profiles
instead of copying the Ultramagi policy into every workflow.

The branch is `feat/issue-116-convergence-profiles` at required baseline
`6cff82d409a4aca56e1fafe19a3bb9ba439a8793`.

## Required contracts

### Shared primitives

- Exact artifact/revision digest binding.
- Stable `root_cause_id`.
- `new`, `resolved`, `repeated`, and `regression` revision delta.
- Blocking severity mass and stall detection.
- Launch affordability with reserved mandatory cross-family transition.
- Deterministic reason codes and fail-closed malformed/stale/missing/mutating
  evidence handling.
- Pure, table-driven, report-only policy evaluation.

### Ultramagi implementation

- Preserve #107 outputs through extraction or a compatibility adapter.
- Blocking severities remain `REJECT`, `CRITICAL`, and `HIGH`.
- Repeated blocking roots or recurrent fix-induced regression can force
  `REDESIGN`.
- Maximum two logical correction cycles.
- Exhausted deadline, retry/cycle allowance, or transition budget is `BLOCKED`.
- Clean review only hands off to the existing plateau authority.
- Add golden/replay histories proving decision and reason-code identity.

### Dual-Magi design

- Same HIGH+ root on the next exact revision => `REDESIGN`.
- Recurring new HIGH+ roots in one subsystem => `SCOPE_SPLIT` (or equivalent
  explicit terminal redesign).
- Non-decreasing blocker mass across the bounded window => `BLOCKED`.
- Maximum two logical correction cycles.
- HIGH+ zero plus verified current-revision cross-family review =>
  `PLATEAU_CANDIDATE`, never plateau authority.
- Only `magi_plateau_gate.sh` may create the plateau marker.
- Reject an unaffordable `fanout + reserved xfamily` transition before launch.

### Magi pre-flight

- Exactly one aggregation round; never launch an autonomous second round.
- Require three independent, complete reviewer artifacts.
- A grounded minority `CRITICAL`, security, data-loss, or irreversibility
  finding survives as a veto.
- Ordinary findings follow corroboration policy.
- Unsupported minority concerns become explicit `QUESTION` entries.
- Deterministically emit only `PROCEED`, `PIVOT`, or `ABORT`.

## Likely implementation surface

- Refactor `plugins/harness-magi-codex/scripts/magi_convergence_gate.py` into a
  small common kernel plus profile adapters.
- Integrate profile decisions with campaign guard/fanout/plateau orchestration
  without changing G1-G9 or routing.
- Add profile and pre-flight schemas/scripts where runtime validation is needed.
- Update Codex skill docs and corresponding Claude/Kimi mirrors required by
  repository policy.
- Update installer/plugin drift tests and manifests only where new installed
  files require it.

## Verification

- Run existing convergence tests before edits as a smoke baseline.
- Run targeted unit and shell tests after each slice.
- Add golden #107 replay, table-driven profile, malformed/stale/symlink/mutation,
  minority-veto, independence/completeness, budget reservation, and plateau-
  authority negative tests.
- Run relevant documentation, schema, plugin-manifest, installer, and mirror
  drift suites.
- Finish with bounded adversarial review and code review; resolve all HIGH+.

## Cost and resource envelope

- Expected engineering walltime: multiple hours on one dedicated worktree.
- External spend: none expected.
- Runtime resources: local Python/shell tests; no large DML, service deployment,
  paid API batch, or production data mutation.
- Disk/memory/CPU peaks should remain ordinary repository-test scale.
- The principal cost is reviewer launch budget: all paths must remain under the
  existing 16-launch ceiling and reserve mandatory cross-family review.

## Reversibility and recovery

- Work is isolated on a dedicated branch and will be committed in intentional
  slices.
- Before each commit, failures can be recovered by surgical edits while
  preserving unexpected user changes.
- No destructive git operations, force pushes, broad cleanup, or GitHub writes.
- If a partial integration fails, keep the shared-kernel extraction compatible
  and narrow later profile work rather than weakening acceptance criteria.

## Concurrent collision risks

- Shared filesystem edits from independent reviewers are forbidden during this
  pre-flight; reviewers return reports only.
- Parent Formation coordination may send messages while work proceeds; inspect
  the inbox at phase boundaries.
- Do not touch other worktrees, Formation identity/process-lock code, provider
  routing, credentials, or unrelated files.

## Pre-commit cut lines

- `ABORT` if preserving #107 reason-code behavior requires weakening G1-G9,
  shipping authority, routing policy, or the launch ceiling.
- `PIVOT` if the common kernel cannot remain small/pure or if Magi aggregation
  begins to require a multi-round workflow.
- Ask the parent before any public schema/exit-code compatibility change or
  GitHub mutation.
