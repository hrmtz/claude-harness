# Documentation Index

## Start here if you are picking up work

| Doc | Topic |
|---|---|
| [`handouts/2026-07-28-orchestrated-day.md`](handouts/2026-07-28-orchestrated-day.md) | 統合オーケストレーター体制の初日: merge した 10 PR、#218 と #235 の測定契約 (窓を開ける前に固定した閾値)、#233 の設計 5 round と slice の現在地、実測が設計を上書きした 3 箇所、起票した構造欠陥 (#239 / #242 / #266)、運用の罠。**まずこれを読む** |
| [`handouts/2026-07-27-next-session.md`](handouts/2026-07-27-next-session.md) | Ordering and rationale for the open work across claude-harness and hippocampus-mcp after v1.13.2, plus the traps that produced it. Read before starting anything hook-, formation-, or retrieval-related. |

Handouts are written at the close of a session and are accurate as of that
moment. Re-measure anything they claim about live machine state.

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
| [`designs/REVIEW_FLOW_PORT.md`](designs/REVIEW_FLOW_PORT.md) | review flow 移植 epic (#233)。global invariants と slice 間の依存。deep design は置かない — 実装計画は下の 4 slice doc が SoT |
| [`designs/REVIEW_FLOW_PORT/01-sedimentation.md`](designs/REVIEW_FLOW_PORT/01-sedimentation.md) | slice ③: magi findings を PG に収穫し再発指摘を昇格候補として提示。dedup key と recurrence key の分離、fail-closed telemetry |
| [`designs/REVIEW_FLOW_PORT/02-grill.md`](designs/REVIEW_FLOW_PORT/02-grill.md) | slice ①: ultramagi Phase 0。設計を書く前に premise の実在を確認し、AI が答えられないことだけを ≤3 問で聞く |
| [`designs/REVIEW_FLOW_PORT/03-babysit-pr.md`](designs/REVIEW_FLOW_PORT/03-babysit-pr.md) | slice ④: PR green 化 loop。reply-only / 複合 green predicate / path 制限 / PUBLIC gate / 待機上限 |
| [`designs/REVIEW_FLOW_PORT/04-freerange.md`](designs/REVIEW_FLOW_PORT/04-freerange.md) | slice ②: 指示なし探索 reviewer (4 体目)。停止判定は散文 gate の管轄で、機械 gate には配線しない |

## Case studies

| Doc | Topic |
|---|---|
| [`INCIDENT_23H_HNSW.md`](INCIDENT_23H_HNSW.md) | 23-hour sunk-cost loss on a 165M-row HNSW build that motivated `harness-rails` |

## Release notes

| Doc | Topic |
|---|---|
| [`releases/v1.0.0.md`](releases/v1.0.0.md) | Public-safe hardening release: opt-in incident automation, genericized paths, legacy-state compatibility |
| [`releases/v1.0.0_ja.md`](releases/v1.0.0_ja.md) | 上記の日本語版 |
| [`releases/v1.7.0.md`](releases/v1.7.0.md) | harness-kimi native hook port (#54): Kimi >= 0.28 `[[hooks]]` wiring, BASH_ENV layer deprecated |
| [`releases/v1.7.0_ja.md`](releases/v1.7.0_ja.md) | 上記の日本語版 |

## Quick reference: which plugin for which situation

- Credential leaks, dangerous bash patterns: **harness-core**
- About to commit to ≥ 2h walltime / ≥ $10 / non-reversible change: **harness-magi**
- Long-running operation in flight, want to know if it's diverging from plan: **harness-rails**
- Long-running operation in planning, want to know if it'll fit RAM: **harness-rails preflight CLI**

## External

- Repository: <https://github.com/hrmtz/claude-harness>
- Codex native plugin install and migration: [`codex_plugins.md`](codex_plugins.md)
- Peer workers: `harness-formation` plugin (`formation` skill + CLI)
- Issue tracker: <https://github.com/hrmtz/claude-harness/issues>
