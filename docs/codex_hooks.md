# Codex Hooks — Discovery Notes and Setup

## Background

This document records the investigation that established Codex hooks work,
and the setup procedure for wiring harness hooks into Codex.

---

## How Codex hooks work (as of v0.130.0, 2026-05)

### Two-layer architecture

| Layer | Feature flag | Status | What it controls |
|---|---|---|---|
| Hook execution engine | `hooks` | **stable / true** | Runtime that runs scripts, blocks tools, injects context |
| Config-based hook loading | `plugin_hooks` | **under development / false** | Loading hooks from `config.toml` or plugin `hooks.json` |

The execution engine is fully implemented. The config-loading layer is gated
behind `plugin_hooks` which defaults to `false`. Once you enable it, hooks
work exactly like Claude Code hooks.

### Why this was confusing

Binary strings analysis showed `"No plugin hooks."` and `"[TODO: ./hooks.json]"`.
The TODO is in a help-text template, not in the execution path. The blocking
code (`"$Command blocked by PreToolUse hook:"`, `HookStartedEvent`,
`HookCompletedEvent`) IS present and functional.

---

## Hook config format

Hooks live in `~/.codex/config.toml`. The TOML format mirrors the plugin
`hooks.json` format:

```toml
[[hooks.PreToolUse]]
matcher = "Bash"            # regex matched against tool_name; omit to match all

[[hooks.PreToolUse.hooks]]
type    = "command"
command = "bash /path/to/hook.sh"
timeout = 5                 # seconds

[[hooks.PostToolUse]]
matcher = "Bash"

[[hooks.PostToolUse.hooks]]
type    = "command"
command = "bash /path/to/hook.sh"
timeout = 10

[[hooks.UserPromptSubmit]]  # no matcher for prompt hooks

[[hooks.UserPromptSubmit.hooks]]
type    = "command"
command = "bash /path/to/hook.sh"
timeout = 5

[features]
plugin_hooks = true         # required
```

Supported events: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`,
`PermissionRequest`, `PreCompact`, `PostCompact`, `SessionStart`, `Stop`.

---

## Hook stdin payload

Codex passes JSON on stdin, identical in structure to Claude Code:

**PreToolUse / PostToolUse**
```json
{
  "session_id": "...",
  "turn_id": "...",
  "transcript_path": "/home/user/.codex/sessions/2026/05/10/rollout-...jsonl",
  "cwd": "/home/user/project",
  "hook_event_name": "PreToolUse",
  "model": "gpt-5.5",
  "permission_mode": "bypassPermissions",
  "tool_name": "Bash",
  "tool_input": { "command": "echo hello" },
  "tool_use_id": "call_..."
}
```

`transcript_path` gives the active session JSONL — use this instead of
scanning `~/.claude/projects/` when running inside Codex.

**Hook output** (same as Claude Code):
```json
{ "hookSpecificOutput": { "hookEventName": "PreToolUse",
                          "permissionDecision": "deny",
                          "permissionDecisionReason": "reason text" } }
```

---

## lib.sh compatibility

`harness-core/hooks/lib.sh` is Codex-aware. Hooks that call multiple lib
functions must set `HOOK_INPUT` before the first call:

```bash
source "$(dirname "$0")/lib.sh"
HOOK_INPUT=$(cat)          # read stdin once
export HOOK_INPUT          # lib functions prefer this over re-reading stdin

OUTPUT=$(parse_tool_output)   # uses HOOK_INPUT
JSONL=$(active_jsonl)         # extracts transcript_path from HOOK_INPUT
```

Hooks that only call one lib function (e.g. `bash_command_guard.sh` which only
calls `emit_context`) do not need `HOOK_INPUT`; the lib functions fall back to
reading stdin directly.

---

## Setup

### Install (one-time)

```bash
bash ~/projects/claude-harness/install-codex-hooks.sh
```

This:
1. Runs `codex features enable plugin_hooks`
2. Strips any existing hook blocks from `~/.codex/config.toml`
3. Appends the harness-core hook config (PreToolUse / PostToolUse / UserPromptSubmit)
4. Verifies the config parses

### Trust step (one-time per machine)

Codex requires explicit trust before running any hook script:

1. Start any Codex session: `codex "hello"`
2. Press **Tab** → Hooks panel opens
3. Press **Enter** on a row that shows `Review > 0`
4. Press **t** to trust the hook
5. Press **Esc** → repeat for other events with pending review

Trust state is stored in `~/.codex/config.toml` under `[hooks.state]` as a
SHA-256 hash of the hook config. If you re-run `install-codex-hooks.sh`
(which rewrites the hooks block), the hashes become stale and you must
re-trust.

---

## What each hook does in Codex

| Hook | Event | Function |
|---|---|---|
| `bash_command_guard.sh` | PreToolUse / Bash | Blocks SOPS credential exposure patterns, `env \| grep KEY`, etc. Same pattern catalog as Claude Code. |
| `credential_value_scrub.sh` | PostToolUse / Bash | Scans tool output for credential values; sanitizes active session JSONL in-place. Uses `transcript_path` from hook JSON to locate the Codex session file. |
| `admission_reminder.sh` | UserPromptSubmit | Injects terse reminders for known failure modes (credential leak, silent lead, etc.) |

---

## Differences vs Claude Code

| Aspect | Claude Code | Codex |
|---|---|---|
| Hook config location | `~/.claude/settings.json` | `~/.codex/config.toml` |
| Feature gate | none (always on) | `plugin_hooks = true` required |
| Trust step | none | one-time interactive `/hooks` → `t` per machine |
| Session JSONL path | `~/.claude/projects/<hash>/*.jsonl` | `~/.codex/sessions/<date>/rollout-*.jsonl` (in `transcript_path`) |
| Plugin hook support | GA | `plugin_hooks` (under development) — config.toml route is the stable path |

---

## Troubleshooting

**`⚠ 1 hook needs review before it can run`** on startup  
→ Complete the trust step: Tab → Enter → t

**Hook fires but `active_jsonl` returns empty / wrong file**  
→ Ensure `HOOK_INPUT=$(cat); export HOOK_INPUT` is at the top of the hook
  script before calling any lib.sh function.

**Config parse error on Codex startup**  
→ Run `install-codex-hooks.sh` again; it strips and rewrites the hooks block
  cleanly.

**After re-running install script, hooks show `Review > 0` again**  
→ Expected. The trust hashes cover the config content; rewriting the block
  invalidates them. Re-trust via Tab → Enter → t.

**`plugin_hooks` warning on every Codex startup**  
→ Add to `~/.codex/config.toml`:
```toml
suppress_unstable_features_warning = true
```
