# Incident: 23h sunk-cost loss on HNSW build (2026-04-30)

This document is the canonical case study that motivated `harness-rails`.
It serves as both the post-mortem and a teaching example for why pre-flight
algorithm fitness rails are needed.

## TL;DR

A pgvector HNSW index build on 165M rows ran for 23h41min and reached only
44% progress before being killed for a structurally different alternative
(8-shard partial indexes, completed in ~6h). The mismatch was algorithmic
and detectable at plan time:

```
working_set = 165M rows × 1024 dim × 2 bytes (halfvec) × 1.5 (HNSW overhead)
            = 472 GB
ram        = 125 GB

ratio       = 472 / 100 (80% of RAM safety margin)
            = 4.72x  ← rejected at plan time would have saved 23h
```

The philosophy `feedback_early_detect_bleeding` ("detect bleeding early,
fix early, minimize damage") was already documented in CLAUDE.md memory
from prior incidents. But it lived only as memory — there was no
structural rail. So when the operation slipped past the original 16h ETA,
nothing fired. The author noticed at 23h+ via manual progress query.

## Timeline

| Time (JST) | Event |
|---|---|
| 04-29 02:00 | HNSW build kicked off, ETA 16h declared |
| 04-29 14:00 | 12h elapsed; assistant reported "build healthy" via routine cron tick |
| 04-29 18:00 | 16h elapsed (estimate hit); should have triggered anomaly review, but no rail |
| 04-29 22:00 | User asked "how much longer?"; assistant queried `pg_stat_progress_create_index` for the first time |
| 04-30 03:00 | Progress 41.8% confirmed; full ETA recalculated as +4-5 days |
| 04-30 ~02:00 (CEST) | Build killed; shard ×8 alternative kicked off; completed in ~6h |

## Root cause classification

**Algorithmic mismatch + missing structural rail.**

- *Algorithmic*: HNSW with default `m=16, ef_construction=64` requires the
  full vector population to be addressable in memory (graph traversal is
  random-access). Working set 472 GB > RAM 125 GB → constant disk paging,
  effectively bottlenecking on NVMe random IOPS rather than CPU.
- *Structural*: `feedback_harness_structural_primary` ("the harness body
  is structural; behavioral guidance is auxiliary") had been written, but
  long-running operations had no automated detector. The detection was
  delegated to operator eyeballs, which is behavioral.

## What `harness-rails` adds

Three structural rails that turn the philosophy into automated detection:

### 1. Pre-flight: `safety-rails-preflight`

Before kicking off a build, run:

```bash
safety-rails-preflight hnsw --rows 165000000 --dim 1024 --ram-gb 125
```

Output (with the actual incident's parameters):

```
== HNSW build (n=165,000,000, dim=1024, m=16) ==
  peak mem:     472.1 GB
  RAM:          125.0 GB
  headroom:    -372.1 GB (OVER)

REJECT: HNSW peak mem 472 GB > 80% of RAM (100 GB), over by 4.72x

alternatives:
  - shard ×5 via partial WHERE indexes (each shard ~94 GB working set)
  - int8 SBQ quantization (pgvectorscale) → peak 30 GB, recall -1~3pt recoverable via Phase-7 rerank
  - IVFFlat instead of HNSW (no graph overhead, peak 315 GB still > RAM but much less spill)
  - larger-RAM host (need 675 GB+ instance)
  - sparse-first + dense brute-force rerank (HNSW skip entirely)
```

Exit code `2`. Use in shell scripts to fail fast before committing 23h of
compute.

### 2. In-flight: `safety-rails-beat` + `safety-rails-watcher`

While the operation runs, write a heartbeat:

```bash
safety-rails-beat write \
    --project PRS-LLM \
    --job hnsw_build \
    --eta-hours 4 \
    --metric tuples_done=$(psql -tAc "SELECT tuples_done FROM pg_stat_progress_create_index")
```

Cron-driven watcher (`*/1 * * * *`) reads the heartbeat and detects:

- **stale** — heartbeat older than 180s → likely crashed/hung
- **eta overrun** — elapsed ÷ eta > 1.5x (warn) / 2.0x (alert) / 3.0x (critical)

Alerts fire to Discord + gh issue. **No auto-kill** — the operator decides.

If this rail had been in place, the alert would have fired around hour 24
(1.5x of declared 16h ETA), surfacing the issue 23 hours earlier than what
actually happened.

### 3. Post-mortem: gh issue auto-create

The watcher's `alert` and `critical` levels auto-create a gh issue (deduped
by title), so even if Discord notifications get lost in the noise, the
audit trail persists. The next operator (or your future self) can search
the same class of incident and find what was done.

## What this rail does NOT solve

Out of scope:

- **Disk topology / mirror health / JBOD state**. The actual incident was
  worsened by `mars` running with a degraded mirror (broken mid-conversion),
  effectively single-NVMe IOPS. That's an infrastructure concern handled
  separately by capacity planning rails — not by `harness-rails`.
- **Algorithm correctness for new vector models**. The pre-flight formulas
  assume known scaling laws (HNSW ~ N × dim × bytes × 1.5). For new or
  proprietary algorithms, run a spike test with a 10-25% scale subset to
  measure empirically.
- **Auto-repair**. By design. Per the `feedback_chain_watcher_restart_duplicate_kick`
  incident (a separate 44GB data loss caused by an over-eager auto-restart
  rail), automated repair tends to inflict secondary damage. `harness-rails`
  detects and notifies; the human decides.

## Lessons summary

1. **Documented philosophy ≠ active rail.** "Detect bleeding early" lived
   only in CLAUDE.md memory. Without an automated detector, it didn't fire.
   Memory is necessary but not sufficient.
2. **Estimates need automated validation.** A 16h ETA that's 1.5x past
   without a progress check is silently telling you the model is wrong.
   Check the model, not just the watch.
3. **Algorithm fitness is computable.** For most well-known algorithms
   (HNSW, IVFFlat, DiskANN, BM25, etc.), peak memory is a closed-form
   function of input size + parameters. Compute it. If it doesn't fit,
   stop and reconsider before committing wall-time.
4. **Rejection should come with alternatives.** A pure "REJECT" message
   pushes the operator to redesign from scratch. A "REJECT, here are 5
   alternatives" turns it into a structured choice.

## Related

- Incident issue: [hrmtz/PRS-LLM#59](https://github.com/hrmtz/PRS-LLM/issues/59)
- Related memory: `feedback_long_run_op_anomaly_rail`,
  `feedback_early_detect_bleeding`, `feedback_harness_structural_primary`,
  `feedback_recurring_incident_to_issue`
- See `plugins/harness-rails/README.md` for the API reference.
