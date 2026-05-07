---
name: formation
version: 0.2.0
description: |
  Spawn a long-running peer Claude Code worker in a new tmux pane when a task
  justifies hours of wall time and needs live observability, mid-flight
  redirection, or phone-based human-in-the-loop acks. Use this when the Task
  tool's ephemeral subagent model is insufficient: specifically for work where
  the user wants to tail the worker's pane, send follow-up instructions, or
  approve decisions from a phone via /remote-control while away from the desk.
  NOT for quick lookups or single-shot research — use Task for those.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - AskUserQuestion
---

# formation — peer pane orchestration

A "worker" is a separate Claude Code CLI running in its own tmux pane, seeded
with a briefing file. Workers are for tasks that earn the cost of a fresh
claude process: **minutes-to-hours of wall time, multi-turn, observable**.

Paradigm comparison:

| | Task tool | formation |
|---|---|---|
| Lifetime | one-shot, returns | persistent pane |
| Observability | result only | user tails the pane live |
| Mid-flight redirect | impossible | `formation msg` |
| Remote ack from phone | no | `/rc formation-<id>` |
| Nesting | shallow | worker can spawn its own |

**Do not invoke for:** quick greps, single-file reads, one-shot research.
Those belong to the Task tool.

## When to invoke

Reach for this skill when the user says things like:
- "別pane起こして並列で{{長時間タスク}}やってもらえ"
- "worker spawn して {{briefing}} 渡して"
- "formation で {{task}} 走らせたい"
- Anywhere the task description implies "hours of work, I want to go do
  something else and check in later / redirect via phone."

## Prerequisites (verify before first spawn)

1. Running inside tmux (`[[ -n "$TMUX" ]]`). If not, tell the user to attach.
2. `formation` is on PATH (symlinked into `~/.local/bin` by `install.sh`).
3. `jq`, `flock`, `sops`, and `inotifywait` (from `inotify-tools`) available.
   The relay daemon falls back to 10s polling if `inotifywait` is missing,
   but inotify is strongly recommended for sub-second mailbox delivery.

## Invocation flow

### 1. Clarify the briefing with the user

Workers cost hours; a vague briefing wastes them. Ask the user for:
- Mission (one sentence: what does "done" mean)
- Scope IN / OUT
- Decision boundary (what may the worker decide alone? what must it ask?)
- Success criteria checklist

If the user's request is already rich enough, skip straight to writing the
briefing file. Otherwise use `AskUserQuestion` to fill gaps. Prefer writing
the briefing under the current project at `./formation/briefings/<id>.md` so
it's version-controlled with the work.

Template: `~/.claude/skills/formation/templates/briefing.md`.

### 2. Spawn

```bash
formation spawn <path/to/briefing.md> [worker_name]
```

- Splits the current tmux window, launches `claude --session-name
  formation-<name>` in the new pane, paste-loads the briefing.
- Registers the worker in `~/.njslyr7/formation/registry.jsonl`.
- `FORMATION_SELF=<name>` and `FORMATION_PARENT=<parent_id>` are exported into
  the worker's pane env; the worker uses those to address the parent.
- **Auto-starts a mailbox relay daemon** (`lib/mailbox_relay.sh`) in the
  background that watches `~/.njslyr7/mailbox/log.jsonl` via inotify and
  injects any new entries addressed to this worker into its tmux pane.
  Without this, the worker only notices new mailbox entries when it idly
  polls — the user historically had to poke each worker manually. The relay
  pid is recorded at `~/.njslyr7/formation/<name>.relay_pid`; logs at
  `/tmp/njslyr7_relay_<name>.log`.

### 3. Supervise

```bash
formation status          # all workers + last pane line
formation inbox           # reports addressed to you
formation msg <id> "<x>"  # send instruction to worker
formation reap <id>       # stop relay daemon, close pane, drop registry row
```

Whenever you return to idle in the lead pane, call `formation inbox` before
continuing — the worker may have asked a question or reported completion.

### 4. Worker-side (what the worker pane should do)

Drop these patterns into the briefing so the worker knows its own protocol:

- Every ~30 min or at logical checkpoints:
  `formation report "<1-line status>"`
- When a decision exceeds its boundary:
  `formation ask "<question>"` — writes to the lead's mailbox AND sends a
  LINE push so the user can reply from phone via `/rc formation-<id>`.
- On completion:
  `formation done "<summary>"` — mailbox + LINE push.

### 5. Remote intervention path

From phone / web / another machine:
```
/remote-control formation-<worker_id>
```
Attaches the remote client to the worker's session. The user can type
directly at the worker's prompt — no separate injection mechanism.

## Patterns

Reusable workflows discovered through actual multi-worker runs. Reach for one
of these before designing a coordination protocol from scratch.

### Race-pivot
- **When**: parent has a default approach; sub-worker explores an experimental
  variant in parallel, with explicit promotion criteria.
- **Setup**: parent runs `single` baseline; worker runs `exp` variant in
  isolation (separate collection / DB / output dir to avoid contamination).
- **Pivot rule**: declare a numeric threshold in the briefing. If exp metric ≥
  X sustained over Y minutes → promote exp to canonical; if exp metric < lower
  threshold → kill exp and let single complete.
- **Promotion mechanics**: Qdrant snapshot rename, DB swap, DNS cutover —
  pre-write the cutover commands in the briefing so promotion is mechanical.
- **Why**: lets the parent commit to a safe path while the worker explores;
  no rollback regret because exp was always isolated.

### Synthetic-then-real progressive validation
- **When**: target dataset is large (tens of GB+) and pull cost is high.
- **Setup**: smoke-test on synthetic data first (1–2M points, ~10 min). The
  vector content can be irrelevant when the downstream treats it as opaque
  (e.g., Qdrant insert speed); only the payload schema needs to be
  representative.
- **Promotion**: once the smoke baseline is trusted, pull real shards.
- **Why**: a host-throttled R2 pull of a 45 GB shard can burn 3 h before any
  feedback. Smoke-first surfaces throttling, disk shortfall, or schema
  mismatches in minutes, not hours.

### Touch-not contract
- **When**: parent has live production state that the worker must read but
  not mutate.
- **Briefing example**: "Read-only on collection `prs_chunks` and CPU instance
  #X. No PUT, no DELETE, no schema change. Use a separate collection
  `prs_chunks_exp` for any writes."
- **Why**: experimental worker config (PQ disabled, segment_number=16, etc.)
  silently leaks into parent state if the boundary isn't named in writing.
  Cite this contract in the briefing's Decision boundary section.

## Long-run discipline (R1–R4)

Workers that run **multi-hour or multi-day** (vast.ai GPU rentals, 100M+
chunk processing, multi-shard upserts) must obey four protocol rules. These
exist because rented hosts die without warning (hardware failure, network
partition, proxy outage); idle local workers don't have the same exposure but
should still respect R3.

### R1 — Cadenced R2 checkpoint push
Long-run upsert / generate / transform writes intermediate state to R2 at a
fixed cadence (e.g., per 20M points per daemon, or per 100 GB of output).
Local snapshot is deleted post-push to relieve disk pressure.
Path convention: `r2:mafutsu-<bucket>/checkpoints/<phase>/<worker>_<units>_<ts>.<ext>`

### R2 — Disk pre-flight (output × 1.5)
Before the contract: `required_disk_gb = expected_output_bytes / 1e9 * 1.5`.
The default `--disk 150` for vast.ai contracts is **forbidden** for shard
processing — it has lost $55+ to 88 % completion crashes. Always compute.

### R3 — Stall alarm (15 min progress = 0 → alert)
Worker spawns `stall_watchdog.sh` alongside the main task. 15 min with no
progress → mailbox alert to parent. False positives are cheap; silent stalls
are not.

### R4 — Host-death threshold (30 min unrecoverable → destroy)
Sustained ping packet loss for 30 min plus one failed `vastai reboot
instance` = host death confirmed. The vast.ai dashboard's `cur_state=running`
has been observed to lie in this scenario; do not trust it. After the 30 min
mark, further wait is sunk cost — destroy the contract and re-spawn elsewhere.

### Applicability
- 16+ daemon long-run, vast.ai $5+ rental, 1 h+ wall time → all four rules apply.
- Local idle worker < 1 h → R3 only (a cron / `ScheduleWakeup` is acceptable
  in lieu of `stall_watchdog.sh`).

## Credential discipline (mandatory)

**Never paste plaintext credentials into a formation message, briefing, or
pane prompt.** The mailbox is plain-text jsonl that persists indefinitely;
a leaked key lives there forever and shows up in every `tail`.

- Credentials live in SOPS-encrypted files (`*.enc.yaml`, `*.enc.env`).
- Agents reference them by path and command, not by value:
  - ✗ `formation msg worker-1 "use key sk-abc123..."`
  - ✓ `formation msg worker-1 "decrypt with: sops -d config/secrets.enc.yaml | jq -r .openai"`
- `formation msg`, `formation report/done/ask` (mailbox), and `formation
  spawn` (briefing file content) all run the same credential pattern check
  and **hard-refuse with exit 3** on match. Patterns covered: `sk-*`,
  `ghp_*`, `AKIA*`, `*_API_KEY=...`, PEM private keys, long JWTs, etc. The
  refusal is logged to `~/.njslyr7/mailbox/refuse.log` (timestamp + channel
  + from-id only; the body itself is NOT logged).
- If you hit the refusal, re-frame the message around a SOPS decrypt
  command — do not try to work around the filter by splitting the secret
  across messages or base64-encoding it.
- Briefings that require a secret should reference the encrypted file and
  the decrypt command, not embed the secret.

If SOPS is not yet set up for the project, stop and ask the user to do
`sops --encrypt` before continuing — do not fall back to plaintext.

## Design invariants

- **Memory MCP is shared** between lead and workers. Workers must namespace
  their writes under `formation/<worker_id>/` to avoid stomping lead entries.
  See "Memory namespace" below for the canonical filename convention and
  worked examples.
- **CWD is inherited.** Workers run in the same working directory as the lead
  pane. Do not support cross-project spawning in v1.
- **Sanada and Matsuoka** protocols (backup-before-destructive, no-retreat)
  live in global `~/.claude/CLAUDE.md` and apply to all panes automatically.
- **Observer privilege**: the user can `tail -f ~/.njslyr7/mailbox/log.jsonl`
  to watch all formation traffic. Never encrypt the mailbox itself — the
  redaction filter + SOPS discipline is what keeps secrets out of it.

### Memory namespace (detailed)

Workers write to `~/.claude/projects/<project>/memory/formation/<self_id>/`
only. Parent's `feedback_*.md` / `project_*.md` / `reference_*.md` at the
memory root are off-limits.

Canonical worker memory filenames (examples observed in real runs):

- `briefing_received.md` — the worker's own first-read interpretation of the
  briefing; useful for diffing later against drift.
- `<name>_strategy.md` — strategy notes for a named pivot (e.g.,
  `race_pivot_strategy.md`).
- `spec_evolution_<period>.md` — running log of instance spec / rate
  iteration during a long task.
- `<topic>_habit.md` — discipline rules the worker writes for itself
  (e.g., `mailbox_poll_habit.md`).
- `gotcha_<short_name>.md` — cautionary notes about traps the worker hit.

Worker memory is **session-scoped**: a future spawn under the same id does
not inherit it (and should not assume it). Generic learnings worth keeping
must be reported to the parent via `formation done`; the parent decides
whether to promote them into root-level `feedback_*` / `reference_*`. The
worker never promotes on its own.

## Anti-patterns

- Spawning a worker for a task that finishes in <10 minutes.
- Briefings that say "figure it out" — specify the success criteria.
- Pasting any credential value into a message. See the discipline section.
- Workers writing to Memory MCP without the `formation/<id>/` prefix.
- Using `formation msg` to dump a multi-paragraph new briefing — re-spawn a
  fresh worker with a new briefing file instead.

## Troubleshooting

- **`formation: refusing — body matches credential pattern`**: the
  redaction filter caught something that looks like a secret in an outgoing
  mailbox entry, a `msg` text, or a briefing file. Re-phrase around a SOPS
  decrypt command. Check `~/.njslyr7/mailbox/refuse.log` to confirm which
  channel tripped it.
- **Worker pane stuck at claude login prompt**: spawn waited 30s for the `│ >`
  prompt and timed out. Manually complete login in the pane and re-send the
  briefing with `tmux load-buffer -f <briefing> && tmux paste-buffer -t <pane>`.
- **Parent `formation inbox` empty but worker claims it reported**: check
  `FORMATION_PARENT` is set in the worker's pane env (`tmux show-environment
  -t <pane>`). If missing, the worker sent to `lead` (default) — read that
  mailbox explicitly: `FORMATION_SELF=lead formation inbox`.
- **`/rc` attach fails**: confirm the worker's claude started with the
  `--session-name formation-<id>` flag (visible in `formation status` registry
  row).
- **Worker pane appears unresponsive after a `formation msg`**: Claude Code's
  text area can swallow a single Enter and leave the message un-submitted.
  Both `formation msg` and the relay daemon double-tap Enter with a 0.5 s
  delay to force submission. If a hand-rolled `tmux send-keys ... Enter`
  looks stuck, reproduce the same pattern (a second Enter after a short
  sleep). If your installation pre-dates this fix, re-run the project's
  `install.sh` from your njslyr7 clone.
- **Mailbox has a new entry but the worker isn't reading it**: the relay
  daemon may have died. Check with `ps aux | grep mailbox_relay | grep
  <worker_id>`. If absent, restart it manually (the lib path is derived
  from `formation` itself so this works regardless of where the repo lives):
  ```bash
  LIB="$(dirname "$(readlink -f "$(command -v formation)")")/../lib"
  nohup bash "$LIB/mailbox_relay.sh" <worker_id> <pane_id> \
    > /tmp/njslyr7_relay_<worker_id>.log 2>&1 &
  echo $! > ~/.njslyr7/formation/<worker_id>.relay_pid
  ```
  Tail `/tmp/njslyr7_relay_<worker_id>.log` to confirm inotify events are
  firing. If the log shows `mode=polling`, install `inotify-tools` for
  sub-second delivery.
