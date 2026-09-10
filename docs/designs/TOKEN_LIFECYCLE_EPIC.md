# Token lifecycle controls

Follow-up to claude-harness #218, #143, #184, #282 and the September 6–8 usage audit.

## Global invariant

Reduce repeated context and unnecessary model turns without losing active work,
mailbox/ASK state, user input, or exact-revision independent review. Resource
thresholds request a continuation boundary; they never declare work complete.

## Independently verifiable slices

- [x] A. Correct Claude daily usage accounting in hippocampus-mcp: one provider
  response counted once, including split tool blocks and final usage updates.
  Acceptance: real-shaped fixture and read-only September audit agree.
- [x] B. Session lifecycle: measure actual response/context usage, create a
  bounded continuation packet, and use a supported safe continuation primitive.
  Preserve identity, pending ASK, active jobs, cwd, permissions, and user draft.
  Acceptance: offline E2E crosses a threshold and continues work with reduced
  context; interrupted/invalid handoff cannot terminate or lose the source.
- [x] C. Coordination traffic: use the existing mailbox event stream, coalesce
  notifications, and prevent repeated unchanged status checks from flooding
  context. Acceptance: burst/unchanged/ASK/DONE tests retain required signals.
- [x] D. Review scope: bind bounded review packets to exact revisions and prior
  evidence, with a mechanically checked whole-change integration review.
  Acceptance: omitted/changed/out-of-scope files invalidate stale evidence;
  no final approval from an incomplete slice set.

## Ordering and rollback

A establishes trustworthy measurements. B and C operate on the existing
Formation lifecycle; D reuses the existing review packet and convergence gates.
Each slice has separate implementation/tests and can be reverted independently.
All work starts in isolated worktrees. No runtime symlink/config/deployment
changes until the complete candidate and integration evidence are reviewable.
After distribution, stop mutations as required by the operator checkpoint.

## Non-goals

No model quota conversion inferred from API token prices. No arbitrary killing
of live Claude processes, synthetic user turns, hidden prompt injection, removal
of required security/correctness reviews, or fabrication of approval/plateau.
Do not infer semantic milestone completion from a token counter.

## Admission

The first reversible step is a corrected read-only usage scanner and fixture.
Its source logs and DB are unchanged; rollback is removal of the isolated diff.
Later slices require their own concrete primitive/acceptance design before code.

## Implementation evidence (2026-09-10; activation pending)

The checkboxes above mean local implementation and validation, not deployment.

- A: hippocampus-mcp commit `c861560`; 11 pytest checks. Read-only September
  6–8 scan matches audit: 55,207,378 new input and 8,028,765 output tokens.
- B: native lifecycle `110de8d` plus the environment-precedence test. 20 pytest
  checks cover the hook, settings backup, launch command and installed Claude
  Code 2.1.265. Localhost mock: env 100k/flag 200k compacts; env 200k/flag 100k
  does not. Mock usage is not an estimate of subscription quota savings.
- C: `0dc20a9`; inbox follow 11 checks pass, including 100 arrivals producing
  one notification with ASK/DONE and cursor intact. Existing ASK suite passes.
- D: 59 convergence tests plus 8 subtests pass. Ordinary packets above 200,000
  bytes fail before writing; `--full-integration` preserves the full base-to-
  target diff with the existing 900,000-byte ceiling. It cannot be incremental.
  Exact SHA, complete path coverage and final independent review gates remain.
- Cross-CLI overlay validates all 25 entries. Independent focused code reviews
  covered A–D; the non-Claude hook-routing finding was fixed and regression-tested.

No live hook sync, canonical checkout update, ledger backfill, or user setting
change has run. After merge, activate hooks from the canonical checkout, apply
`configure_token_lifecycle.py --apply`, and verify wiring/native window. Keep
past ledger backfill separate from source-log read-only validation. Respect the
operator hard stop after distribution; subsequent fixes need explicit direction.
