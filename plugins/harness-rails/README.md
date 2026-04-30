# harness-rails

**Operational safety rails for long-running operations.** Pre-flight algorithm fitness check, in-flight anomaly detection (heartbeat + cron watcher), Discord + gh issue auto-emit. Human-in-loop only — no auto-kill, no auto-revert.

Built after a 23h sunk-cost incident: an HNSW build hit the I/O ceiling because `working_set (320 GB) > RAM (125 GB)`, but no rail caught it. The "early bleeding detection" philosophy lived only in CLAUDE.md memory, not in structural rails.

This plugin makes that detection structural.

## Components

### 1. `safety-rails-preflight` — pre-execution algorithm fitness CLI

Before kicking off a long-running build, check if it'll fit:

```bash
safety-rails-preflight hnsw --rows 165000000 --dim 1024 --ram-gb 125
# → REJECT: HNSW peak mem 480 GB > 80% of RAM (100 GB), over by 4.80x
#   alternatives:
#     - shard ×5 via partial WHERE indexes
#     - int8 SBQ quantization → peak 30 GB, recall recoverable via Phase-7 rerank
#     - IVFFlat (no graph overhead)
#     - larger-RAM host (need 686 GB+ instance)
#     - sparse-first + dense brute-force rerank
```

Exit code `2` on reject. Use in shell scripts to fail fast:

```bash
safety-rails-preflight hnsw --rows ... --ram-gb 125 || exit 2
psql -c "CREATE INDEX ... USING hnsw ..."
```

Supported algorithms: `hnsw`, `ivfflat`, `diskann`. Add more in `lib/safety_rails/preflight.py`.

### 2. `safety-rails-beat` — in-flight heartbeat helper

Long-running scripts write a heartbeat file every interval; the watcher reads it.

**Bash:**
```bash
# inside a long-running loop or before a long DDL
safety-rails-beat write \
    --project PRS-LLM \
    --job hnsw_shard_build \
    --eta-hours 4 \
    --metric tuples_done=$(psql -tAc "SELECT tuples_done FROM ...")
```

**Python:**
```python
from safety_rails import heartbeat

def sample():
    return {"tuples_done": pg_query(...)}

with heartbeat.beat("hnsw_shard_build", project="PRS-LLM",
                    eta_hours=4, sampler=sample):
    run_long_operation()
```

State location: `~/.local/run/safety-rails/<project>/<job>.json`

### 3. `safety-rails-watcher` — cron-driven anomaly scanner

Reads heartbeat files. Detects:

- **stale** — heartbeat older than 180s (default) → likely crashed/hung
- **eta overrun** — elapsed/eta_hours > 1.5x (warn) / 2.0x (alert) / 3.0x (critical)

On detect:

- Discord notify (per-project channel via `discord-bot post <project>`, or fallback `discord-notify`)
- gh issue create on alert+ (deduped by title)

**Does NOT auto-kill or auto-revert.** All repair is human-in-loop. (See "design philosophy" below.)

Cron line:

```cron
*/1 * * * * /path/to/harness-rails/bin/safety-rails-watcher >> /var/log/safety-rails-watcher.log 2>&1
```

### 4. `hooks/pipeline_preflight_gate.sh` — PreToolUse hard block

A behavioral memory rule ("smoke 1 batch before bulk") gets ignored under cognitive load. This hook makes it structural by **blocking** Claude's `Bash` tool calls that hit dangerous integration patterns until the operator has logged a pre-flight ack.

Trigger patterns (built from a 12-bug cascade incident, 2026-04-30):

| Trigger | Pattern | Why |
|---|---|---|
| `cloud-instance-create` | `vastai create instance` / `hcloud server create` / `aws ec2 run-instances` / `gcloud compute instances create` | hourly billing locks in; smoke 1 instance with target workload before fleet rent |
| `multi-component-pipe` | `curl …` + `zcat/gunzip/unzstd` + `python/jq/awk` + `psql … COPY` chained | 4-stage pipe has 4+ failure modes (partial transfer, EOF, parser-format mismatch, COPY back-pressure) |
| `cross-host-pg-stream` | `ssh + pg_dump` / `COPY … FROM STDIN BINARY` | network bandwidth ceiling + TCP single-stream limit; measure raw bandwidth before parallelism |
| `bulk-parallel-loop` | `for X in 0 1 2 3 …; do … &; done` (N≥4 backgrounded) | for-loop with N≥4 amplifies any single-unit bug N times; verify N=1 happy path first |

Bypass: complete pre-flight (sample 1 row, measure bandwidth, smoke 1 unit end-to-end, list assumptions) and:

```bash
mkdir -p ~/.local/state/pipeline-preflight
echo "preflight done $(date -u +%Y-%m-%dT%H:%M:%SZ): assumptions verified" \
  > ~/.local/state/pipeline-preflight/<trigger>.ack
```

Validity: 30 minutes. Re-create after major changes (new offer, new file format, new host).

Install on `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [
        { "type": "command", "command": "bash ~/.claude/hooks/pipeline_preflight_gate.sh", "timeout": 5 }
      ]
    }]
  }
}
```

## Install

Plugin path: `plugins/harness-rails/` (this directory).

Add bin to PATH:

```bash
echo 'export PATH="$HOME/.claude/plugins/claude-harness/plugins/harness-rails/bin:$PATH"' >> ~/.bashrc
```

(Adjust path to wherever you installed the plugin.)

Install crontab on each host that runs long ops:

```bash
crontab -e
# add: */1 * * * * /full/path/to/safety-rails-watcher >> ~/.local/log/safety-rails.log 2>&1
```

For Discord/gh integration, ensure `discord-bot` (or `discord-notify`) and `gh` CLI are on PATH and authenticated.

## Design philosophy

### Why no auto-kill / auto-revert

In the canonical incident (44GB lost from a duplicate watcher restart kicking the same DB-rewriting subprocess twice), automated repair caused more damage than it prevented. This rail commits to the inverse policy:

> Detection automatic. Decision human.

Discord notify + gh issue happen instantly; the operator decides what to do. The framework provides:

- the timing (so you can decide quickly)
- the data (so you can decide correctly)
- the audit trail (so the next time, you remember)

### Scope: algorithm defects only

In scope:
- working set vs RAM ceiling
- algorithm choice appropriateness for data size
- complexity vs scale mismatch

Out of scope (handled by separate ops discipline):
- disk topology / mirror health / JBOD state
- network throughput / firewall
- physical infra capacity planning

### Smoke test cannot detect this

A 1% subset or 1M-row test DB will not detect memory ceiling issues — the working set is too small to spill. Use the `preflight` formula instead. Smoke tests are for correctness; preflight is for resource sizing.

## Motivation: the 23h incident

See [hrmtz/PRS-LLM #59](https://github.com/hrmtz/PRS-LLM/issues/59) for the full retrospective.

Summary: 165M chunks × 1024d halfvec HNSW build started with `working_set (320 GB) > RAM (125 GB)`. The build progressed at 1/4 the projected rate, hit 23h41min elapsed at 44% progress, and was killed for a shard ×8 alternative.

The fix was algorithmic (shard the build into RAM-fitting subsets), and that decision could have been made *before* kickoff if `preflight` had existed. This plugin retrofits that rail.

## Roadmap

- v0.1 (this release): heartbeat + watcher + preflight (HNSW/IVFFlat/DiskANN)
- v0.2: declarative `jobs.yaml` per-project config
- v0.3: dead-man-switch (external host pings the watcher's liveness)
- v0.4: incident escalation (cron summing same-class issues)
- v0.5: estimate confidence (range-based ETA, with extracted historical data)

## License

MIT — see `../LICENSE` at repo root.
