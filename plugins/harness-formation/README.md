# harness-formation

Peer-pane Claude Code and Codex worker orchestration. Spawn long-running workers in tmux panes and coordinate through an append-only jsonl mailbox. Claude workers also support Claude Code phone/web remote control via `/rc`; Codex workers use tmux or mailbox messages. Auto-suggests itself when the user's prompt matches natural-language worker-spawn intent ("裏のclaude にやらせる", "並行で", etc).

> All `formation` CLI + `mailbox_relay` runtime ships inside this plugin.

## What's inside

| Path | What it does |
|---|---|
| `skills/formation/SKILL.md` | Skill definition (when to spawn vs use Task tool, briefing template, R1-R4 long-run rules, credential discipline) |
| `bin/formation` | CLI: worker coordination plus the read-only `integration-audit` report |
| `bin/formation-mail-nudge` | Optional one-shot/watching escalation for ignored badges; never starts automatically |
| `bin/install-formation-mail-nudge-service` | Explicit systemd user-service install/uninstall for the optional watcher |
| `bin/formation-window-status` | Explicit journaled tmux window-list apply/status/revert tool |
| `lib/mailbox.sh` | Durable JSONL storage, locking, sequence allocation, and read cursors |
| `lib/mailbox_delivery.sh` | Shared recipient/sender resolution, relay delegation, and exclusive-injection policy |
| `lib/mailbox_notify.sh`, `lib/mailbox_relay.sh` | Zero-keystroke pane signaling primitives and the per-worker signal daemon |
| `lib/requests.sh` | Durable semantic ASK/ACK/resolve state, separate from transport |
| `lib/wake.sh`, `lib/redact.sh` | The single exceptional submit primitive and shared credential refusal/audit |
| `hooks/formation_suggest.sh` | UserPromptSubmit hook: detects worker-spawn intent, injects a formation keyword to surface the skill |

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

```bash
formation-mail-nudge --dry-run
formation-mail-nudge                 # one sweep
formation-mail-nudge --watch         # foreground watcher
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

ASKs are durable semantic state, stored separately from mailbox transport.
`formation ask` returns a request id and makes the worker `WAITING_PARENT`;
the parent closes it explicitly with `formation ack` or `formation resolve`.
`formation status` keeps unresolved requests visible after later reports, and
`formation reap` refuses them unless `--force` is explicit. Spawn also passes
the parent's pane route separately from its semantic identity: worker
`report`/`done`/`ask` and parent `ack`/`resolve` all append first, then use the
same zero-keystroke relay-or-direct signal policy. A dead relay therefore does
not silently remove the badge fallback, and the body never enters a prompt.
The parent pane is discovered by process ancestry rather than trusting
`TMUX_PANE`; stale/inherited sibling ids are ignored. Spawn fails closed when
it can prove neither a real parent pane nor an explicit `FORMATION_SELF`,
because replies would otherwise be unaddressable.
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
