---
name: ultramagi
description: End-to-end rigor loop for a non-trivial change, orchestrated from Codex - classify reversibility, start bounded reversible work once ready-to-drive, and require dual-magi plus implementation review before every irreversible boundary. A superset of dual-magi-review that gates the WHOLE lifecycle rather than a single design round. TRIGGER, ultramagi, or a canonical migration, scoring algorithm, public launch, or data-loss-capable change. Not for small diffs, and not for a design doc with no implementation, use dual-magi-review for that.
---

# ultramagi (Codex orchestrator, Claude/Grok cross-family)

All `scripts/...` and `schemas/...` references are relative to the installed
`harness-magi-codex` plugin root (two directories above this `SKILL.md`).
Resolve that absolute plugin root before invoking a bundled script.

**dual-magi** pairs same-family reviewers with a cross-family reviewer to subtract shared training
bias. **ultramagi** wraps dual-magi around the *entire* change lifecycle: it gates both design and
implementation before an irreversible boundary, with schema-grounded verification at each gate.
Bounded reversible measurement/build/scaffolding may start earlier at the ready-to-drive checkpoint.
It exists because AI-drafted designs are biased toward narrative coherence and skip
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
the current chassis." If the next boundary is irreversible and a Claude worker is available,
handoff or spawn Claude for the design plateau. Reversible work may start at the ready-to-drive
checkpoint; exact-revision plateau remains mandatory before destructive/canonical mutation,
production cutover, or shipping.

### Fallback when a family is unavailable

"Unavailable" includes missing CLI, no active contract/subscription, model capacity, rate limit,
or a worker that cannot be spawned. Fallback is allowed, but it must be explicit in the design or
handoff notes and it must not erase the cross-family gate.

Fallback order:

1. **Claude unavailable during planning**: Codex may draft and revise the design locally, then use
   the explicit Grok cross-family fallback. Record the routing change. Do not proceed to an
   irreversible step until the selected provider passes the mechanical gate.
2. **Codex unavailable during coding**: Claude may implement only small, reversible scaffolding or
   tests. For migration/data-loss/security changes, stop before the irreversible step and queue a
   Codex implementation/review pass when available.
3. **Claude unavailable during implementation review**: run the final design-intent review through
   the explicit Grok fallback. Codex self-review alone remains "not final-reviewed".
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

## Epic admission gate (before [0])

Classify the request before drafting a design. Treat it as an **epic**, not one ultramagi task, if
any of these hold:

- it contains two or more outcomes that can be merged, verified, or rolled back independently;
- one invariant plus one acceptance-test/rollback pair cannot describe the whole change;
- it crosses ordered phases whose failure modes need separate gates; or
- the proposed design would contain multiple task-level implementation plans.

For an epic:

1. Create one parent Epic in the repository's native issue tracker before design review. Give it
   the global invariant, explicit non-goals, ordered dependency-aware slices, acceptance criteria,
   rollback boundaries, and a checkbox/status link for every slice. If no remote tracker is
   configured, create `docs/designs/<EPIC>-EPIC.md` with the same fields and record that the remote
   Epic is pending; do not silently collapse the work back into one task.
2. Split the work into the smallest **vertical, independently mergeable slices** that preserve the
   global invariant. Prefer one pull request and one observable acceptance test per slice. Create
   child issues only when separate ownership or scheduling needs them; otherwise the parent
   checklist is enough.
3. Keep the Epic document at coordination altitude: scope, dependencies, shared invariants,
   sequencing, and completion state. Put implementable detail in one local design doc per slice,
   normally `docs/designs/<EPIC>/<NN>-<SLICE>.md`.
4. Select only the first unblocked slice and run [0] through [5] on that slice. Never send the
   whole Epic or its umbrella document through the design plateau loop, and never implement later
   slices merely because they appear in the same plan.
5. After the slice passes its gates, update the Epic with evidence and discovered dependency
   changes, then select the next unblocked slice at [6].

If the request is already one independently mergeable slice, do not create ceremonial Epic
overhead.

## Reversibility briefing gate

Classify the next step, not the project label:

- **Heavy briefing:** destructive or hard-to-reverse work (`DROP`, migration/schema mutation,
  canonical/bulk DML, production cutover, public launch, ranking changes
  touching users). Complete the existing exact-revision design gate before that step.
- **Warmup track:** local measurement, build, tests, disposable scaffolding, or additive work in a
  non-canonical/staging target with an executable rollback check. Stop prose planning and start
  when the invariant, first bounded executable
  step, rollback/disposal path, and absence of a known CRITICAL/HIGH against that step are explicit.

Warmup is not plateau and never authorizes shipping or an irreversible action. Put open
implementation-detail questions into executable checks or the next-step reconnaissance ledger.
If evidence changes the invariant, invalidates rollback, or expands blast radius, return to heavy
briefing. Do not spend another design round perfecting machine-derivable detail.

During a background wait estimated at 30 minutes or more, inspect the next step's real branches,
constants, test lockstep, referenced objects, and role/index/env naming assumptions. Record drift
as next-step scope; do not fix it opportunistically while the current job runs.

## The loop (one pass per task)

```
[0] SCOPE      one independently mergeable task/slice. State the local and inherited epic
               invariants that must not break.
[1] PLAN       design doc, preferably Claude-led for hard planning, written LOCALLY into
               docs/designs/<NAME>.md. On the warmup track, stop at ready-to-drive.
[2] DUAL-MAGI  heavy track: run bounded dual-magi-review campaigns on the doc toward PLATEAU.
               Warmup track: review may overlap reversible execution, but exact-revision plateau
               remains mandatory before any irreversible boundary.
   ↻           each round: revise with findings, re-review up to the campaign guard. The cross-family (Claude)
               round, or explicit Grok fallback, is MANDATORY before any plateau claim; the gate — not
               you — decides whether plateau was reached.
[3] CODE       implement the plateau'd design, or only the bounded reversible step admitted by the
               warmup checkpoint. Prefer Codex for repo-local coding. Repo-baked,
               idempotent, reversible
               (backup-first for canonical writes), schema-grounded.
[4] BUG-HUNT   adversarial review of the IMPLEMENTATION, not the design:
                 python3 scripts/magi_review_packet.py --repo <absolute-root> --base <sha> \
                   --scope <id> --invariant <id> --deadline <RFC3339> --output <stable-manifest>
                 scripts/magi_fanout_codex.sh <target> <round> <dir> --persona-set bug-hunt \
                   --prior <prior-synthesis.json|->
               Reviewers RUN read-only verification against real data and try to break it.
               After each completed phase, run:
                 python3 scripts/magi_convergence_gate.py evaluate <implementation-manifest.json>
               Follow CONTINUE. On FINAL_REVIEW_REQUIRED / next_mode=final-full, run:
                 # Full fanout only: first synthesize every round-N persona artifact into the
                 # schema-valid round_N_codex.json prior required by round N+1.
                 scripts/magi_xfamily.sh --reviewer claude <manifest> <next-round> \
                   <state-dir/round_N_codex.json> <state-dir/round_N+1_xfamily>
               Then evaluate again. A clean final cycle returns BLOCKED with reason
               REPORT_ONLY_READY_FOR_EXISTING_PLATEAU_GATE: this is an evaluator-terminal
               handoff to magi_plateau_gate.sh, not permission to ship and not a hard blocker.
               Invoke the plateau gate only after mechanically matching both that decision and
               reason code; any CONTINUE result requires its named fix/review path instead.
               Every other BLOCKED reason and every REDESIGN are terminal fail-closed results.
               The evaluator is advisory/report-only: it never emits PASS or authorizes shipping.
               For a later standard-risk fix, rebuild the same packet with --allow-incremental.
               If the evaluator returns next_mode=incremental-fix, run:
                 scripts/magi_fanout_codex.sh <manifest> 1 <dir> \
                   --persona-set bug-hunt --review-mode incremental
               This is one deterministic targeted reviewer (weight 1), never a final certificate.
               Public-interface, trust-boundary, persistence/schema/rollback, design-invariant,
               >8-path, or >200-line fixes require full review; declare semantic surface changes
               with magi_review_packet.py --surface-change <kind>.
               At most two fanout/targeted -> xfamily cycles may run; never "re-run until clean".
               Existing exact-revision plateau + human judgment remain the ship authority.
[5] CODE-REVIEW on the final diff. Prefer Claude for design-intent/adversarial review,
               then Codex for final fixes/tests. Commit only when requested or policy allows.
[6] NEXT       update the epic with gate/test evidence; pick the next unblocked slice; back to [0].
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
G1..G9 — see the `dual-magi-review` skill). Same-family agreement is never plateau: in this repo's
own field data, three Claude reviewers reached consensus on a design that one cross-family round
then REJECTED with five new criticals, two of which were literally unimplementable as written.

If the gate exits non-zero, you are not at plateau. Do not cross an irreversible boundary or claim
shipping authority. A ready-to-drive warmup step may continue only while it remains bounded and
reversible.

On the heavy track, before gate [0], arm `scripts/magi_autorun.py arm <design-doc>` once. Its Stop hook owns
acknowledgement-free session continuation across the design loop and ends only at exact-revision
plateau or a definitive blocked state. Do not pause between phases for user acknowledgement.

If a reviewer script exits `4`, the autonomous campaign budget is exhausted. This is also not
plateau: autonomously reduce scope, replace the primitive, or record an explicit limitation, then
restart at round 1. A changed document/protocol rolls over automatically within the fixed global
allowance of 16 weighted model launches across all revision campaigns, without acknowledgement.
Fan-out admission preserves one weighted launch for its immediately following mandatory
cross-family review. Reserve denial is a definitive blocked state, never permission to ship; the
cross-family claim still passes the normal transition and budget guards and is not double-charged.
At global exhaustion, the current campaign is finished and remains non-plateau. The parent
orchestrator must not stop merely because the user is away. It must inspect the durable findings
and autonomously perform one material recovery action: split the slice into independently gated
sub-slices, narrow the review contract and acceptance conditions to the failing concern, replace
an invalid primitive, or record a limitation that removes the blocked claim from scope. Then start
round 1 of a new, explicitly named campaign with a fresh bounded allowance. Preserve the exhausted
campaign, findings, and linkage; changing only wording, revision hash, state directory, or reviewer
prompt is not a valid reset. Never reroll an unchanged claim until a model happens to say GO.

The parent irreversible boundary stays blocked until every required recovered sub-slice and their
integration gate pass. If no honest material decomposition or contract correction exists, emit a
definitive blocked result for that boundary. Do not pause for user acknowledgement. If the
ready-to-drive conditions still hold, continue reversible warmup and use executable evidence to
narrow the recovery campaign.

A cross-family claim must match the exact document and review-protocol identity of its successful
same-family source. After changing the target, restart at round 1 fan-out in a revision-scoped state
directory such as `.dual-magi/revisions/<artifact-sha-prefix>`. Existing round basenames belong to
the prior successful launch; the guard refuses reuse of their state directory across exact
revisions before accounting. Do not overwrite or relocate them. A legacy cross-revision failure
sequence can roll over only to the matching new revision's round 1 fan-out, with all failed
launches still charged and the global fuse unchanged.

If requirements change while an adapter owns a live claim, do not edit the target or relaunch
around it. Cancel that exact charged revision first:

```bash
TARGET="/absolute/path/to/active-review-target"
python3 scripts/magi_campaign_guard.py cancel-revision "$TARGET" \
  --expected-artifact-sha "$(sha256sum "$TARGET" | cut -d' ' -f1)" \
  --reason "requirements changed: <brief reason>"
```

Proceed only after the verified owner process tree is gone and the guard confirms cancellation.
A cleanup-blocked result remains terminal; retry the same cancellation instead of unlinking the
review lock or starting another adapter. Change the target content before replacement round 1:
a protocol-only change cannot restart a superseded revision.

## Schema-grounding mandate

Every reviewer verifies existing-code and file-backed claims with the adapter's read-only tools
and reports the operations it ran. A round whose reviewers only read prose is **degraded**
regardless of its verdict — re-run it. Any doc-vs-reality drift is a CRITICAL finding. Direct DB
and `psql` verification is out of scope until a credential-safe wrapper exists; do not put DSNs or
query credentials into prompts or findings.

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
| reviewing or implementing the umbrella Epic as one task | rigor amplifies scope instead of convergence | admit the Epic, then gate one mergeable slice at a time |
| specifying a mechanism you have not verified exists | ships an unimplementable spec | probe the interface *before* writing it into the design |

That last row is not hypothetical: this plugin's own design specified a `--json-schema @file` flag
form and a transcript prompt-hash field. Neither exists. Both were caught by a cross-family round,
not by the three same-family reviewers who read the same text.

## Cost

Per task: the default design campaign permits at most 12 weighted model launches (three pairs
without retries),
build varies, bug-hunt ~1 fan-out, code-review ~5 min.
A hard canonical task is a multi-hour loop. That is the point — it is cheaper than restoring
corrupted canonical data. A tiny diff does not need ultramagi.
