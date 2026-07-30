# harness-formation

Peer-pane Claude Code and Codex worker orchestration. Spawn long-running workers in tmux panes and coordinate through an append-only jsonl mailbox. Claude workers also support Claude Code phone/web remote control via `/rc`; Codex workers use tmux or mailbox messages. Auto-suggests itself when the user's prompt matches natural-language worker-spawn intent ("裏のclaude にやらせる", "並行で", etc).

> All `formation` CLI + `mailbox_relay` runtime ships inside this plugin.

## What's inside

| Path | What it does |
|---|---|
| `skills/formation/SKILL.md` | Skill definition (when to spawn vs use Task tool, briefing template, R1-R4 long-run rules, credential discipline) |
| `skills/babysit-pr/SKILL.md` | Receipt-gated CI monitoring, bounded source repair, and review follow-up for Formation-owned PRs |
| `bin/formation` | CLI: worker coordination plus the read-only `integration-audit` report |
| `bin/formation-mail-nudge` | Optional one-shot/watching escalation for ignored badges; never starts automatically |
| `bin/formation-stall-watch` | Structural stall observer using mailbox silence and pane stability |
| `bin/install-formation-mail-nudge-service` | Explicit systemd user-service install/uninstall for the optional watcher |
| `bin/formation-window-status` | Explicit journaled tmux window-list apply/status/revert tool |
| `lib/mailbox.sh` | Durable JSONL storage, locking, sequence allocation, and read cursors |
| `lib/mailbox_delivery.sh` | Shared recipient/sender resolution, relay delegation, and exclusive-injection policy |
| `lib/mailbox_notify.sh`, `lib/mailbox_relay.sh` | Zero-keystroke pane signaling primitives and the per-worker signal daemon |
| `lib/requests.sh` | Durable semantic ASK/ACK/resolve state, separate from transport |
| `lib/wake.sh`, `lib/redact.sh` | The single exceptional submit primitive and shared credential refusal/audit |
| `hooks/formation_suggest.sh` | UserPromptSubmit hook: detects worker-spawn intent, injects a formation keyword to surface the skill |
| `hooks/pr_receipt.sh` | PostToolUse hook: mints a local PR ownership receipt only for an active Formation session |

## Trigger keywords (auto-suggest hook)

The hook fires when any of these high-confidence worker-spawn patterns match the user prompt:

- `(裏の|他の|別の|違う|もう一人の)(claude|おまえ|お前|キミ|君)` — "裏のお前にやらせる" 100% formation
- `裏で(やっ|やら|走らせ)` — "裏でやって"
- `並[行列](で|して|に).*(claude|task|やる)` — "並行で claude"
- `別(セッション|pane).*(claude|統合)` — "別セッションで統合"
- `formation skill` / `spawn.*worker` — direct invocation

## Install

```bash
# in Claude Code
/plugin marketplace add github:hrmtz/claude-harness
/plugin install harness-core@claude-harness
/plugin install harness-formation@claude-harness
```

`harness-core` supplies the cross-CLI identity guard used by every Formation
worker launch. Install both plugins; Formation fails closed if the guard is
unavailable.

After install:

1. Symlink the CLI onto `PATH`:
   ```bash
   ln -sfn ~/.claude/plugins/harness-formation/bin/formation ~/.local/bin/formation
   # (path may differ — adjust to your Claude Code plugin install root)
   ```
2. Verify:
   ```bash
   formation status   # → "(no workers)"
   ```
3. Audit integration state without changing GitHub, branches, worktrees, or
   checkouts:
   ```bash
   formation integration-audit --repo OWNER/REPO \
     --parent-checkout /path/to/parent
   formation integration-audit --repo OWNER/REPO \
     --parent-checkout /path/to/parent --json
   ```
   The report reconciles open/draft/recently merged PRs, linked issues,
   recorded checks/reviews/tests, remote branches, local worktrees, parent
   divergence/dirty counts, and Formation worker state. Missing auth, refs, or
   evidence—including pane-gone workers with unknown repository—is `ACTION`;
   exit status is `1` when an `ACTION` is present, `0` for PASS/WARN-only reports, and `2` for invalid
   invocation or unreadable fixture input.
4. The auto-suggest hook is **active by default** and injects the Formation skill hint for high-confidence worker-spawn prompts. To observe matches without injecting:
   ```bash
   export FORMATION_SUGGEST_MODE=shadow
   ```
5. Codex/Kimi panes: install the mailbox-first pane-messaging rail (gh #105/#130/#166)
   into the always-loaded AGENTS surface so agents that have not loaded the
   Formation skill still route through durable `formation msg`; the rail keeps
   `tmux_send_submit` only for explicitly exclusive prompt injection:
   ```bash
   bash ~/.claude/plugins/harness-formation/bin/install-pane-messaging-rail.sh            # ~/AGENTS.md (Codex global)
   bash ~/.claude/plugins/harness-formation/bin/install-pane-messaging-rail.sh <project>/AGENTS.md
   ```
   The Kimi `AGENTS.md.template` already carries the same rail (§9). The installer
   is marker-bounded and idempotent, fails closed on any inconsistent marker
   state, preserves foreign content, and takes a persistent Sanada backup
   before modifying an existing file.

Prompt injection is not inferred from an apparently idle pane. If a worker
truly has no concurrent human input, create it with
`formation spawn --exclusive-input ...`; this records the contract in both the
registry and `@formation_exclusive_input`. Only then may
`formation msg --inject <worker> ...` or `mailbox-send ... --inject` send a
short pull nudge. The durable body always remains in the mailbox.
Every injection remains `receipt unconfirmed` and uses the shared delayed-submit
primitive.
Normal zero-keystroke signals now report
`receipt=unconfirmed recipient_activity=unknown`: a durable row and pane badge
do not prove that the recipient read its inbox. Do not resend the body. For an
urgent instruction, inspect `formation status` or the explicit
`formation-stall-watch`; an idle recipient may not act until a later turn.

### Registering an existing pane

Use `formation register` inside a tmux pane that Formation did not spawn, or
inside a surviving pane after an approved registry reset:

```bash
formation register --cli claude --task coordination lead
```

Registration requires the pane-local `$TMUX_PANE` to agree with independent
process-ancestry/TTY resolution and with a targeted tmux lookup. It refuses a
duplicate id, duplicate pane, inherited `FORMATION_SELF` mismatch, or existing
pane identity conflict. A pre-existing pane is always registered with
`exclusive_input=false`, and the live `@formation_exclusive_input` option is
removed. Its relay state is explicitly `DEAD` with reason
`manual-registration-no-relay`; mailbox senders therefore use the existing
zero-keystroke direct signal fallback. Credential-shaped `--task` or `--goal`
metadata is refused before registry or pane mutation. Registration preserves
the existing window name; locked pane identity, not mutable display text,
remains the routing source of truth.

If the pane inherited both `FORMATION_PARENT` and `FORMATION_PARENT_PANE`,
register the parent first. Registration accepts that route only when the latest
parent registry row and its locked pane identity agree. With neither variable,
the row is intentionally `parent=UNROUTABLE`: it can receive `formation msg`
and read `formation inbox`, but has no inferred report destination.
Never derive the caller pane with untargeted
`tmux display-message -p '#{pane_id}'`; that returns the session's active pane,
which may belong to another agent.

If option update fails, registration restores every captured pane option and
does not append a row. Re-run the same id after fixing the cause. A conflicting
locked identity remains fail-closed and requires inspection before an approved
registry reset. If rollback itself fails, the command exits 8 with
`PARTIAL REGISTRATION`; inspect the named pane and registry before retrying.

### Optional ignored-badge escalation

`formation-mail-nudge` is exceptional, opt-in automation for an exclusive
spawned worker. It requires both the latest registry row and the live pane to
declare `--exclusive-input`, plus an old badge and a stable pane snapshot. It
then sends exactly one short `formation inbox` pull nudge, never the mailbox
body and never a retry. A later badge clear/advance or durable row from that
canonical worker is the only success evidence; a pane repaint is not. If
neither appears through the verification interval, it appends one fixed-metadata
alert to the spawn-time parent and signals that parent with zero prompt keystrokes. Legacy or
mismatched parent routes are reported visibly and are never guessed.
If a pending sequence never becomes eligible for any child attempt, the
bounded `FORMATION_MAIL_NUDGE_NO_ATTEMPT_ALERT` ceiling (default 300 seconds)
still produces one durable parent alert with
`idle-never-stable`, `nonexclusive`, or `registry-route-invalid`. This path
sends zero child and parent prompt keystrokes and never retries the alert.

```bash
formation-mail-nudge --dry-run
formation-mail-nudge                 # one sweep
formation-mail-nudge --watch         # foreground watcher
formation-mail-nudge --watch --quiet # drop routine lines; keep escalations
install-formation-mail-nudge-service --dry-run install
install-formation-mail-nudge-service install
install-formation-mail-nudge-service uninstall
```

Neither plugin install nor `formation spawn` starts the watcher. Persistent
operation is an explicit systemd user-service choice; the installer resolves
the canonical checkout rather than persisting a caller worktree.

`formation-window-status` is likewise explicit. `apply` changes server-global
window formats and journals their exact preimage; `--arrange` is separately
opt-in. `revert` restores that journal for the same tmux server, and `status`
is read-only:

```bash
formation-window-status status
formation-window-status apply --lead "$TMUX_PANE" --task "review"
formation-window-status apply --arrange --dry-run
formation-window-status revert
```

`formation-stall-watch` classifies a worker as stalled only when two
independent clocks have both expired: the worker has emitted no mailbox row,
and its captured pane hash has not changed. It validates the live pane identity
against the latest registry row and stores observation state below
`~/.formation/state/stall-watch/`. Kimi's idle TUI redraw changes its leading
spinner glyph without doing work, so that glyph is normalized; semantic pane
text remains part of the hash. Workers with an unresolved ASK report
`WAITING_PARENT`, not `STALL`.

```bash
formation-stall-watch --silence 900 --idle 900 --json
formation-stall-watch --watch --quiet
```

ASKs are durable semantic state, stored separately from mailbox transport.
`formation ask` returns a request id and makes the worker `WAITING_PARENT`;
the parent closes it explicitly with `formation ack` or `formation resolve`.
`formation status` keeps unresolved requests visible after later reports, and
`formation reap` refuses them unless `--force` is explicit. Spawn also passes
the parent's pane route separately from its semantic identity: worker
`report`/`done`/`ask` and parent `ack`/`resolve` all append first, then use the
same zero-keystroke relay-or-direct signal policy. A dead relay therefore does
not silently remove the badge fallback, and the body never enters a prompt.
The parent pane is discovered by process ancestry, with a unique controlling
TTY ↔ `pane_tty` match as the safe fallback for wrapper processes that break
the root-PID chain. `TMUX_PANE` and mutable window names are never proof;
stale/inherited sibling ids are ignored. Spawn fails closed when it can prove
neither a real parent pane with a valid locked/legacy identity nor an explicit
`FORMATION_SELF`, because replies would otherwise be unaddressable.
`formation status` renders missing/invalid routes as `parent=UNROUTABLE`
without mutating legacy rows. An operator in the intended parent pane can
repair exactly one unambiguous legacy row with
`formation repair-parent <worker_id>`; the command derives the parent from the
verified current pane and its locked identity. It also requires the target
child pane to be live and still carry the worker's locked/legacy identity,
then synchronizes that pane's parent options with an in-place registry update
under the registry lock. Before mutation it writes registry, target-row, and
pane-option preimages below `~/sanada_backup_persistent/` (override with
`FORMATION_PARENT_REPAIR_BACKUP_ROOT`) and prints that recovery path.
Set-option or registry failures roll pane options back; closed/recycled child
panes and mismatched non-null routes are refused. An already exact pane+row
pair is a byte-for-byte no-op.

Review work has a separate durable lifecycle. The requester runs
`formation review-request <reviewer-id> <subject>` and retains the printed
review id. Formation sends the id to the reviewer and copies the request to
the requester's manager. The assigned reviewer closes it with
`formation verdict <review-id> <PASS|BLOCK> <summary>`; the verdict is copied
to both requester and manager. `formation reviews --stale-minutes <N>` exposes
unanswered requests directly, so a watcher does not have to infer progress
from mailbox unread counts or pane text.

Lifecycle commands return exit `4` when the row/state is durable but a known
pane could not be signaled. Do not automatically retry `report` or `done` on
that code—the retry would append a duplicate row. A missing or unverified pane
route is pull-only and remains exit `0`, with `signal=unavailable` on stderr.

## Migration from legacy standalone formation

If you previously installed the standalone `formation` CLI via `bash <(curl ...)/install.sh`:

```bash
# remove old symlinks
rm -f ~/.local/bin/formation ~/.claude/skills/formation

# install plugin (above)

# point new symlinks (paths above)
ln -sfn ~/.claude/plugins/harness-formation/bin/formation ~/.local/bin/formation
ln -sfn ~/.claude/plugins/harness-formation/skills/formation ~/.claude/skills/formation
```

New installs use `~/.formation/` runtime state (mailbox/log.jsonl, formation/registry.jsonl). Existing legacy runtime dirs can keep working by setting `FORMATION_HOME` to the old state path.

## Why bundled (vs companion)

Original design kept formation as a separate companion repo so users could opt in. After production use the friction was clear: users had to remember a project-specific trigger phrase each time to spawn workers. The hook closes the loop — natural worker-spawn phrasing now auto-surfaces the skill, no skill-name memorization.

## Credential discipline

Same rules as the standalone `formation` skill (see `skills/formation/SKILL.md`):

- Never paste plaintext credentials into messages, briefings, or pane prompts
- `formation msg` / `formation spawn` hard-refuse credential-shaped bodies (exit 3)
- Reference SOPS-encrypted files by path + decrypt command, not value

## Remote access

Claude workers are named `formation-<id>` and can be selected through Claude
Code `/remote-control` (`/rc`). For Codex, run `formation remote-check` to
detect whether the installed CLI exposes its experimental remote-control
command. That command manages a separate app-server daemon and cannot attach to
an existing Formation worker TUI, so tmux and `formation msg` remain the
supported Codex intervention paths.

## Related

- `harness-core` — credential leak scrub + bash command guard hooks (load-bearing pre-req for credential discipline)
- `harness-magi` — three-perspective preflight review (use before spawning workers on high-stakes tasks)
- `harness-rails` — long-run heartbeat + cron watcher (compose with formation R1-R4 long-run rules)
