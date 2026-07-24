---
name: ultramagi
version: 0.4.0
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

Claude-orchestrated ultramagi should therefore keep design ownership until the design gate is
mechanically complete, then delegate coding to Codex when available. If the user asks for
"subagent Codex coding after design", enforce that no coding starts until the design plateau is
recorded.

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

## The loop (one pass per task; the task list is usually a gh epic)

```
[0] SCOPE      one task from the epic/plan. State the invariant that must not break.
[1] PLAN       local design doc, preferably Claude-led for hard planning. GitHub transport for Plan is
               unreliable → plan LOCALLY into docs/designs/<NAME>.md.
[2] DUAL-MAGI  loop dual-magi-review on the doc until PLATEAU (see definition). N rounds.
   ↻           each round: revise the doc with findings, re-review. Cross-family (codex) round
               is MANDATORY before any plateau claim.
[2b] BATTLE    (optional, high-stakes only) red-vs-blue team battle over the plateau'd design:
               red chains the DEFERRED/low findings into attack paths, blue defends from the
               design, a cross-family blinded judge scores proven holes. Replaces the plateau
               漸近 tail with a scored verdict. Patch RED_WINS holes, then continue. Skill:
               `magi-battle`. Skip for reversible / low-stakes tasks.
[3] CODE       implement the plateau'd design. Prefer Codex for repo-local coding. Scripts
               repo-baked, idempotent, reversible
               (backup-first for canonical writes), schema-grounded.
[4] BUG-HUNT   dual-magi / adversarial review of the IMPLEMENTATION (not the design): a
               Workflow of parallel reviewers that RUN read-only verification against real data
               and try to break it. Fix findings; re-run until clean. This is the gate before
               an irreversible run (swap, deploy, bulk DML, publish).
[5] CODE-REVIEW /code-review (or /simplify for quality-only) on the final diff. Prefer
               Claude for design-intent/adversarial review, then Codex for final fixes/tests.
               Commit only when requested or policy allows.
[6] NEXT       update the epic checkboxes; pick the next task; back to [0].
```

## Plateau definition (when [2] stops) — severity-gated (v0.2.0)

A design is at plateau ONLY when **all** hold:
1. No `REJECT` verdict in the latest round.
2. No NEW `CRITICAL`/`HIGH` finding **that breaks the stated invariant** in the latest round
   (cross-family included). MED/LOW/nit findings do **NOT** block plateau — they go to the
   deferred ledger (see § Convergence economics), not into another doc revision.
3. A **cross-family (codex) round** has run on the current revision with a non-blocking verdict
   (`GO` / `GO-WITH-REVISE` whose revisions are minor). Same-family Claude CONFIRM is **never**
   plateau (gh #195: 4 Claude CONFIRM rounds → codex 1 round = REJECT + 6 new criticals).
4. Every load-bearing claim is schema-grounded (verified against the live DB / code, not prose).

**Zero-findings is NOT a reachable state** with Fable-class reviewers — every round emits 3-7
findings indefinitely (field datum 2026-07-10, company-shared-hippocampus: 41 rounds, findings
never reached zero). Do not wait for it, and do not treat the old "new-vs-prior ratio < 20%"
as a requirement — with strong reviewers it never fires. The ratio remains a *signal*; the
severity gate above is the *criterion*.

If a round surfaces NEW criticals that break the invariant, it is **not** plateau — revise
(minimally, see churn rule) and re-review.

For guarded campaigns, fan-out admission preserves one weighted launch for its immediately
following mandatory cross-family review. Reserve denial is a definitive blocked state, never
permission to ship; the cross-family claim still passes the normal transition and budget guards
and is not double-charged.

## Convergence economics (v0.2.0 — token budget + altitude rails)

Rigor per round is worthless if the loop never terminates. Four rails, all learned from the
2026-07-10 41-round run:

- **Round budget.** Design gate [2] soft budget = **5 rounds**. Hitting it does not mean "keep
  grinding" — it triggers a mandatory **altitude checkpoint**: choose (a) ship the plateau'd
  core and defer the rest, (b) slice the doc into smaller per-task docs, or (c) descend — stop
  reviewing prose and start writing the code + executable checks that settle the open questions.
  **8 rounds = hard stop**; continuing requires explicit user sign-off with a stated reason.
- **Deferred ledger.** MED/LOW findings are recorded in `${doc_dir}/.dual-magi-<slug>/DEFERRED.md`
  with the gate that will resolve each ([4] bug-hunt / [5] code-review / a named executable
  check). The doc is NOT revised for them.
- **Revision churn rule.** Every doc revision is new review surface: the fix for round N
  routinely becomes round N+1's finding (field datum: the r34 grant fix itself was r35's
  CRITICAL doc-vs-reality drift). Therefore: revise only for REJECT/CRITICAL/HIGH, minimal
  span, no opportunistic rewriting; and re-review rounds after a revision are **diff-scoped** —
  reviewers get the diff + the invariant, and re-litigation of unchanged text is auto-dup.
- **Altitude rule (execution-derived, not text-derived).** If a finding class concerns
  enumerable implementation detail that a script can derive or verify (grant lists, SQL
  operators/opclasses, column lists, sequence privileges), the fix is NOT more prose — it is
  an **executable gate** in the build ([3]/[4]). A doc that keeps generating this finding class
  is written below design altitude (field datum: 3 straight rounds of grant whack-a-mole until
  a reviewer itself concluded "the grant list must be execution-derived, not text-derived").
  Design-doc altitude = invariants + interfaces + irreversibility strategy; enumerable detail
  belongs to code and checks.
- **Scope freeze.** A capability or section added *during* review is a new slice/task, never
  inline review material — mid-review scope growth is what re-opened CRITICAL space at r29
  after the doc had been at GO-WITH-REVISE since r13.

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

> **子 agent の model 固定（必須、memory `feedback_ultramagi_children_opus_max`）**: review/批評/verify
> 系の子は **`model: "opus"` を必ず明示**（Agent tool は `model:"opus"`、Workflow の `agent()` は
> `opts.model:"opus"`）。実装専任の子は `sonnet`。**省略すると親 model を継承する** — 親が **fable
> (Mythos-class) のとき子に fable がこぼれ、review 品質に寄与しないまま fable クォータを無駄食いする
> (fable は親 orchestrator のみ、子に使うのは禁止)**。opus 明示は親が opus/fable/sonnet のどれでも安全側。

> **Headroom-aware child tier（capacity-oracle、任意・fail-open）**: Claude は実運用で唯一
> subscription 枠を焼き切る family（Codex/Kimi はほぼ枯渇しない）。heavy な review/verify fan-out
> の前に Claude の live headroom を oracle に問う — `capacity-oracle substitute -q '.keep'`
> （CLI 不在なら fail-open で opus のまま）。`true` = Claude に余裕 → 上記どおり子は **opus**。
> `false` = Claude が offload floor 未満 = 熱い → 同一 family の review 子を **`model:"sonnet"`**
> に格下げして残枠を伸ばす（sonnet も**明示 tier** なので fable 継承 leak ではない）。子 tier を
> 下げても review 品質は multi-perspective 幅 + **必須 codex cross-family round** で担保される（=
> 上の opus-pin の論旨そのもの）。cross-family round は Codex（枯渇しない）なので Claude が熱いときこそ
> full weight で回す — 決して薄めない。cf. capacity-oracle-mcp#92 / docs/WIRING.md §3。

- **Design gate** → invoke the `dual-magi-review` skill (it runs Claude×3 + codex per round and
  synthesizes findings). Loop it (one invocation per round) until plateau.
- **Build** → write the repo-baked, backup-first, gated scripts.
- **Implementation gate (bug-hunt)** → a Workflow with `parallel()` of 3–5 adversarial reviewer
  agents (**each spawned with `opts.model: "opus"`** — 上記) that RUN read-only verification and
  return structured findings (`schema`-typed); fix + re-run until clean. (dedup-script-review pattern.)
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
| reviewing until zero findings | Fable-class reviewers never emit zero; loop runs away (41-round field datum) | severity-gated plateau + deferred ledger |
| revising the doc for MED/LOW every round | revision churn — each fix is new review surface | defer to gate [4]/[5] via DEFERRED.md |
| prose that enumerates machine-derivable detail (grants, opclasses, column lists) | whack-a-mole finding class; doc below design altitude | executable gate in the build; execution-derived, not text-derived |
| full-doc re-review after a small revision | re-litigates unchanged text, burns tokens | diff-scoped re-review with invariant attached |

## Cost / cadence

Per task: design gate ~2–4 dual-magi rounds (each ~10–20 min Claude + ~10 min codex), build
varies, bug-hunt ~1 workflow (~10 min), code-review ~5 min. A hard canonical task is a multi-hour
loop — that is the point; it is cheaper than restoring corrupted canonical data. Scope to the
task's blast radius: a tiny diff doesn't need ultramagi (use /simplify); a 436K-row author dedup
or a public launch does.

At each round's synthesis, report **round count vs budget + cumulative walltime** so runaway
loops are visible in-flight, not post-mortem (the 41-round run burned ~4.7h before anyone
counted).

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
| 2026-07-10 | 0.2.0 | **Convergence economics** — learned from the company-shared-hippocampus run (41 rounds, ~4.7h, findings never reached zero under Fable-class reviewers). Plateau redefined severity-gated (new CRITICAL/HIGH breaking the invariant blocks; MED/LOW → deferred ledger, never a doc revision). Added: round budget (soft 5 / hard 8 with user sign-off) + altitude checkpoint, revision churn rule (fix-minimal + diff-scoped re-review; the r34 fix was r35's CRITICAL), altitude rule (execution-derived not text-derived — enumerable detail goes to executable gates, not prose), scope freeze during review, per-round budget/walltime reporting. |
| 2026-07-21 | 0.4.0 | **Headroom-aware child tier (capacity-oracle #92 / claude-harness#97)** — before a heavy review/verify fan-out, consult `capacity-oracle substitute -q '.keep'` (fail-open if the CLI is absent). Claude is the only family that routinely exhausts its subscription; when it's below the offload floor, downgrade same-family review children opus→sonnet (still an explicit tier, never inherited fable) to stretch its budget — quality is carried by multi-perspective breadth + the mandatory Codex round, not child tier. Cross-family (Codex) round stays full-weight. |
| 2026-07-21 | 0.3.0 | **Drift reconciliation (#98)** — the live installed 0.2.0 (convergence economics) had never been committed to source, while source had independently gained the `Default family routing` section + `[2b] BATTLE` phase + flow routing hints. Merged both into a single canonical superset (installed 0.2.0 as base + source-only routing/battle content) and re-established source as SoT. No behavior removed from either side. |
