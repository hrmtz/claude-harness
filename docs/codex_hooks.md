# Codex Hooks — Discovery Notes and Setup

## Background

This document records the investigation that established Codex hooks work,
and the setup procedure for wiring harness hooks into Codex.

---

## How current Codex hooks work

### Stable lifecycle feature

The canonical feature is `hooks`, currently reported as stable. The former
`plugin_hooks` feature has been removed and must not be enabled or documented
as a prerequisite. Matching hooks from all active config and plugin sources are
merged, and matching command hooks for an event run concurrently.

Codex discovers inline hook tables in `config.toml`, adjacent `hooks.json`
files, and enabled plugins. User hooks load independently of project trust;
project-local hooks require the project config layer to be trusted.

### Why this was confusing

Binary strings analysis showed `"No plugin hooks."` and `"[TODO: ./hooks.json]"`.
The TODO is in a help-text template, not in the execution path. The blocking
code (`"$Command blocked by PreToolUse hook:"`, `HookStartedEvent`,
`HookCompletedEvent`) IS present and functional.

---

## Hook config format

Hooks can live inline in `~/.codex/config.toml`, in an adjacent
`~/.codex/hooks.json`, or in an enabled plugin. This installer owns a marked
inline TOML block:

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

```

Supported command-hook events: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`,
`PermissionRequest`, `PreCompact`, `PostCompact`, `SessionStart`,
`SessionEnd`, `SubagentStart`, `SubagentStop`, and `Stop`.

Tool matchers cover shell/unified exec as `Bash`, `apply_patch` (also aliased
as `Edit` and `Write`), MCP tool names, and most local function tools.
`spawn_agent` also matches `Agent`. Hosted tools such as `WebSearch` do not
currently traverse the local tool-hook path.

---

## Hook stdin payload

Codex passes a Claude-compatible JSON envelope on stdin with Codex extensions.
Event-specific fields and the rollout JSONL records are not identical:

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

`Stop` also includes `last_assistant_message`; use that stable field when only
the final response is needed. `SubagentStop` includes `agent_id`, `agent_type`,
and `agent_transcript_path`; its ordinary `transcript_path` points at the parent.
To request another turn, return `{"decision":"block","reason":"..."}`. Codex
turns `reason` into the continuation prompt. `continue:false` stops processing;
it is not the continuation form.

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
1. Verifies the canonical stable `hooks` feature, enabling it only when present
   but disabled; unsupported older versions fail with an upgrade message.
2. Replaces only the marker-bounded hook block owned by claude-harness. Hooks
   from other installers and `[hooks.state]` trust records are preserved.
3. Generates and appends the hook config from `plugins/cross_cli_hooks.json`
   (hook set) + each plugin's `hooks/hooks.json` (event/matcher/timeout SSOT —
   the same files that drive Claude via `sync_hooks_to_live.py`)
4. Verifies the config parses

To add/remove a Codex hook: edit the `codex` section of
`plugins/cross_cli_hooks.json`, re-run the installer, re-trust. Script
*content* changes need no re-run — the config references repo paths directly.
Check sync state anytime with:

```bash
bash ~/projects/claude-harness/scripts/check_cross_cli_hooks.sh --live
```

The live check reads only the `BEGIN/END claude-harness managed hooks` block.
Hooks owned by other installers are ignored; missing and duplicate harness
commands are reported separately. Re-running the installer is the
non-destructive repair path and preserves those unrelated hook sources.

### Trust step (one-time per machine)

Codex requires explicit trust before running any hook script:

1. Start any Codex session: `codex "hello"`
2. Run **`/hooks`** to open the hook browser.
3. Review each new or changed definition and trust it.
4. Repeat for event rows that still show pending review.

Trust state is stored in `~/.codex/config.toml` under `[hooks.state]` as a
SHA-256 hash of the hook config. If you re-run `install-codex-hooks.sh`
(which rewrites the hooks block), the hashes become stale and you must
re-trust.

---

## What each hook does in Codex

The authoritative hook set lives in `plugins/cross_cli_hooks.json` (`codex`
section). It includes the portable Bash gates (sanada, bash_command_guard,
branch_policy, pg_rotation, pipeline_preflight, phase_review,
long_task_advisor), patch-aware Write/Edit rails (`check_zsh_reserved_vars`,
`check_early_check_timer`, `ssh_fanout_canonical_check`), Stop hooks
(`stall_autocontinue`, `sr_depth_gate`), Bash PostToolUse hooks
(`credential_value_scrub`, `credential_scrub`, `self_check_reminder`,
`vastai_create_followup_check`), UserPromptSubmit hooks (`admission_reminder`,
`code_review_suggest`, `formation_suggest`), and SessionStart context
(`temporal_anchor`, hippocampus injector, codex tmux self-name). Hook behavior
uses the same scripts and pattern catalogs where the Codex payload shape is
compatible. Notable Codex-specific points:

| Hook | Event | Codex-specific note |
|---|---|---|
| `credential_value_scrub.sh` | PostToolUse / Bash | Uses `transcript_path` from hook JSON to locate the Codex session file under `~/.codex/sessions/`. |
| `credential_scrub.sh` | PostToolUse / Bash and compatible tool events | Sources repo-local `lib.sh`, then uses `transcript_path` rather than Claude project scanning when available. |
| `formation_suggest.sh` | UserPromptSubmit | Emits JSON `additionalContext` so Codex honors the hint when `FORMATION_SUGGEST_MODE=active`. |
| `versioning_autorun.py` | PostToolUse / Bash | After a main-branch `git push`, auto-detects semver bump, tags, and creates a GitHub Release. Docs-only pushes no-op. |
| `sr_depth_gate.py` | Stop, SubagentStop | Normalizes Claude messages and Codex rollout records; SubagentStop reads `agent_transcript_path`. |
| `stall_autocontinue.sh` | Stop | Uses Codex's stable `last_assistant_message` field instead of parsing its unstable rollout format. |
| `check_zsh_reserved_vars.sh` | PreToolUse / `apply_patch` | Blocks only when the patch itself shows zsh context (`.zsh` path or zsh shebang), avoiding false positives on bash `.sh` hunks. |
| `check_early_check_timer.sh`, `ssh_fanout_canonical_check.sh` | PreToolUse / `apply_patch` | Extract added lines from the patch body and run the existing warning checks against those lines. |
| `credential_file_read_guard.sh` parity | PreToolUse / Bash | Codex has no standalone Read tool in the observed payloads; the same sensitive-file coverage is enforced through `bash_command_guard.sh` for `exec_command` reads. |

---

## Differences vs Claude Code

| Aspect | Claude Code | Codex |
|---|---|---|
| Hook config location | `~/.claude/settings.json` | `~/.codex/config.toml` |
| Feature gate | none (always on) | canonical `hooks` feature (stable) |
| Trust step | none | review changed definitions in interactive `/hooks` |
| Session JSONL path | `~/.claude/projects/<hash>/*.jsonl` | `~/.codex/sessions/<date>/rollout-*.jsonl` (in `transcript_path`) |
| Plugin hook support | GA | stable; default `hooks/hooks.json` or manifest `hooks` entry |

---

## Troubleshooting

**`⚠ 1 hook needs review before it can run`** on startup
→ Open `/hooks`, review the pending definition, and trust it.

**Hook fires but `active_jsonl` returns empty / wrong file**  
→ Ensure `HOOK_INPUT=$(cat); export HOOK_INPUT` is at the top of the hook
  script before calling any lib.sh function.

**Config parse error on Codex startup**  
→ Run `install-codex-hooks.sh` again; it strips and rewrites the hooks block
  cleanly.

**After re-running install script, hooks show `Review > 0` again**  
→ Expected. The trust hashes cover the config content; rewriting the block
  invalidates them. Re-trust via Tab → Enter → t.

**Installer reports canonical `hooks` as unknown or removed**
→ Upgrade Codex. The installer fails closed instead of claiming that a removed
feature was enabled.
