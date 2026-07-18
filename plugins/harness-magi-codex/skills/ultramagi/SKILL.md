---
name: ultramagi
description: End-to-end rigor loop for a non-trivial, hard-to-reverse change, orchestrated from Codex - local plan/design, then dual-magi review to PLATEAU, then code, then an adversarial bug-hunt on the implementation, then code review. A superset of dual-magi-review that gates the WHOLE lifecycle rather than a single design round, so that every irreversible step is preceded by adversarial, schema-grounded, cross-family review. TRIGGER, ultramagi, or a canonical migration, scoring algorithm, public launch, or data-loss-capable change. Not for small diffs, and not for a design doc with no implementation, use dual-magi-review for that.
---

# ultramagi (Codex orchestrator, Claude cross-family)

All `scripts/...` and `schemas/...` references are relative to the installed
`harness-magi-codex` plugin root (two directories above this `SKILL.md`).
Resolve that absolute plugin root before invoking a bundled script.

**dual-magi** pairs same-family reviewers with a cross-family reviewer to subtract shared training
bias. **ultramagi** wraps dual-magi around the *entire* change lifecycle: it gates both the design
(before you write code) and the implementation (before you ship), with schema-grounded verification
at each gate. It exists because AI-drafted designs are biased toward narrative coherence and skip
literal verification — and the cheapest place to catch that is before the irreversible step.

This is the Codex-orchestrated mirror of `plugins/harness-magi/skills/ultramagi`. The loop is
family-agnostic; only the adapters differ.

## Default family routing

For hard design→implementation work, prefer this routing unless the user explicitly overrides it:

```
Claude: planning / design plateau
Codex: implementation
Claude: adversarial design-intent review
Codex: final fixes + tests
```

Rationale:

- Claude is the default owner for planning/design synthesis, long-context contradiction hunting,
  operational runbooks, privacy/permission boundaries, and "what not to build" decisions.
- Codex is the default owner for repo-local implementation, migration/test mechanics, small
  scoped diffs, and final executable verification.
- Claude is the default cross-family reviewer for implementation intent: does the code still
  satisfy the plateau'd design, and did implementation introduce policy/security/ops gaps?

Codex-orchestrated ultramagi must therefore not assume "Codex writes the design because Codex is
the current chassis." If the task is still in design/planning and a Claude worker is available,
handoff or spawn Claude for the design plateau, then resume here for coding. If the user asks for
"subagent Codex coding after design", enforce that no coding starts until the design gate has a
mechanical plateau marker.

### Fallback when a family is unavailable

"Unavailable" includes missing CLI, no active contract/subscription, model capacity, rate limit,
or a worker that cannot be spawned. Fallback is allowed, but it must be explicit in the design or
handoff notes and it must not erase the cross-family gate.

Fallback order:

1. **Claude unavailable during planning**: Codex may draft and revise the design locally, but must
   mark the run as "Codex-drafted design, Claude review pending". Do not proceed to irreversible
   implementation until either Claude cross-family review runs or the user explicitly accepts the
   degraded path for a reversible spike.
2. **Codex unavailable during coding**: Claude may implement only small, reversible scaffolding or
   tests. For migration/data-loss/security changes, stop before the irreversible step and queue a
   Codex implementation/review pass when available.
3. **Claude unavailable during implementation review**: Codex may run self-review and tests, but
   the result is "not final-reviewed". Do not deploy, migrate canonical data, or tag a production
   release until Claude or another non-Codex family reviews the final diff, unless the user
   explicitly accepts the degraded release path.
4. **Both families cannot cross-review**: narrow the task to documentation, reversible spike, or
   local-only proof. The plateau gate must not be claimed.

When fallback is used, write a short `FAMILY_ROUTING` note in the design state directory or
implementation handoff:

```text
preferred: Claude design -> Codex code -> Claude review -> Codex fixes
actual: <what ran>
missing: <family/phase/reason>
degraded_until: <what must run before ship>
```

## The loop (one pass per task)

```
[0] SCOPE      one task. State the invariant that must not break.
[1] PLAN       design doc, preferably Claude-led for hard planning, written LOCALLY into
               docs/designs/<NAME>.md.
[2] DUAL-MAGI  loop dual-magi-review on the doc until PLATEAU.
   ↻           each round: revise with findings, re-review. The cross-family (Claude)
               round is MANDATORY before any plateau claim, and the gate script — not
               you — decides whether plateau was reached.
[3] CODE       implement the plateau'd design. Prefer Codex for repo-local coding. Repo-baked,
               idempotent, reversible
               (backup-first for canonical writes), schema-grounded.
[4] BUG-HUNT   adversarial review of the IMPLEMENTATION, not the design:
                 scripts/magi_fanout_codex.sh <target> <round> <dir> --persona-set bug-hunt
               Reviewers RUN read-only verification against real data and try to break it.
               Fix findings; re-run until clean. This is the gate before an irreversible run.
[5] CODE-REVIEW on the final diff. Prefer Claude for design-intent/adversarial review,
               then Codex for final fixes/tests. Commit only when requested or policy allows.
[6] NEXT       update the epic; pick the next task; back to [0].
```

## Gates that block the irreversible step

Never cross these without the preceding review:

- **canonical / bulk DML, schema swap** → bug-hunt [4] on the migration script first; backup taken;
  a *programmatic* gate inside the script (coverage / residual / invariants), not just `--confirm`.
  A `--confirm` flag is cleared by an agent on bad state; a coverage assert is not.
- **public launch / no-auth endpoint** → adversarial bug-hunt for injection, DoS, rate-limit,
  resource exhaustion before DNS cutover.
- **scoring / ranking algorithm** → numerical-rigor plus adversarial-precision review before it
  touches users. A false merge conflates real entities; a non-converged iteration emits garbage.

## Plateau is not yours to declare

Gate [2] ends when `scripts/magi_plateau_gate.sh` writes a marker, which it does only if it can
mechanically confirm a cross-family round ran **against the current revision** of the doc (asserts
G1..G7 — see the `dual-magi-review` skill). Same-family agreement is never plateau: in this repo's
own field data, three Claude reviewers reached consensus on a design that one cross-family round
then REJECTED with five new criticals, two of which were literally unimplementable as written.

If the gate exits non-zero, you are not at plateau. Do not proceed to [3].

## Schema-grounding mandate

Every reviewer verifies table/column existence, populate state, and existing-code behavior against
reality (`psql \d`, `rg migrations/ core/`, real `SELECT count(*)`) and emits the commands it ran.
A round whose reviewers only read prose is **degraded** regardless of its verdict — re-run it. Any
doc-vs-reality drift is a CRITICAL finding: the design's SQL, column, or flag premise is imaginary.

Honest limit: this detects omission and inconsistency, not semantic truth. A reviewer that runs one
command and invents its conclusions will pass.

## Perspectives (orthogonal; adapt per task)

Defaults for a data/migration task:
- **algorithm / numerical rigor** — correctness, convergence, scale, the load-bearing premise.
- **adversarial precision / data-loss** — try to break the invariant on real data; find the concrete
  counterexample.
- **scale / feasibility / integration** — cost, timeouts, what else reads this, rollback, ROI.

For a product/launch task, swap in security-abuse and business/GTM lenses.

## Anti-patterns

| anti-pattern | why bad | instead |
|---|---|---|
| straight-to-code on a canonical change | ships the silent corruption | gate [2] then [4] |
| declaring plateau after same-family rounds | shared blind spots survive | cross-family round, verified by the gate |
| reviewers that don't run commands | self-reported grounding = hallucination passes | require `verify_commands_executed`; degrade empty rounds |
| `--confirm` as the only swap guard | an agent clears it on bad state | programmatic invariant gate inside the script |
| one mega design doc for a whole epic | un-reviewable, un-shippable | one task per loop |
| specifying a mechanism you have not verified exists | ships an unimplementable spec | probe the interface *before* writing it into the design |

That last row is not hypothetical: this plugin's own design specified a `--json-schema @file` flag
form and a transcript prompt-hash field. Neither exists. Both were caught by a cross-family round,
not by the three same-family reviewers who read the same text.

## Cost

Per task: design gate 2–5 dual-magi rounds, build varies, bug-hunt ~1 fan-out, code-review ~5 min.
A hard canonical task is a multi-hour loop. That is the point — it is cheaper than restoring
corrupted canonical data. A tiny diff does not need ultramagi.
