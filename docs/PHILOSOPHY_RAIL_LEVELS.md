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

## Related

- `docs/INCIDENT_23H_HNSW.md` — the case study that crystallized this model
- `docs/CLAUDE_HARNESS_DISTILLED.md` — the broader design rationale
- `plugins/harness-rails/README.md` — concrete implementation of level 3+4
