# claude-harness

> 🇯🇵 [日本語版 README はこちら](./README_ja.md)

Operational harness for [Claude Code](https://claude.com/claude-code), distilled from 6+ months of self-hosted production use.

> **Why a "harness"?** LLM agents fail in patterned ways — credential leaks, recovery loops, premature script generation. Behavioral rules in `CLAUDE.md` catch some of these, but the same incidents repeat. This marketplace ships **structural** fixes (hooks, guards, reflexive context injection) that don't rely on the agent remembering.

## Plugins

| Plugin | What it does | Trigger |
|---|---|---|
| **harness-core** | 3 hooks: credential value scrub (PostToolUse) + dangerous bash guard (PreToolUse) + admission-keyword workflow reminder (UserPromptSubmit) | Every Bash call + every user prompt |
| **harness-magi** | Three-perspective preflight review skill (MELCHIOR/BALTHASAR/CASPAR personas, parallel `Task` spawn). Front-loads architectural / operational / commercial blind spots before high-stakes changes execute | Walltime ≥ 2h, ≥ 100M row DML, non-reversible cutover, new pipeline layer, ≥ $10 spend, or long-poll scripts |
| **harness-rails** | Operational safety rails for long-running ops: pre-flight algorithm fitness CLI (working set vs RAM), in-flight heartbeat + cron watcher (stale + ETA overrun), Discord + gh issue auto-emit. Human-in-loop only — no auto-kill. | Long-running operations (> 1h walltime); watcher runs via cron `*/1 * * * *` |

Companion repository: [**njslyr7**](https://github.com/hrmtz/njslyr7) ships the `formation` skill + CLI for long-running peer-pane workers in tmux. Install separately via `bash <(curl ...)/install.sh` from that repo.

More plugins are planned (CLAUDE.md persona templates, repo-init skeleton). See [GitHub issues](https://github.com/hrmtz/claude-harness/issues) for status.

## Install

```bash
# in Claude Code
/plugin marketplace add github:hrmtz/claude-harness
/plugin install harness-core@claude-harness
```

After install, Claude Code auto-wires the hooks via `${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json`. No manual `~/.claude/settings.json` edit needed.

### Verify install

```bash
# trigger credential scrub: paste a fake key into a Bash command output
echo 'sk-ant-api03-FAKE_KEY_FOR_TEST_xxxxxxxxxxxxxxxxxxxx'
# expected: hook detects pattern, sanitizes active session jsonl, emits warning

# trigger bash guard: try a forbidden pattern
sops -d secrets.enc.yaml
# expected: PreToolUse blocks with explanation
```

## Manual install (without plugin marketplace)

If you'd rather copy the files directly:

```bash
git clone https://github.com/hrmtz/claude-harness
cp -r claude-harness/plugins/harness-core/hooks/* ~/.claude/hooks/
# then add to ~/.claude/settings.json — see plugins/harness-core/README.md
```

## Philosophy

This marketplace is one half of a larger system. The other half is the philosophy + memory + persona doc:

- **`docs/CLAUDE_HARNESS_DISTILLED.md`** — full design rationale (3-tier memory, 真田/松岡/仗助 persona stack, SOPS 2-command rule, 8 incident timeline → structural fix)

Read that first if you want to understand *why* these hooks exist before installing them.

## Status

- ✅ `harness-core` — production-tested locally
- ✅ `harness-magi` — pure-prompt skill, ships immediately
- ✅ `harness-rails` — production-tested locally on 165M-row HNSW build (see [docs/INCIDENT_23H_HNSW.md](./docs/INCIDENT_23H_HNSW.md))
- 🔗 `formation` skill — lives in [hrmtz/njslyr7](https://github.com/hrmtz/njslyr7) (separate repo, separate install)
- ⏳ `harness-claude-md-template` — paste-able CLAUDE.md skeleton

## License

MIT — see `LICENSE`.
