# Philosophy → Rail: 4-Level Model

A reflection on why `claude-harness` exists. Written after a 23h sunk-cost
incident exposed a gap in the original "memory + behavioral guidance"
approach.

## The 4 levels

```
level 1   memory.md / CLAUDE.md note      "be careful about X"
                ↓ reread on task start, easy to forget
level 2   protocol step in CLAUDE.md      "in step N, check X"
                ↓ fires only when that protocol is invoked
level 3   inline check in script          if (X is true) { warn() }
                ↓ fires every time the script runs
level 4   external rail (cron / hook)     watcher pings the script's state
                ↓ fires even if the script itself is silent / dead
```

Each level catches more failure modes than the previous, at higher
implementation cost. The mistake is to stop at level 1 thinking "I wrote
it down, so I'll remember."

## What goes wrong at each level

### Level 1 (memory only) — silent failures

Memory is read at session start, then the agent moves on. If the relevant
memory entry doesn't surface during planning (no keyword trigger, no
re-read), the principle doesn't fire. Long-running operations are
particularly vulnerable: by hour 8, the agent has forgotten the principle
written 8 hours ago.

**Failure example**: `feedback_early_detect_bleeding` was in memory; the
agent wrote it themselves. But during a 23h HNSW build, no automated step
re-read it. So early detection didn't happen.

### Level 2 (CLAUDE.md protocol step) — protocol gaps

Adding "in step N, check X" to CLAUDE.md works *if* step N is reliably
invoked. But many failure modes don't have a corresponding protocol step.
The 23h incident wasn't blocked by any documented protocol because there
wasn't one for "long-running build is taking too long."

### Level 3 (script inline check) — script silence

In-script checks fire reliably while the script runs. But if the script
hangs, crashes silently, or is killed without trace, the check doesn't
fire. For long-running operations (where "hung" is the most common
failure mode), an inline check is necessary but not sufficient.

### Level 4 (external rail) — independent observer

A separate process (cron job, daemon, watcher) observes the operation
from outside. Even if the operation hangs, the observer keeps running and
notices the silence. Even if the operation lies to itself, the observer
queries authoritative state (PG `pg_stat_progress_*`, log file mtime,
etc.).

## Mapping claude-harness plugins to levels

| Plugin | Levels covered |
|---|---|
| **harness-core** hooks | Level 4 (external observer of every Bash call) |
| **harness-magi** skill | Level 2 (protocol step: "before high-stakes change, run MAGI") |
| **harness-rails** preflight | Level 3 (script inline: `safety-rails-preflight ... \|\| exit 2`) |
| **harness-rails** watcher | Level 4 (cron observer of long-running op heartbeats) |

## When to use which

- Casual reminder, low cost: Level 1 (just write it in memory).
- Recurring task with known structure: Level 2 (CLAUDE.md protocol).
- Anything that takes > 5min to run: Level 3 (inline check).
- Anything that takes > 1h to run, or runs unattended: Level 4 (external rail).

The cost gradient is real. Level 4 requires installing a watcher,
configuring cron, agreeing on a heartbeat schema. But for a 23h build
that costs $X in wall-time + opportunity cost, the rail pays for itself
the first time it fires.

## The catch: structural primary, behavioral auxiliary

The deeper principle is in `feedback_harness_structural_primary`: the
harness body should be structural, with behavioral guidance as an
auxiliary layer. When you write a memory entry, ask: "what's the level-3
or level-4 rail this should turn into?" If you can't think of one, the
memory is at risk of being the only safeguard.

This doesn't mean every memory entry needs a rail. Many are notes,
context, identity sketches. But for *operational* memories — the ones
that say "always do X" or "watch out for Y" — the rail question should be
asked.

## Case study 2: 2026-05-22 v3 embed orch incident (= 5 L1 violations)

The 23h HNSW case crystallized "memory alone fails". This case shows the
follow-on failure mode: **L1-only stacking ≠ multi-layer**.

5 rule violations in 1 session, all silent:

1. **R2 transport rule** (`feedback_gpu_rental_r2_only`) — AI used scp instead, citing manufactured "creds exfil concern"
2. **Existing-pattern grep** (`feedback_script_saves_tokens` R7) — AI wrote orchestrator from scratch ignoring 4 canonical scripts
3. **Junk script proliferation** — AI created `_v4_prod_worker.sh` duplicate of existing `_v3_prod_worker_v3.sh`
4. **dual-magi review brief gap** — review process itself missed canonical violations + runtime bash semantics
5. **Self-check chain maintenance** — 30-min poll chain broke after 1 cycle, 3.4h silent burn

**Total cost**: ~$15 GPU idle + 6h debug + 4 production bugs that survived 2 dual-magi rounds.

**Root cause**: all 5 guardrails were L1 (behavioral, AI self-discipline). When AI manufactures override rationale (= "this case is different"), all L1 rails silently bypass. They were not multi-layer — they were "OR of single-shot shields" at the same layer.

```
=== What we had (= 5/22 state) ===
L1 only:
  - memory feedback_gpu_rental_r2_only       ← AI override
  - memory feedback_script_saves_tokens (R7)  ← AI override
  - memory feedback_self_check_inflight_workers ← chain break
  - hook self_check_reminder.sh                ← first-touch only
  - dual-magi review                           ← static blind spots

→ patterns 一致しないと全 silent. 1 override で全抜け.

=== What true defense-in-depth looks like ===
L1 behavioral: AI grep memory before design (= unreliable but cheap)
L2 structural static: PreToolUse Write/Edit hook → ssh fanout anti-pattern detect
   (= AI cannot bypass without explicit `# canonical-pattern-reviewed: <ref>` annotation)
L3 structural runtime: integration smoke gate (= 1-instance × 1-shard before full kick)
L4 external observer: AgentShield nightly scan of scripts/ for anti-pattern

異 failure class 補完:
  L1 fails (= AI forgets) → L2 catch (= file content scanner)
  L2 misses (= edge case) → L3 catch (= smoke run runtime expose)
  L3 misses (= scope 外) → L4 catch (= nightly historic audit)
```

**Fix shipped**: new L2 hook `ssh_fanout_canonical_check.sh` (Write/Edit on .sh files, anti-pattern detect including ssh-in-loop, scp-to-vast, .setup_done bypass, novel-orchestrator-shape) + new trigger 6 in `pipeline_preflight_gate.sh` (= Bash kick of orch shell blocks unless dual-magi ack present).

L3 (integration smoke gate) + L4 (AgentShield ssh-fanout yaml) still pending.

## Related

- `docs/INCIDENT_23H_HNSW.md` — the case study that crystallized this model
- `docs/INCIDENT_V3_EMBED_5_VIOLATIONS.md` — 2026-05-22 5-violation incident detail
- `docs/CLAUDE_HARNESS_DISTILLED.md` — the broader design rationale
- `plugins/harness-rails/README.md` — concrete implementation of level 3+4
- `plugins/harness-rails/hooks/ssh_fanout_canonical_check.sh` — L2 hook deployed 2026-05-22 from this incident
