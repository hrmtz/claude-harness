# Documentation Index

## Plugin docs

| Doc | Topic |
|---|---|
| [`../plugins/harness-core/README.md`](../plugins/harness-core/README.md) | Three defense-in-depth hooks (credential scrub, bash guard, admission reminder) |
| [`../plugins/harness-magi/README.md`](../plugins/harness-magi/README.md) | Three-perspective preflight review skill (MELCHIOR/BALTHASAR/CASPAR) |
| [`../plugins/harness-rails/README.md`](../plugins/harness-rails/README.md) | Operational safety rails for long-running operations |

## Design / philosophy

| Doc | Topic |
|---|---|
| [`CLAUDE_HARNESS_DISTILLED.md`](CLAUDE_HARNESS_DISTILLED.md) | Full design rationale: 3-tier memory, persona stack, SOPS rules, incident timeline |
| [`PHILOSOPHY_RAIL_LEVELS.md`](PHILOSOPHY_RAIL_LEVELS.md) | The 4-level rail model (memory → CLAUDE.md → script → cron) and why it matters |

## Case studies

| Doc | Topic |
|---|---|
| [`INCIDENT_23H_HNSW.md`](INCIDENT_23H_HNSW.md) | 23-hour sunk-cost loss on a 165M-row HNSW build that motivated `harness-rails` |

## Quick reference: which plugin for which situation

- Credential leaks, dangerous bash patterns: **harness-core**
- About to commit to ≥ 2h walltime / ≥ $10 / non-reversible change: **harness-magi**
- Long-running operation in flight, want to know if it's diverging from plan: **harness-rails**
- Long-running operation in planning, want to know if it'll fit RAM: **harness-rails preflight CLI**

## External

- Repository: <https://github.com/hrmtz/claude-harness>
- Companion (long-running tmux peer workers): <https://github.com/hrmtz/njslyr7>
- Issue tracker: <https://github.com/hrmtz/claude-harness/issues>
