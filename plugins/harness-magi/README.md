# harness-magi

Contract mirror for a three-perspective preflight review. This Claude surface
documents the protocol but deliberately fails closed until a Claude-native
structural runner can prove reviewer independence and artifact provenance.
Use the `harness-magi-codex` companion for the working runner.

## Why "Magi"?

Named after Evangelion's Magi supercomputer system: three personalities
(Melchior, Balthasar, Caspar) decide together. The contract defines three
independent perspectives; prose-only `Task` dispatch is not accepted as proof
that they ran independently.

- **MELCHIOR** (technical): silent failure modes, per-unit cost reality, alternative idioms
- **BALTHASAR** (operational): recovery cost, monitoring blind spots, peak resource envelope, concurrent-task collisions
- **CASPAR** (commercial): walltime / cost vs alternative, ROI-driven pivots, pre-commit cut lines

Corroborated findings carry the most weight. Unsupported single-persona
findings remain explicit questions. A grounded minority CRITICAL, security,
data-loss, or irreversibility finding retains veto power.

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

The persona prompts define the three lanes. `references/review-contract.md`
defines the structured artifact consumed by the deterministic companion gate.

**Sibling skills in this plugin:** `dual-magi-review` (per-round design review), `ultramagi` (the full design→review→build→review→ship loop), `bug-hunt` (adversarial review of an implementation diff), and `magi-battle` (red-vs-blue team battle that runs between plateau and ship — replaces the review convergence漸近 tail with a scored verdict on attack chains). See each skill's `SKILL.md`.

## Output

The deterministic gate requires a provider-specific structural runner. The
Codex companion currently ships that runner; this Claude surface remains
fail-closed until a Claude-native runner can produce truthful provenance.

## Anti-patterns

- **"I already thought about it"** — that's exactly when blind spots hide. Confidence correlates poorly with completeness.
- **Single agent doing all three perspectives** — defeats the point. *Independence* is what surfaces divergent observations.
- **Running Magi after starting** — sunk cost biases the synthesis. Pre-flight only.
- **Magi for trivial changes** — don't dilute the protocol.

## Related

- `formation` — long-running peer pane workers; complements Magi for changes needing both pre-flight AND multi-hour observability
- `harness-core` (this marketplace) — defense-in-depth runtime hooks for credential / command safety
