---
name: ultramagi
version: 0.1.0
description: >-
  End-to-end rigor loop for a non-trivial, hard-to-reverse change: local plan/design →
  dual-magi review to PLATEAU → code → dual-magi/bug-hunt on the implementation → code-review →
  next. A superset of dual-magi-review that drives the WHOLE lifecycle, not just one design
  round, and is meant to be invoked from inside a Workflow so multi-agent execution stays gated
  by adversarial, schema-grounded, cross-family review at every irreversible step.
  TRIGGER: "ultramagi", "本筋を片付ける", a canonical migration / scoring algorithm / public
  launch / data-loss-capable change, or any task the user wants designed→reviewed→built→reviewed
  rather than coded straight. NOT for small diffs (use /simplify) or a single design doc with no
  implementation (use dual-magi-review).
---

# ultramagi — the design→review→build→review→ship rigor loop

A "magi" is a panel of independent, perspective-orthogonal reviewers. **dual-magi** pairs
same-family reviewers with a cross-family (codex) reviewer to subtract shared training bias.
**ultramagi** wraps dual-magi around the ENTIRE change lifecycle: it gates BOTH the design (before
you write code) AND the implementation (before you ship), with schema-grounded, real-data
verification at each gate. It exists because heuristics and AI-drafted designs are biased toward
narrative coherence and skip literal verification — and the cheapest place to catch that is before
the irreversible step.

Field-proven (2026-06-02, PRS-LLM authors-dedup): dual-magi gates caught **3 separate
data-corruption / data-loss bugs** that would each have silently destroyed canonical data —
the design's core attribution premise (REJECTED on its own flagship example via mars queries),
the heuristic's 10–55% ambiguity (measured), and the migration script's silent 31%-edge drop
(caught before the swap). Straight-to-code would have shipped all three.

## The loop (one pass per task; the task list is usually a gh epic)

```
[0] SCOPE      one task from the epic/plan. State the invariant that must not break.
[1] PLAN       local design doc (Plan agent or hand-write). GitHub transport for Plan is
               unreliable → plan LOCALLY into docs/designs/<NAME>.md.
[2] DUAL-MAGI  loop dual-magi-review on the doc until PLATEAU (see definition). N rounds.
   ↻           each round: revise the doc with findings, re-review. Cross-family (codex) round
               is MANDATORY before any plateau claim.
[3] CODE       implement the plateau'd design. Scripts repo-baked, idempotent, reversible
               (backup-first for canonical writes), schema-grounded.
[4] BUG-HUNT   dual-magi / adversarial review of the IMPLEMENTATION (not the design): a
               Workflow of parallel reviewers that RUN read-only verification against real data
               and try to break it. Fix findings; re-run until clean. This is the gate before
               an irreversible run (swap, deploy, bulk DML, publish).
[5] CODE-REVIEW /code-review (or /simplify for quality-only) on the final diff. Commit.
[6] NEXT       update the epic checkboxes; pick the next task; back to [0].
```

## Plateau definition (when [2] stops)

A design is at plateau ONLY when **all** hold:
1. No `REJECT` verdict in the latest round.
2. New-vs-prior findings ratio < ~20% (the round is mostly re-confirming, not discovering).
3. A **cross-family (codex) round** has run on the current revision with a non-blocking verdict
   (`GO` / `GO-WITH-REVISE` whose revisions are minor). Same-family Claude CONFIRM is **never**
   plateau (gh #195: 4 Claude CONFIRM rounds → codex 1 round = REJECT + 6 new criticals).
4. Every load-bearing claim is schema-grounded (verified against the live DB / code, not prose).

If a round surfaces NEW criticals (even at `GO-WITH-REVISE`), it is **not** plateau — revise and
re-review. Productive rounds (new findings) mean keep going; a round that only re-states prior
findings means ship.

## Gates that block the irreversible step

ultramagi's whole value is the gate placement. Never cross these without the preceding review:
- **canonical / bulk DML, schema swap** → bug-hunt ([4]) on the migration script first; backup
  ([真田]) taken; a programmatic gate inside the script (coverage / residual / invariants), not
  just `--confirm`. (dedup lesson: a reviewer found the swap silently dropped 31% of edges.)
- **public launch / no-auth endpoint** → adversarial bug-hunt for injection / DoS / rate-limit /
  XSS / resource-exhaustion before DNS cutover.
- **scoring / ranking algorithm** → numerical-rigor + adversarial-precision review (a false
  merge conflates real entities; a non-converged PageRank emits garbage) before it touches users.

## Schema-grounding mandate (inherited from dual-magi-review v0.5)

Every reviewer MUST verify table/column existence, populate state, and existing-code behavior
against reality (psql `\d`, `grep migrations/ core/`, real `SELECT count(*)`), and emit the
commands it ran (`verify_commands_executed`). A round whose reviewers only read prose, with no
targeted verification, is graded **degraded** regardless of its stated verdict — re-run it. Any
doc-vs-reality drift = a CRITICAL finding (the design's SQL / column / flag premise is imaginary).

## Perspectives (orthogonal; adapt per task)

Pick 3 that cut the task differently. Defaults for a data/migration task:
- **algorithm / numerical rigor** — correctness, convergence, scale, the load-bearing premise.
- **adversarial precision / data-loss** — try to break the invariant on real data (find the
  concrete counterexample, e.g. "Rachel shares MORE co-authors than the correct fragment").
- **scale / feasibility / integration** — cost, timeouts, what else reads this, rollback, ROI.
For a product/launch task swap in security-abuse and business/GTM lenses.

## Invocation

Drive it from the main loop, or from inside a Workflow:

- **Design gate** → invoke the `dual-magi-review` skill (it runs Claude×3 + codex per round and
  synthesizes findings). Loop it (one invocation per round) until plateau.
- **Build** → write the repo-baked, backup-first, gated scripts.
- **Implementation gate (bug-hunt)** → a Workflow with `parallel()` of 3–5 adversarial reviewer
  agents (each with a `schema`-typed findings return) that RUN read-only verification and return
  structured findings; fix + re-run until clean. (This is the dedup-script-review pattern.)
- **code-review** → the `/code-review` skill (or `/simplify` for quality-only).

When the user runs a **Workflow** for multi-agent execution, ultramagi is the contract that each
phase that crosses an irreversible boundary is preceded by a dual-magi/bug-hunt phase in the same
workflow — i.e. the workflow's phase graph is `design → review → build → bug-hunt → swap/deploy`,
never `build → swap` directly.

## Anti-patterns

| anti-pattern | why bad | instead |
|---|---|---|
| straight-to-code on a canonical change | ships the silent corruption | gate [2] then [4] |
| declaring plateau after Claude-only rounds | same-family blind spots survive | codex round mandatory |
| reviewers that don't run psql/grep | self-reported grounding = hallucination passes | `verify_commands_executed`, degrade empty rounds |
| `--confirm` as the only swap guard | an agent / operator clears it on bad state | programmatic coverage/residual/invariant gate in the script |
| one mega design doc for the whole epic | un-reviewable, un-shippable | one task per loop, epic tracks the list |
| `--apply`/auto-mutate by default | overwrites work, hides drift | review-only default; mutation opt-in |

## Cost / cadence

Per task: design gate ~2–4 dual-magi rounds (each ~10–20 min Claude + ~10 min codex), build
varies, bug-hunt ~1 workflow (~10 min), code-review ~5 min. A hard canonical task is a multi-hour
loop — that is the point; it is cheaper than restoring corrupted canonical data. Scope to the
task's blast radius: a tiny diff doesn't need ultramagi (use /simplify); a 436K-row author dedup
or a public launch does.

## Related

- `dual-magi-review` — the single design-review round ultramagi loops in gate [2].
- `Workflow` tool — runs the parallel bug-hunt ([4]) and multi-agent build; ultramagi is the
  gate-placement contract over it.
- `/code-review`, `/simplify` — gate [5].
- memory: `feedback_dual_magi_mandatory_for_scripts`, `feedback_magi_preflight_for_major_updates`,
  `feedback_harness_structural_primary`, `feedback_design_doc_schema_grounding_required`.

## Revision history
| date | version | change |
|---|---|---|
| 2026-06-02 | 0.1.0 | Initial — codifies the design→dual-magi→code→bug-hunt→code-review loop proven on the PRS-LLM authors-dedup (caught 3 data-corruption bugs at the gates). |
