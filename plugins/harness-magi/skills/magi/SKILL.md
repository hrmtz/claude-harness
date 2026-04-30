---
name: magi
version: 0.1.0
description: |
  Three-perspective preflight review for high-stakes changes (long walltime,
  large DML, irreversible cutover, new infra layer, significant cost). Spawns
  three parallel Task agents — MELCHIOR (technical), BALTHASAR (operational),
  CASPAR (commercial) — and synthesizes their verdicts BEFORE the change
  begins, front-loading "post-hoc optimization" insights that otherwise
  surface mid-execution as expensive course corrections.

  USE WHEN any one trigger fires: walltime ≥ 2h, ≥ 100M row DML, hard-to-
  reverse change, new pipeline / service layer, ≥ $10 confirmed spend, single
  script with > 1h sleep / poll loops. SKIP for debug, fix, 1-line edit,
  ad-hoc query, doc / memory edits, or changes already approved by a prior
  Magi pass.
allowed-tools:
  - Task
  - Read
  - Write
  - Bash
  - Grep
  - Glob
---

# magi — three-perspective preflight review

Named after Evangelion's Magi supercomputer (three personalities decide
together). Here, three parallel Task agents with distinct perspectives surface
divergent observations in the planning phase — before you spend hours doing
the wrong thing.

## When to invoke

Trigger on **any one** of:

| Threshold | Why this matters |
|---|---|
| Walltime ≥ 2h | Manual babysitting impossible, failures detected late |
| ≥ 100M row DML | Index maintenance overhead, lock pressure, table bloat |
| Non-reversible / 6h+ rollback | Production cutover, destructive ops |
| New layer / pipeline / service | First-pass-adequate bias bites hard |
| ≥ $10 confirmed spend | GPU rental, API batch jobs, paid services |
| Single script > 1h with sleep / poll loops | Silent failure modes plentiful |

**Skip for**:
- Debug, fix, 1-line edit
- Ad-hoc query / read-only inspect
- Doc / memory / commit message
- Already approved by a prior Magi pass (cite the ADR / prior synthesis)

## Why it works

The "post-hoc optimization" failure mode: you start a 2-hour job, then
mid-run someone says *"what if you DROP→rebuild instead?"* — yielding a 7×
speedup. By that point you've already paid the slow path.

`magi` front-loads the same conversation. Three independent perspectives
bring divergent observations into planning *before* you commit walltime,
cost, lock pressure, or destructive state.

The personas are distinct on purpose:

- **MELCHIOR** thinks in cost-per-row, lock acquisition order, silent failure
  modes — sharp technical hazards that hide behind plausible defaults.
- **BALTHASAR** thinks in recovery cost, monitoring blind spots, peak resource
  envelope — operational pain you only notice when something breaks.
- **CASPAR** thinks in alternative paths, sunk-cost cut lines, ROI-driven
  pivots — *"do we even need this?"* reframings that change scope.

**Convergent findings** (all three flag the same concern) carry the most
weight. **Divergent findings** (one persona only) deserve a second look but
may be persona-specific noise.

## Protocol

### 1. Brief the change

Compose a concise description (≤ 200 lines) covering:

- What the change does (one paragraph)
- Why it's happening (driver, deadline, dependency)
- Estimated walltime, cost, lock / disk / memory peaks
- Reversibility (rollback path + estimated cost)
- Concurrent tasks that may collide

Save the brief at `docs/magi/<YYYYMMDD>_<change-slug>_brief.md` if a written
trail is wanted, otherwise keep it in the chat.

### 2. Round 1 — three Task agents in parallel

Spawn three `Task` calls **in a single assistant turn** (so they run
concurrently, not sequentially). Each agent receives:

1. The change brief
2. Its persona prompt template (under `templates/`)

Persona templates in this skill:

- `templates/melchior_prompt.md` — technical
- `templates/balthasar_prompt.md` — operational
- `templates/caspar_prompt.md` — commercial

Each persona returns a 600 - 900 word report.

### 3. Synthesize

Read all three reports. Produce a synthesis with:

- **Convergent concerns** (all three flagged → high confidence)
- **Persona-specific concerns** (single flagged → review for persona bias)
- **Suggested mitigations** / alternative paths
- **Verdict**: PROCEED / PIVOT / ABORT
- If **PIVOT**: propose the alternative; if non-trivial, re-run Round 1 on
  the new plan

### 4. Round 2+ (optional, strategic-layer only)

Reserved for architecture-class decisions where Round 1 is inconclusive or
the change touches multiple layers. Re-prompt each persona with refined
context based on Round 1 findings — ADR-style depth.

**Most pre-flights end at Round 1.**

## Output format

A single markdown document:

```
# Magi pre-flight: <change name>

## Trigger that fired
- <which threshold(s)>

## Persona summaries

### MELCHIOR (technical)
<summary>

### BALTHASAR (operational)
<summary>

### CASPAR (commercial)
<summary>

## Synthesis

**Convergent**: <concerns flagged by 2+ personas>

**Divergent**: <single-persona concerns worth a second look>

## Verdict
**PROCEED** / **PIVOT** / **ABORT** — <one-line reason>

## Next action
<concrete step>
```

Save to `docs/magi/<YYYYMMDD>_<change-slug>.md` if a written record is wanted.
Otherwise stay in chat.

## Anti-patterns

- **Skipping for "I already thought about it"** — that's exactly when blind
  spots hide. The thresholds exist because confidence correlates poorly with
  completeness.
- **Single agent doing all three perspectives** — defeats the point. The
  *independence* is what surfaces divergent observations.
- **Running Magi after starting the change** — sunk-cost biases the
  synthesis. Pre-flight only.
- **Over-trusting any one persona** — each has its own bias. Synthesis is
  where signal emerges from triangulation.
- **Magi for trivial changes** — debug / fix / 1-line edits don't need this.
  Don't dilute the protocol.

## Related

- `formation` (njslyr7) — long-running peer pane workers; complements Magi
  for changes needing both pre-flight AND multi-hour observability
- `code-review`, `pr-review-toolkit` — *post-change* review; Magi is the
  pre-change counterpart
