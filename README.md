# claude-harness

Operational harness for [Claude Code](https://claude.com/claude-code), distilled from 6+ months of self-hosted production use.

> **Why a "harness"?** LLM agents fail in patterned ways — credential leaks, recovery loops, premature script generation. Behavioral rules in `CLAUDE.md` catch some of these, but the same incidents repeat. This marketplace ships **structural** fixes (hooks, guards, reflexive context injection) that don't rely on the agent remembering.

## Plugins

| Plugin | What it does | Trigger |
|---|---|---|
| **harness-core** | 3 hooks: credential value scrub (PostToolUse) + dangerous bash guard (PreToolUse) + admission-keyword workflow reminder (UserPromptSubmit) | Every Bash call + every user prompt |

More plugins are planned (formation skill for long-running tmux pane workers, CLAUDE.md persona templates) — see `docs/ROADMAP.md` once published.

## Install

```bash
# in Claude Code
/plugin marketplace add github:<your-fork>/claude-harness-marketplace
/plugin install harness-core@claude-harness
```

> Replace `<your-fork>` with this repo's owner once forked / cloned to GitHub.

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
git clone https://github.com/<your-fork>/claude-harness-marketplace
cp -r claude-harness-marketplace/plugins/harness-core/hooks/* ~/.claude/hooks/
# then add to ~/.claude/settings.json — see plugins/harness-core/README.md
```

## Philosophy

This marketplace is one half of a larger system. The other half is the philosophy + memory + persona doc:

- **`docs/CLAUDE_HARNESS_DISTILLED.md`** — full design rationale (3-tier memory, 真田/松岡/仗助 persona stack, SOPS 2-command rule, 8 incident timeline → structural fix)

Read that first if you want to understand *why* these hooks exist before installing them.

## Status

- ✅ `harness-core` (this commit) — production-tested locally
- ⏳ `harness-formation` — pending public release of [njslyr7](https://github.com/<your-fork>/njslyr7) (tmux pane peer-worker daemon)
- ⏳ `harness-claude-md-template` — paste-able CLAUDE.md skeleton

## License

MIT — see `LICENSE`.
