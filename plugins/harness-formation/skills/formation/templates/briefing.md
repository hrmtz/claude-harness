# Formation Worker Briefing

> Replace every `{{placeholder}}` before spawning. A briefing is a contract
> between lead and worker; vagueness here surfaces as wasted hours later.

## Mission
{{one-sentence statement of what "done" looks like}}

## Scope
- IN:  {{what this worker owns}}
- OUT: {{what this worker must not touch}}

## Context
{{background the worker needs that is not in the codebase: prior decisions,
  why this task exists, constraints the lead already considered}}

## Inputs
- Working directory: {{absolute path — state it, do not leave it inherited}}
  > Say where the work happens and have the worker `cd` there in its first
  > command. Inheriting the lead's cwd has grounded reviewers in the wrong
  > checkout twice (#57, #139): the run looked healthy while the target repo's
  > source and tests were absent from the workspace. If the task uses a
  > worktree, name the worktree path and the branch here.
- Key files: {{paths}}
- External resources: {{URLs, API endpoints}}
- Credentials (SOPS-only, never inline):
  - {{name}} → `sops exec-env {{path/to/secrets.enc.yaml}} '<cmd that uses $\{{key}}>'` (always inject into the subprocess env via exec-env; never decrypt to stdout, never print the value)
  - Never paste the decrypted value into a mailbox message or pane prompt.

## Expected output
- Artifacts: {{file paths the worker should create/modify}}
- Report cadence: every {{30m|1h}} via `formation report "<status>"`
- Completion: `formation done "<summary>"` when mission is met

## Decision boundary
- Worker may decide autonomously: {{list}}
- Worker MUST ask via `formation ask "<question>"` for: {{list}}

## Guardrails
- Forbidden actions: {{destructive ops outside project tree, pushes to main,
   package removal, anything in D001-D010 of CLAUDE.md}}
- Memory MCP: shared with lead. Write findings under a namespace prefix
  `formation/{{worker_id}}/` to avoid stepping on lead's entries.
- Credential discipline: SOPS-decrypt on demand, never persist decrypted
  values to disk, never include them in `formation report/ask/done` bodies.
  The mailbox will hard-refuse credential-shaped strings.

## Success criteria (checklist the worker uses to self-verify before `done`)
- [ ] {{criterion 1}}
- [ ] {{criterion 2}}
- [ ] {{criterion 3}}
- [ ] `/simplify` review passed

## Standing orders (apply unless this briefing overrides them)

These defaults exist because earlier multi-worker runs lost hours to vague
discipline. Override only with a written reason below.

### Mailbox discipline
- The relay daemon signals new mailbox entries without typing into your prompt.
  At every turn boundary, run `formation inbox` (or inspect
  `tail -5 ~/.formation/mailbox/log.jsonl`) — a badge cannot wake an idle agent,
  and a stalled relay or missed `to` field can hide a parent ack.
- Unless this worker was spawned with `--exclusive-input`, parent messages
  never type into the prompt. Even for an exclusive worker, an explicit
  `--inject` sends only a short pull nudge; the body is read through this inbox.
- Skip your own outbound entries; only act on `from` ≠ self.
- Do not let parent acks stall longer than 15 min unanswered: if you are
  blocked on a parent decision, that wait is parent's blocker too.

### Reporting cadence
- 30 min cadence: one-line `formation report "<status>"` covering position,
  rate, and any errors observed.
- Off-cadence triggers: large rate change, unexpected error, shard / phase
  completion. Report immediately, don't wait for the next 30 min mark.
- When you cross your decision boundary, use `formation ask "<question>"` and
  retain the printed request id. Wait idle until the parent closes it with
  `formation ack` or `formation resolve`; an ordinary message is not an ACK.
  Don't proceed past the boundary on a guess.

### Memory namespace
- Write only under `~/.claude/projects/<project>/memory/formation/<self_id>/`.
  Touching the parent's root-level `feedback_*.md` / `project_*.md` /
  `reference_*.md` is forbidden.
- Filenames follow the worker memory convention (see SKILL.md "Memory
  namespace"): `briefing_received.md`, `<name>_strategy.md`,
  `spec_evolution_<period>.md`, `<topic>_habit.md`, `gotcha_<short>.md`.
- Generic learnings worth keeping go into your `formation done` summary; the
  parent decides whether to promote them. Do not promote yourself.

### Token discipline (claude-harness#218)
- Session context grows ~1k tokens per API response, so unit cost rises
  linearly the longer a pane lives. Close at a milestone once you reach
  **~50 responses**, or earlier if cache_read per response exceeds **100k**
  tokens: write your resume packet, `formation done`, and let the parent
  respawn you from it. Measured basis: claude-harness#218 (2026-07-28
  comment) — a 1,012-response session cost 455M cache-read tokens; cutting
  every ~40 responses would have cost 72M.
- Prose output in Japanese runs under `/genshijin 通常` (invoke it as your
  first action). Worker prose is machine-read; keigo and cushion words are
  pure token waste. The skill's auto-clarity exceptions (destructive-op
  warnings, security notes) still apply.

### Long-run discipline (R1–R4) — applies if this task is multi-hour
- R1: push intermediate state to R2 at the cadence stated below.
  Cadence (set per task): {{e.g., per 20M points per daemon, or per 100 GB output, or N/A}}
- R2: confirm disk pre-flight before any vast.ai contract:
  `required_disk_gb = expected_output_bytes / 1e9 * 1.5`. Don't accept the
  default `--disk 150` for shard processing.
- R3: run `stall_watchdog.sh` (or equivalent) alongside the main task; alert
  parent if progress = 0 for 15 min.
- R4: 30 min sustained ping loss + one failed `vastai reboot instance` =
  host death; destroy the contract and re-spawn. Don't trust dashboard
  `cur_state=running` past this threshold.

### vast.ai operational gotchas
- After `vastai create instance`, the printed `new_contract` ID can differ
  from the `instance_id` you'll need to destroy. Always verify with
  `vastai show instances --raw | grep label=<unique-label>` before any
  destroy, and assign a unique `--label` per contract (e.g.,
  `qdrant-parallel-exp-v1`). Past incident: a $0.39 zombie ran undestroyed
  because the wrong ID was used.
