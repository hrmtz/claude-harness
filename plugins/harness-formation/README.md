# harness-formation

Peer-pane Claude Code worker orchestration. Spawn long-running workers in tmux panes, coordinate via append-only jsonl mailbox, support phone-based remote control via `/rc`. Auto-suggests itself when the user's prompt matches natural-language worker-spawn intent ("裏のclaude にやらせる", "並行で", etc).

> Absorbs the previously-companion [njslyr7](https://github.com/hrmtz/njslyr7) repository (archived). All `formation` CLI + `mailbox_relay` runtime ships inside this plugin.

## What's inside

| Path | What it does |
|---|---|
| `skills/formation/SKILL.md` | Skill definition (when to spawn vs use Task tool, briefing template, R1-R4 long-run rules, credential discipline) |
| `bin/formation` | CLI: `spawn / status / inbox / msg / report / done / ask / reap` |
| `lib/{mailbox,wake,redact,mailbox_relay}.sh` | Helpers sourced by `bin/formation` |
| `hooks/formation_suggest.sh` | UserPromptSubmit hook: detects worker-spawn intent, injects `njslyr7` keyword to surface the skill |

## Trigger keywords (auto-suggest hook)

Mined from real user utterances across 6 months of sessions. The hook fires when any of these patterns match the user prompt:

- `(裏の|他の|別の|違う|もう一人の)(claude|おまえ|お前|キミ|君)` — "裏のお前にやらせる" 100% formation
- `裏で(やっ|やら|走らせ)` — "裏でやって"
- `並[行列](で|して|に).*(claude|task|やる)` — "並行で claude"
- `chichibu-win.*claude` etc — host-specific delegation
- `別(セッション|pane).*(claude|統合)` — "別セッションで統合"
- `njslyr7` / `formation skill` / `spawn.*worker` — direct invocation

## Install

```bash
# in Claude Code
/plugin marketplace add github:hrmtz/claude-harness
/plugin install harness-formation@claude-harness
```

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
3. Hook starts in **shadow mode** (logs matches to `~/.local/log/formation_suggest.log`, doesn't inject). After 24h with no false-positive complaints, switch to active:
   ```bash
   echo 'export FORMATION_SUGGEST_MODE=active' >> ~/.bashrc
   ```

## Migration from njslyr7 standalone

If you previously installed njslyr7 via `bash <(curl ...)/install.sh`:

```bash
# remove old symlinks
rm -f ~/.local/bin/formation ~/.claude/skills/formation

# install plugin (above)

# point new symlinks (paths above)
ln -sfn ~/.claude/plugins/harness-formation/bin/formation ~/.local/bin/formation
ln -sfn ~/.claude/plugins/harness-formation/skills/formation ~/.claude/skills/formation
```

`~/.njslyr7/` runtime state (mailbox/log.jsonl, formation/registry.jsonl) is **preserved** — both implementations share the same runtime layout.

## Why bundled (vs companion)

Original design kept formation as a separate companion repo so users could opt in. After 6 weeks of production use the friction was clear: users had to remember "njslyr7 で formation して" each time to trigger spawn. The hook closes the loop — natural worker-spawn phrasing now auto-surfaces the skill, no skill-name memorization.

## Credential discipline

Same rules as the standalone `formation` skill (see `skills/formation/SKILL.md`):

- Never paste plaintext credentials into messages, briefings, or pane prompts
- `formation msg` / `formation spawn` hard-refuse credential-shaped bodies (exit 3)
- Reference SOPS-encrypted files by path + decrypt command, not value

## Related

- `harness-core` — credential leak scrub + bash command guard hooks (load-bearing pre-req for credential discipline)
- `harness-magi` — three-perspective preflight review (use before spawning workers on high-stakes tasks)
- `harness-rails` — long-run heartbeat + cron watcher (compose with formation R1-R4 long-run rules)
