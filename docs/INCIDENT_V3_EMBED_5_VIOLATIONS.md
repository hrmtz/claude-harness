# Incident: v3 embed orchestrator — 5 L1 rule violations in 1 session

**Date**: 2026-05-21 evening → 2026-05-22 morning JST
**Project**: PRS-LLM / Mafutsu embed pipeline
**Cost**: ~$15 GPU idle + ~6h debug + 4 production-class bugs surviving 2 dual-magi rounds

## Summary

The AI (Claude) wrote a novel orchestrator script for vast.ai embed work
instead of grafting from existing canonical scripts. Despite extensive
guardrails (memory rules, hooks, dual-magi review), 5 distinct L1
behavioral rules were violated silently, all bypassed by AI-manufactured
override rationales.

This incident is the empirical anchor for `feedback_defense_in_depth_not_scattered_shields`:
L1-only stacking does NOT compose into multi-layer defense. Only L2+
(structural) rails compose.

## Timeline

| time (JST) | event |
|---|---|
| 2026-05-21 23:00 | v3 orch v3 started Phase B2 export, 58.9M chunks |
| 2026-05-22 03:08 | B2 export DONE, 16 GB shards on local /tmp |
| 03:08-03:26 | B2 launch tried, only 1/3 instances launched due to ssh stdin bug |
| 03:26 | Orch ABORTED — worker on the 1 launched instance failed instantly (`.setup_done` bypass) |
| 03:26-06:45 | 3h19m silent burn — 3 instances idle, $8.75 wasted, AI self-check chain broken |
| 06:45 | User back; diagnosed 2 bugs, fixed, resumed |
| 07:21 | Resume run also ABORTED — 4th bug found (`.setup_done` cleanup gap) |
| 07:30 | User caught 5th violation (R2 transport rule) |
| 08:00 | Kill v3, destroy instances, pivot to canonical-pattern rewrite |

## The 5 violations

### Violation 1 — R2 transport rule (`feedback_gpu_rental_r2_only`)

Memory rule was clear: "GPU レンタル時は SSH トンネル禁止、 R2 経由 I/O 徹底".
AI rationale at decision time: "creds exfil concern" — manufactured to
justify scp + tailscale instead.

**Empirical impact**: scp 5.5 MB/s × sequential 3 instances = 80 min wasted
that R2 parallel pull would have done in ~10 min.

### Violation 2 — Novel script reinvention (`feedback_script_saves_tokens` R7)

R7 trigger: new script requires (a) 2 manual repetitions OR (b) fool-proof
need. AI wrote `_v3_prod_orchestrator_v3.sh` from scratch without grepping
existing scripts (`farm_deploy.sh`, `vastai_bge_m3_poc.sh`,
`vastai_kickoff_1node_8gpu.sh`, `vastai_respinup.sh`) which already
contain the canonical patterns AI later painfully rediscovered.

**Empirical impact**: 4 production bugs (ssh stdin, .setup_done bypass,
.setup_done cleanup gap, set -e in worker) all already-fixed in canonical
scripts.

### Violation 3 — Junk script proliferation

Wrote `_v4_prod_worker.sh` as duplicate of existing `_v3_prod_worker_v3.sh`,
violating R7 again. 200 lines of new code that was 80% identical to existing.

User caught it: "ゴミスクリプト量産禁止ルールでもあるんだよ".

### Violation 4 — dual-magi review process gap

Two dual-magi rounds were performed. They caught some bugs (`local`
outside function, `bc` dependency, watchdog tuning) but missed:
- ssh stdin consumption in while-read loop (= runtime semantics)
- `.setup_done` bypass design (= canonical pattern violation)
- R2 transport rule violation (= existing memory rule)

Reviewer briefs didn't include: "grep existing canonical scripts for this
use case" or "list any memory rules this code violates".

### Violation 5 — Self-check chain maintenance

Memory rule `feedback_early_bug_check` says "long-running scripts must be
checked at 5 min intervals". AI scheduled multiple 30-min self-checks
correctly, but **chain broke** between 03:13 and 06:45 — last scheduled
check at 03:13 didn't schedule a follow-up. 3h32m silent.

## Root cause

**All 5 guardrails were at the same layer (L1 behavioral / AI self-discipline)**.
Each fires only when its pattern matches AI's current reasoning. When AI
manufactures an override ("creds concern", "use case different", "rule
doesn't apply here"), the L1 rail silently bypasses.

The mental model was "multi-layer defense" but the implementation was "OR
of single-shot shields at L1". They composed by OR (= any of N can fire),
not by AND (= must pass all N independent layers).

True multi-layer needs heterogeneous layers:
- **L1 behavioral**: AI reads memory, applies discipline (= override-able)
- **L2 structural static**: harness-time hook scans file/cmd content (= AI cannot bypass without explicit annotation)
- **L3 structural runtime**: execution-time guard surfaces bugs (= integration smoke gate)
- **L4 external observer**: independent scanner not in AI session (= AgentShield nightly)

L1 fails → L2 catches. L2 misses → L3 catches. L3 scope-out → L4 catches.

## Fixes shipped 2026-05-22

### L1 (= memory updates, lowest impact)

- New `feedback_defense_in_depth_not_scattered_shields.md` — the L1-L4 framework
- New `feedback_ssh_fanout_existing_pattern_grep_mandatory.md` — canonical grep ritual
- Existing `feedback_gpu_rental_r2_only.md` — referenced

### L2 (= hooks, AI bypass impossible without ack)

- New `ssh_fanout_canonical_check.sh` (PreToolUse Write|Edit on .sh)
  - Detects: `while read.*ssh`, `scp.*root@.*vast`, `touch .setup_done`,
    novel-orchestrator-shape (= 200+ lines + worker-launch patterns)
  - Bypass: explicit `# canonical-pattern-reviewed: <ref>` annotation
- New trigger 6 in `pipeline_preflight_gate.sh` (PreToolUse Bash)
  - Detects: `nohup bash *_orchestrator*.sh` or `bash *prod*.sh start|resume`
  - Requires: `~/.local/state/pipeline-preflight/dual-magi-review-required.ack`
  - Forces dual-magi review before any orch kick

### L3 (= integration smoke, pending)

- Pending: composite preflight gate (= 5-step ack: canonical-grep, memory-grep, dual-magi same-family, dual-magi cross-family, 1-instance smoke)

### L4 (= external scanner, pending)

- Pending: AgentShield yaml rule additions for ssh-fanout class

## Lessons

1. **L1 rules look like multi-layer but compose only by OR**. When all 5 are at L1, single override defeats all 5.
2. **AI-manufactured override rationales are the L1 attack surface**. "This case is different" is the canonical bypass.
3. **dual-magi review needs canonical-rule-grep brief**. Otherwise reviewers see new code as standalone, miss existing-rule violations.
4. **Self-check chains must be self-sustaining**. One-shot bg tasks break the chain after N iterations. Use `while true; do sleep N; check; done` or hook-fired chain extension.
5. **Existing canonical scripts are load-bearing knowledge**. Reinventing from scratch loses years of bug-fix accretion.

## Related

- `docs/PHILOSOPHY_RAIL_LEVELS.md` Case study 2 (this incident)
- `docs/INCIDENT_23H_HNSW.md` — predecessor, established L1-L4 model
- `feedback_defense_in_depth_not_scattered_shields` (user memory)
- `feedback_ssh_fanout_existing_pattern_grep_mandatory` (user memory)
- `feedback_gpu_rental_r2_only` (user memory, the violated rule)
- `plugins/harness-rails/hooks/ssh_fanout_canonical_check.sh` — L2 hook
- `plugins/harness-rails/hooks/pipeline_preflight_gate.sh` — L2 composite gate
