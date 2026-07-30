---
name: formation
version: 0.2.0
description: |
  Spawn a long-running peer Claude Code or Codex worker in a new tmux pane when
  a task justifies hours of wall time and needs live observability, mid-flight
  redirection, or human-in-the-loop acks. Use this when an ephemeral subagent is
  insufficient: specifically for work where the user wants to tail the worker's
  pane, send follow-up instructions, or approve decisions through the mailbox.
  Claude workers can additionally use Claude Code remote control. NOT for quick
  lookups or single-shot research — use Task for those.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - AskUserQuestion
---

# formation — peer pane orchestration

A "worker" is a separate Claude Code or Codex CLI running in its own tmux pane,
seeded with a briefing file. Workers are for tasks that earn the cost of a fresh
agent process: **minutes-to-hours of wall time, multi-turn, observable**.

Paradigm comparison:

| | Task tool | formation |
|---|---|---|
| Lifetime | one-shot, returns | persistent pane |
| Observability | result only | user tails the pane live |
| Mid-flight redirect | impossible | `formation msg` |
| Remote decision | no | redirect: `formation msg`; close ASK: `formation ack` / `resolve`; Claude TUI: `/rc formation-<id>` |
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
2. `formation` is on PATH (symlink the plugin's `bin/formation` into `~/.local/bin`).
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

Template: `templates/briefing.md`, resolved relative to this `SKILL.md`.

### 2. Spawn

```bash
formation spawn [--bypass-sandbox|--sandbox] [--cli claude|codex|kimi] \
  [--model <model>] [--orchestrator] [--exclusive-input] [--task <label>] \
  <path/to/briefing.md> [worker_name]
```

#### Choosing the CLI (subscription quotas are the constraint)

Claude, Codex and Kimi are separate paid quotas that refill on separate clocks.
Spending them evenly is not a preference — a session that runs everything on
Claude exhausts one plan while two others sit idle. Measured on 2026-07-26 after
a single day of Claude-led work: **claude 30% of the weekly window consumed,
codex 7%, kimi 7%.**

Default assignment, unless the task argues otherwise:

| Work | CLI | Why |
|---|---|---|
| Long-running **implementation** worker, and the orchestrator of an implementation campaign | **codex** | See below — this is the load-bearing assignment. |
| **Coordination across repos, design, and questioning a premise** | claude | See below. |
| **Large-diff / whole-document review**, full-path traces | kimi | 1M context takes a whole doc or a wide diff in one pass. Note its window is ~5h rolling, not weekly — good for bounded review bursts, poor for a multi-hour trunk. |
| Cheap **independent verdict** | grok | `--no-subagents --max-turns 20` headless. Not quota-tracked; treat as a free second opinion, not a workhorse. |

#### What the record shows about Claude vs Codex

Two measured periods, not impressions.

**2026-07-23 to 07-25** ran almost entirely on Codex — eight codex workers, two
kimi, zero claude (archived registry). They were handed bounded issues to finish
end to end (#107, #116, #121, #127, plus #50/#57 in a sweep) and **all six closed
the same day**. The commits were not small: Deja Review Slice 0 landed 6,294
lines across 5 files, the fan-out failure-classification fix 688 lines across 11.
The target was usually magi's own internals — convergence gates, fan-out, schema
preflight — i.e. intricate existing machinery, not greenfield. One of those
workers, `applier-magi`, was spawned explicitly as *the orchestrator of the full
ultramagi loop*. On 2026-07-26 a codex orchestrator ran 4h21m, spawned and reaped
nine workers, routed review briefs to claude/kimi/grok reviewers, and handed off
five merged PRs.

So: **Codex finishes bounded work, sustains long autonomous runs, operates
comfortably inside intricate existing code, and can hold the orchestrator seat.**
Give it a defined entry and exit and it will get there.

Its recorded blind spot is **the context it is itself running in.** #57 and #139
are the same defect twice: `magi_fanout_codex.sh` launched reviewers with the
harness checkout as their working directory, so the target repository's source
and tests were absent from the reviewer's workspace — the review looked healthy
and was grounded in the wrong tree. It surfaced only because a reviewer reported
`schema_grounding_verdict=PARTIAL`. **Therefore: pin the execution context
explicitly in every codex briefing** — worktree path, target repository, and an
instruction to `cd` there first. Do not leave it to be inherited.

Claude's complementary strength is the other half of that: **noticing that a
premise is wrong.** #177 — the dispatcher inferring its chassis from an
environment variable rather than being told — was found by reading a worker's
report and asking why the shape was like that at all; #139 likewise came from
reading a reviewer's verdict rather than from the run itself. Conversely, when a
Claude-authored design was reviewed, it was the **Codex** reviewer that caught its
runtime behaviour (a shell assignment prefix scopes to one simple command, so a
chassis stamp never reached the compound commands it was meant for) after it had
already passed same-family review, an independent coordinator check, and CI.

The short form: **Codex is stronger on what the code will actually do; Claude is
stronger on whether it should be shaped that way at all.** Route accordingly, and
keep both on anything that matters.

Cross-family review stays mandatory regardless of who implements: at least one
reviewer from a different family than the author. That rule is about correctness,
not quota — a same-family panel shares the author's blind spots.

Check live headroom before a large fan-out, and prefer the provider with room:

```bash
capacity-oracle headroom      # per-provider used_percent / headroom
capacity-oracle recommend     # ranked assignment
```

Note that `capacity-oracle substitute` only diverts once the preferred provider
drops below its floor (default 0.30) — it is a pressure valve, not a balancer.
Do not rely on it to spread load; choose by the table above from the start.

- **Claude and Codex default to full permission/sandbox bypass** so an
  autonomous peer has the authority needed to finish its briefing. Safety is
  enforced by the Formation harness: scoped briefing and decision boundaries,
  credential refusal, mailbox/audit trails, stop conditions, and review gates.
  `--sandbox` is an explicit per-spawn opt-in; for Codex it selects
  `workspace-write`, disables approval prompts, and adds `FORMATION_HOME` as a
  writable directory for `formation report/done/ask`. Flags go before the
  briefing.
- **Kimi (`--cli kimi`)** launches the Kimi Code TUI with `--auto` (fully
  autonomous); `--sandbox` picks the softer `-y` (auto-approve tools, may still
  ask). Kimi has no CLI-level sandbox — its safety layer is the always-on
  harness-kimi bash guard, so a kimi worker's shell is intercepted regardless of
  flag. The seed briefing is injected into the TUI (Kimi has no positional
  prompt arg). Coordinate via `formation msg`/tmux (Kimi has no `/remote-control`).
  The offload target from `capacity-oracle substitute` (`k3`) lands here.
- `--model` is omitted by default, so the worker inherits the global default
  model; set it explicitly when a worker needs a different tier than the
  session default.
- `--orchestrator` (claude workers, no explicit `--model`): asks `capacity-oracle
  orchestrator-model --weight-class heavy` to pick the Claude tier by live
  subscription headroom — **fable** for a heavy orchestration when there's ample
  quota, else **opus**. A running session can't switch its own model, so this is
  how you launch a *peer* orchestrator on the right tier. Fail-open: if
  capacity-oracle isn't installed the worker just inherits the default tier. A
  one-line stderr notice states the picked tier. (See capacity-oracle-mcp#92.)
- **Placement defaults to a new tmux window** (isolates the worker's SessionStart
  window-rename from the parent — the ember-tanuki incident); pass `--split` for
  the old split-pane behavior. Either way it launches `claude --session-name
  formation-<name>` in the new pane and paste-loads the briefing.
- Registers the worker in `~/.formation/formation/registry.jsonl`.
- `--exclusive-input` is an explicit promise that no human or other sender
  will type into this worker's prompt concurrently. It records
  `exclusive_input=true` in the registry and
  `@formation_exclusive_input=1` on the pane. Without that spawn contract,
  `--inject` is refused after the durable row is appended and signaled.
- `FORMATION_SELF=<name>` and `FORMATION_PARENT=<parent_id>` are exported into
  the worker's pane env; the worker uses those to address the parent.
- **Identity is unified and locked (#101):** `FORMATION_SELF` /
  `@formation_identity_locked` is the routing, header, and self-reference source
  of truth. `@formation_id` remains a compatibility alias, but later task/status
  updates cannot change the locked identity. A dedicated worker window is named
  `<cli>-<name>`; SessionStart/compact/resume reassert that same identity instead
  of generating a second random codename. Standalone CLI auto-naming remains
  independent.
- **Pane visibility (#93)**: the worker window gets a `pane-border-status`
  strip showing `<locked-id> · <task> — <live-status>` — blue for claude,
  orange for codex, magenta for kimi, plus a
  `🔒sandbox` marker on the rare non-bypass workers (bypass is the de facto
  universal mode, so marking it would carry no signal) — and
  `formation report/done` update the strip's status suffix live. The task label is `--task` if given, else the briefing
  basename. Claude workers additionally pin `🎯 <goal>` in a statusline under
  the prompt box (goal = first content line under `## Mission` in the
  briefing) and get the same goal appended to their system prompt, both
  injected per-launch via `--settings`/`--append-system-prompt` so the user's
  global `settings.json` is untouched. Bypass claude workers also load the
  red-accent `formation-bypass` theme (installed idempotently to
  `~/.claude/themes/`) as a "no guardrails here" light.
- **Auto-starts a mailbox relay daemon** (`lib/mailbox_relay.sh`) in the
  background that watches `~/.formation/mailbox/log.jsonl` via inotify and
  sets `@formation_mail_pending` and a display signal for new entries addressed
  to this worker. It sends zero keystrokes into the worker prompt; the worker
  pulls the durable body with `formation inbox`. The relay
  pid is recorded at `~/.formation/formation/<name>.relay_pid`; logs at
  `/tmp/formation_relay_<name>.log`.

### 3. Supervise

```bash
formation status          # all workers: task, age, last mailbox report (or last pane line)
formation inbox           # reports addressed to you
formation inbox --history # last 50 addressed rows; does not move the unread cursor
formation msg <id> "<x>"  # send instruction to worker
formation msg --inject <id> "<x>" # exclusive worker only; short pull nudge
formation ack <request-id> ["summary"]
formation resolve <request-id> "<summary>"
formation review-request <reviewer-id> "<subject>"
formation verdict <review-id> <PASS|BLOCK> "<summary>"
formation reviews --stale-minutes <N>
formation reap <id>       # stop relay daemon, close pane, drop registry row
```

### Optional mail nudge and window-list presentation

The normal mailbox signal remains badge-only and sends zero prompt
keystrokes. `formation-mail-nudge` is an exceptional opt-in sweep/watcher for
spawned workers whose latest registry row **and** live pane both carry the
`--exclusive-input` contract. After the configured stale and snapshot-idle
periods it calls the shared `mailbox_inject_nudge` / `tmux_send_submit` path
once for that sequence and reports `receipt unconfirmed`; it never injects the
mailbox body or retries the child prompt. Only a badge clear/advance or a later
durable row from the canonical worker counts as effect; a pane repaint does
not. If neither is observable after the verification interval, it appends one
fixed-metadata alert to the recorded parent and uses the existing zero-keystroke signal path.
A missing/mismatched legacy parent route is visible and never guessed.
If no child attempt ever becomes eligible, the bounded
`FORMATION_MAIL_NUDGE_NO_ATTEMPT_ALERT` ceiling (default 300 seconds) emits
one distinct durable parent alert with reason `idle-never-stable`,
`nonexclusive`, or `registry-route-invalid`. It sends zero child/parent prompt
keystrokes and is never retried.

Use `formation-mail-nudge --dry-run` before a one-shot run or `--watch`.
Neither plugin install nor spawn starts it. Persistent operation requires an
explicit `install-formation-mail-nudge-service install`; its uninstall command
archives the namespaced unit/state. The service installer resolves the
canonical checkout and never records a disposable worktree.

`formation-window-status status` is read-only. Its explicit `apply` journals
the same tmux server's exact global-format and changed-pane preimages;
`--arrange` is separately opt-in. `revert` restores the journal when safe.
Neither helper is invoked automatically.

Whenever you return to idle in the lead pane, call `formation inbox` before
continuing — the worker may have asked a question or reported completion.

### 4. Worker-side (what the worker pane should do)

Drop these patterns into the briefing so the worker knows its own protocol:

- Every ~30 min or at logical checkpoints:
  `formation report "<1-line status>"`
- When a decision exceeds its boundary:
  `formation ask "<question>"` — creates a durable opaque request id, marks
  the worker `WAITING_PARENT`, writes the ASK to the lead's mailbox, and
  non-destructively signals the parent pane recorded at spawn.
  Use `--next-state READY_TO_MERGE` (or another uppercase state) when a
  resolution should transition somewhere other than `RUNNING`. The lead must
  close it with `formation ack <request-id> [summary]` or
  `formation resolve <request-id> <summary>`; an ordinary `formation msg`
  does not clear the ASK. ACK/resolution returns through the normal
  zero-keystroke mailbox relay.
- Parent authorization uses the exact `parent_id` captured by the ASK. If a
  tmux/server recovery changes the lead's fallback identity, the local
  operator can recover without weakening the gate:
  `FORMATION_SELF=<original-parent-id> formation ack <request-id> <summary>`.
  Use `formation status`, whose sticky ASK row remains caller-independent and
  shows both `request=` and the stored `parent=` id. Do not treat `lead` as a
  wildcard.
- When assigning a review, use
  `formation review-request <reviewer-id> "<subject>"`. The reviewer must
  answer with `formation verdict <review-id> <PASS|BLOCK> "<summary>"`.
  Formation copies the verdict to both requester and manager. A free-form
  report does not close the review request; unresolved work remains visible
  through `formation reviews --stale-minutes <N>`.
- **Reading the delivery line. Never re-send on the strength of it.** Every
  `msg` / `report` / `done` / `ask` prints one of four outcomes. Three of them
  mean the send is finished and the body is durable either way — a re-send only
  duplicates a long verdict and forces the recipient to de-duplicate it:

  | Output | Meaning | Your move |
  |---|---|---|
  | `signal=relay-owned` | Best case. The recipient's relay is alive and owns the badge write. | Nothing. Done. |
  | `signal=sent-directly` | No relay, so the sender set the badge itself. | Nothing. Done. |
  | `signal=unavailable … pull required` | No usable route. The row is durable; the recipient will see it when it reads its inbox. | Tell a human if it was urgent. Do not re-send. |
  | `FAILED (exit 4)` | The pane could not be signaled. Row still durable, but no badge appears. | Report the failure. Do not re-send the body. |

  If you believe a message was lost, check `formation inbox --history` or ask
  the recipient — do not put the same body in the mailbox twice.
- On completion:
  `formation done "<summary>"` — durable mailbox append plus the same
  zero-keystroke parent signal. `formation report` uses this route too.
  Spawn resolves the parent pane from process ancestry, with a unique
  controlling-TTY / `pane_tty` match as the wrapper-safe fallback; it never
  trusts a possibly inherited `TMUX_PANE`. Without a proven pane carrying a
  valid locked/legacy identity or an explicit `FORMATION_SELF`, it refuses to
  create an unaddressable worker. `formation status` shows legacy missing
  routes as `parent=UNROUTABLE`; from the intended parent pane,
  `formation repair-parent <worker_id>` repairs one unambiguous null row
  only when the target child pane is live and still owns that worker identity.
  It synchronizes the child pane's parent options and the registry row, writes
  persistent Sanada preimages first, rolls pane options back on failure, and
  prints the recovery path. Closed/recycled panes and mismatched non-null
  routes are refused; an already exact pane+row pair is a no-op.
  If `report` / `done` / `ask` / `ack` / `resolve` exits `4`, its row or
  semantic transition is already durable but a known pane could not be
  signaled. Do not automatically resend `report` or `done` (that would append
  duplicates); the recipient will still pull the row with `formation inbox`.
  A missing or unverified pane route degrades to pull-only exit `0` with
  `signal=unavailable`.

### 5. Remote intervention path

For a Claude worker, from phone / web / another machine:
```
/remote-control formation-<worker_id>
```
Attaches the remote client to the worker's session. The user can type
directly at the worker's prompt — no separate injection mechanism.

For a Codex worker, use `formation msg <worker_id> "..."` or attach to its tmux
pane. `formation msg` is mailbox-first: it appends the durable body and lets the
relay set a non-destructive badge with zero keystrokes into the prompt. An idle
agent is not proof that its draft is empty. **Never hand-roll a raw
`tmux send-keys -l "<text>" && tmux send-keys Enter` to nudge a peer pane.**
Only a worker spawned with `formation spawn --exclusive-input` may use
`formation msg --inject <worker_id> <body>` or
`mailbox-send <pane> <body> --inject`; even then the output remains
`receipt unconfirmed`, and only a short
pull nudge is injected through `tmux_send_submit` (copy-mode cancel → bracketed
paste → Enter, settle ~0.4s, Enter). Current Codex
may expose experimental `codex remote-control` commands for
an app-server daemon, but they do not attach to the already-running interactive
TUI that Formation spawned. Check the installed CLI without starting a daemon:

```bash
formation remote-check
```

Do not advertise Codex remote pairing as a Formation worker path until Codex
publishes a supported way to target an existing TUI session.

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
Path convention: `r2:<bucket>/checkpoints/<phase>/<worker>_<units>_<ts>.<ext>`

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
  - ✓ `formation msg worker-1 "decrypt with: sops exec-env config/secrets.enc.yaml '<cmd using \$openai>'"`
- `formation msg`, `formation report/done/ask` (mailbox), and `formation
  spawn` (briefing file content) all run the same credential pattern check
  and **hard-refuse with exit 3** on match. Patterns covered: `sk-*`,
  `ghp_*`, `AKIA*`, `*_API_KEY=...`, PEM private keys, long JWTs, etc. The
  refusal is logged to `~/.formation/mailbox/refuse.log` (timestamp + channel
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
- **Observer privilege**: the user can `tail -f ~/.formation/mailbox/log.jsonl`
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

## Nesting: fan out with subagents, not panes

A worker that needs parallelism has two ways to get it, and they are NOT
interchangeable:

- **Subagent (Agent/Task tool)** — the worker's *internal* fan-out. Runs
  inside the worker's own process, **no new pane**. The human sees only the
  worker, not its children. This is the default for a worker's child tasks.
- **`formation spawn` (new pane)** — a *peer* the human wants to observe and
  steer directly. Every spawn adds a pane to the orchestrator's tmux.

The failure mode is **pane explosion**: a worker that `formation spawn`s its
own children buries the human orchestrator under panes they can't track, and
the whole point of formation — live observability of a *small set of trunks* —
is lost. So:

- **Trunk → formation pane.** The few peers the human wants to watch / redirect.
- **Branch → subagent.** A trunk's own fan-out (parallel readers, per-item
  transforms, bounded builds). Invisible to the human, managed by the trunk,
  returns a result.

Decision rule a worker applies before spawning a child: *"does the human need
to watch or steer THIS child directly?"* — yes → `formation spawn` (rare);
no → subagent (the common case). Put this rule in worker briefings so the
convention holds from the first turn (e.g. "child tasks go to subagents;
`formation spawn` only for children the human must watch directly").

## Anti-patterns

- Spawning a worker for a task that finishes in <10 minutes.
- Briefings that say "figure it out" — specify the success criteria.
- Pasting any credential value into a message. See the discipline section.
- Workers writing to Memory MCP without the `formation/<id>/` prefix.
- Using `formation msg` to dump a multi-paragraph new briefing — re-spawn a
  fresh worker with a new briefing file instead.
- **Nesting formation panes for a worker's own fan-out.** A worker's child
  tasks belong in subagents (Agent/Task tool), not new panes — see "Nesting"
  above. Every nested `formation spawn` costs the human a pane; reserve it for
  children they genuinely need to watch or steer.

## Troubleshooting

- **`formation: refusing — body matches credential pattern`**: the
  redaction filter caught something that looks like a secret in an outgoing
  mailbox entry, a `msg` text, or a briefing file. Re-phrase around a SOPS
  decrypt command. Check `~/.formation/mailbox/refuse.log` to confirm which
  channel tripped it.
- **Worker pane stuck at claude login prompt**: spawn waited 30s for the `│ >`
  prompt and timed out. Manually complete login in the pane and re-send the
  briefing with `tmux load-buffer -b b0 <briefing> && tmux paste-buffer -p -d -b b0 -t <pane>`
  followed by a short sleep and `tmux send-keys -t <pane> Enter` (the `-p`
  bracketed paste avoids the early-submit / search-mode failure modes).
- **Parent `formation inbox` empty but worker claims it reported**: check
  `FORMATION_PARENT` is set in the worker's pane env (`tmux show-environment
  -t <pane>`). If missing, the worker sent to `lead` (default) — read that
  mailbox explicitly: `FORMATION_SELF=lead formation inbox`.
- **`/rc` attach fails**: confirm the worker's claude started with the
  `--session-name formation-<id>` flag (visible in `formation status` registry
  row).
- **Worker pane un-submitted, or jumps into slash/file "search-mode"**:
  the dominant cause is the **target pane being in tmux copy-mode** (the user
  scrolled up to read, or a prior action left it there). In copy-mode,
  `send-keys` is consumed by copy-mode, not the app: the submit `Enter` copies
  the selection and exits instead of submitting (message sits un-submitted),
  and a literal `/` from `send-keys -l` opens copy-mode *search* (the
  "search-mode" symptom). A secondary cause is `send-keys -l` itself — typed
  keystrokes race the render tick and embedded newlines in a multi-line
  briefing submit early. `tmux_send_submit` (used by the initial seed and the
  exceptional `mailbox-send --inject` path) (1) **leaves copy-mode first**
  (`send-keys -X cancel` when `#{pane_in_mode}` is 1) and (2) injects via
  **bracketed paste** (`load-buffer` + `paste-buffer -p`, which reaches the app
  tty even from copy-mode and lands atomically), then sleeps before the submit
  Enter. If you hand-roll an injection: cancel copy-mode, use `paste-buffer -p`
  (not `send-keys -l`), sleep ~0.4 s, press Enter, wait ~0.5 s, then press Enter
  again. The delayed double-submit applies to both Claude and Codex textareas.
  Diagnose with
  `tmux display-message -p -t <pane> '#{pane_in_mode}'` (1 = in copy-mode). If
  your installation pre-dates this fix, update the plugin and refresh the
  `formation` symlink from your plugin install.
- **Mailbox has a new entry but the worker isn't reading it**: the relay
  daemon may have died. Check with `ps aux | grep mailbox_relay | grep
  <worker_id>`. If absent, restart it manually (the lib path is derived
  from `formation` itself so this works regardless of where the repo lives):
  ```bash
  LIB="$(dirname "$(readlink -f "$(command -v formation)")")/../lib"
  STATE="${FORMATION_HOME:-$HOME/.formation}/formation"
  READY="$STATE/<worker_id>.relay_ready"
  PIDFILE="$STATE/<worker_id>.relay_pid"
  rm -f "$READY" "$PIDFILE"
  FORMATION_RELAY_READY_FILE="$READY" \
    nohup bash "$LIB/mailbox_relay.sh" <worker_id> <pane_id> \
    > /tmp/formation_relay_<worker_id>.log 2>&1 &
  PID=$!
  for _ in $(seq 1 40); do
    [[ "$(cat "$READY" 2>/dev/null)" == "$PID" ]] && break
    sleep 0.05
  done
  source "$LIB/mailbox_notify.sh"
  if [[ "$(cat "$READY" 2>/dev/null)" == "$PID" ]] &&
     mailbox_relay_alive "$PID" <worker_id>; then
    printf '%s\n' "$PID" > "$PIDFILE"
  else
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    rm -f "$READY"
    echo "relay failed to become ready; inspect the log" >&2
  fi
  ```
  Do not publish the pidfile before the ready marker matches the child PID;
  doing so recreates the startup race where a sender defers to a relay that has
  not anchored its mailbox high-water yet. Tail
  `/tmp/formation_relay_<worker_id>.log` to confirm events are firing. If the
  log shows `mode=polling`, install `inotify-tools` for lower-latency delivery.
