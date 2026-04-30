# harness-magi

Three-perspective preflight review for Claude Code. Surfaces architectural / operational / commercial blind spots **before** you commit walltime, cost, or destructive state — front-loading the *"what if you DROP→rebuild instead?"* conversation that otherwise happens mid-execution as an expensive course correction.

## Why "Magi"?

Named after Evangelion's Magi supercomputer system: three personalities (Melchior, Balthasar, Caspar) decide together. Here, three parallel `Task` agents — each with a distinct perspective — independently review the change and surface divergent observations.

- **MELCHIOR** (technical): silent failure modes, per-unit cost reality, alternative idioms
- **BALTHASAR** (operational): recovery cost, monitoring blind spots, peak resource envelope, concurrent-task collisions
- **CASPAR** (commercial): walltime / cost vs alternative, ROI-driven pivots, pre-commit cut lines

Convergent findings (all three flagged) carry the most weight. Divergent (single-persona) findings deserve a second look but may be persona-specific noise.

## When to invoke

Trigger on **any one** of:

- Walltime ≥ 2h
- ≥ 100M row DML
- Non-reversible / 6h+ rollback
- New layer / pipeline / service
- ≥ $10 confirmed spend
- Single script with > 1h sleep / poll loops

**Skip** for debug, fix, 1-line edits, ad-hoc queries, doc / memory changes, and changes already approved by a prior Magi pass.

## Install

```bash
# in Claude Code
/plugin marketplace add github:hrmtz/claude-harness
/plugin install harness-magi@claude-harness
```

The skill becomes available as `magi` and triggers on the conditions documented in `skills/magi/SKILL.md`.

## What's inside

```
skills/magi/
├── SKILL.md                          protocol + trigger conditions + output format
└── templates/
    ├── melchior_prompt.md            technical persona prompt
    ├── balthasar_prompt.md           operational persona prompt
    └── caspar_prompt.md              commercial persona prompt
```

The persona prompts are kept in `templates/` so you can review and adapt them. Each is ~80 lines, ~600-900 word output target.

## Output

A single markdown synthesis:

```
# Magi pre-flight: <change name>

## Trigger that fired
- <which threshold(s)>

## Persona summaries
### MELCHIOR (technical)
### BALTHASAR (operational)
### CASPAR (commercial)

## Synthesis
**Convergent**: ...
**Divergent**: ...

## Verdict
PROCEED / PIVOT / ABORT — <one-line reason>

## Next action
<concrete step>
```

Save under `docs/magi/<YYYYMMDD>_<change-slug>.md` for written ADR-style trail, or stay in chat for lightweight reviews.

## Anti-patterns

- **"I already thought about it"** — that's exactly when blind spots hide. Confidence correlates poorly with completeness.
- **Single agent doing all three perspectives** — defeats the point. *Independence* is what surfaces divergent observations.
- **Running Magi after starting** — sunk cost biases the synthesis. Pre-flight only.
- **Magi for trivial changes** — don't dilute the protocol.

## Related

- `formation` (from [njslyr7](https://github.com/hrmtz/njslyr7)) — long-running peer pane workers; complements Magi for changes needing both pre-flight AND multi-hour observability
- `harness-core` (this marketplace) — defense-in-depth runtime hooks for credential / command safety
