# Grok Hooks — Discovery Notes and Setup

## Background

This document records the harness-grok port (gh #55) — how Grok's native hook
API differs from Claude Code / Codex, the defect the port closes, and the setup
procedure for wiring harness hooks into Grok.

Unlike Kimi at the time (no native hook API → BASH_ENV interception; Kimi >= 0.28
later shipped native hooks — see `kimi_hooks.md`),
Grok has a **native hook engine** modeled closely on Claude Code, so the port is
a close cousin of the Codex one: a curated hook set in `cross_cli_hooks.json`
plus an installer that writes Grok's config. The catch is that Grok's *payload
shape* and *deny shape* differ, and a hook that only reads the Claude shape
**silently passes** a Grok tool call (fail-open) — the exact hole this port
fixes.

---

## The defect (confirmed 2026-07-03)

```bash
# Claude/Codex shape → deny (correct)
printf '%s' '{"tool_input":{"command":"sops -d secrets.enc.yaml"}}' \
  | bash plugins/harness-core/hooks/bash_command_guard.sh

# Grok shape (pre-port) → empty stdout, tool runs (dangerous command passes)
printf '%s' '{"toolInput":{"command":"sops -d secrets.enc.yaml"}}' \
  | bash plugins/harness-core/hooks/bash_command_guard.sh
```

| Gap | harness expected | Grok actual |
|---|---|---|
| Tool input | `.tool_input.command` (snake) | `.toolInput.command` (camel) |
| Deny output | `hookSpecificOutput.permissionDecision` | `{"decision":"deny","reason":...}` |
| Session JSONL | `.transcript_path` | none — `GROK_SESSION_ID` + `GROK_WORKSPACE_ROOT` env |
| Tool name | `Bash`, `Read`, `Write` | `run_terminal_command`, `read_file`, `search_replace` (matcher aliased Grok-side) |

Worst case: the hook runs but silently passes → combined with Grok's fail-open
error handling, the guard is inert. `lib.sh` now absorbs all four gaps so the
same hook scripts work under Claude, Codex, and Grok.

---

## How Grok hooks work

### Discovery

Native hook engine, documented in `~/.grok/docs/user-guide/10-hooks.md`. Hooks
are discovered from several merged sources:

| Scope | Path | Trusted? |
|---|---|---|
| Global | `~/.grok/hooks/*.json` | **always** |
| Global | `~/.claude/settings.json` (compat) | always (configurable) |
| Project | `<project>/.grok/hooks/*.json` | requires folder-trust |

The harness installs a **global** file (`~/.grok/hooks/harness.json`) — always
trusted, no per-project trust step.

### Hook config format

Grok uses JSON (not Claude's settings.json wrapper, not Codex TOML):

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash",
        "hooks": [ { "type": "command", "command": "bash /abs/path/hook.sh", "timeout": 5 } ] }
    ],
    "PostToolUse": [
      { "matcher": "Bash",
        "hooks": [ { "type": "command", "command": "bash /abs/path/scrub.sh", "timeout": 10 } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "bash /abs/path/admission.sh", "timeout": 5 } ] }
    ]
  }
}
```

**Matcher rule**: `matcher` is only valid on the tool/notification events
(`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionDenied`,
`Notification`). Lifecycle events (`SessionStart`, `SessionEnd`, `Stop`,
`UserPromptSubmit`, …) **reject** a matcher — the installer drops it for those.
Grok aliases `matcher:"Bash"` to also match `run_terminal_command`, so a hook
written for `Bash` fires on Grok's shell tool.

### Hook stdin payload

**PreToolUse / PostToolUse**
```json
{
  "hookEventName": "pre_tool_use",
  "sessionId": "abc-123",
  "cwd": "/home/you/project",
  "workspaceRoot": "/home/you/project",
  "toolName": "run_terminal_command",
  "toolInput": { "command": "npm test" },
  "timestamp": "2026-04-14T12:00:00Z"
}
```

Note `toolInput` / `toolName` / `sessionId` (camelCase), and no
`transcript_path`.

### Hook output (blocking)

```json
{ "decision": "deny", "reason": "reason text" }
```

- Exit `2` is also treated as an explicit deny.
- A stdout `deny` is honored **regardless of exit code**.
- Everything else is **fail-open**: a crash, timeout, or malformed output does
  NOT block the tool. To enforce policy a hook must run to completion and emit an
  explicit `deny` — which is why `emit_deny` never relies on exit codes alone.

### Environment variables (runner-injected, always present)

| Var | Meaning |
|---|---|
| `GROK_HOOK_EVENT` | event name (`pre_tool_use`, `post_tool_use`, …) |
| `GROK_SESSION_ID` | session UUID |
| `GROK_WORKSPACE_ROOT` | workspace absolute path |
| `CLAUDE_PROJECT_DIR` | Claude-compat alias for the workspace root |

These are set on **every** Grok hook process, including hooks loaded via the
Claude-compat source — so `lib.sh` can reliably detect "am I under Grok?".

### Session transcript path

Grok stores the transcript at:

```
~/.grok/sessions/<percent-encoded-workspace>/<sessionId>/chat_history.jsonl
```

e.g. `~/.grok/sessions/%2Fhome%2Fhrmtz%2Fprojects%2Fclaude-harness/<uuid>/chat_history.jsonl`.
`lib.sh`'s `active_jsonl()` resolves it by **globbing the unique sessionId**
(`*/<sid>/chat_history.jsonl`) rather than re-deriving Grok's percent-encoding —
robust to whatever `quote()`-variant Grok uses. The `chat_history.jsonl` lines
are `{"type":"assistant","content":"<string>"}` (plain-string content, unlike
Claude's `.message.content[].text` array); `recent_assistant_turns()` handles
both.

---

## lib.sh compatibility

`harness-core/hooks/lib.sh` is Grok-aware. Hooks set `HOOK_INPUT` once and call
the shared helpers, which accept BOTH shapes:

```bash
source "$(dirname "$0")/lib.sh"
HOOK_INPUT=$(cat); export HOOK_INPUT

CMD=$(parse_tool_command)   # .tool_input.command // .toolInput.command
# ... pattern match ...
emit_deny "$MSG"            # Claude/Codex hookSpecificOutput, OR Grok {"decision":"deny"}
```

| Helper | Cross-CLI behavior |
|---|---|
| `parse_tool_command` | `.tool_input.command` // `.toolInput.command` |
| `parse_tool_file_path` | `.tool_input.file_path` // `.toolInput.file_path` // `.toolInput.path` |
| `parse_tool_content` | `.content` // `.new_string` (either case) |
| `parse_prompt` | `.prompt` // `.userPrompt` // … |
| `parse_tool_output` | `.tool_response.*` // `.toolResponse.*` // `.toolOutput` (+ tostring) |
| `emit_deny` | Grok shape iff `GROK_SESSION_ID`/`GROK_HOOK_EVENT` set, else Claude/Codex shape. Exits 0. |
| `active_jsonl` | `transcript_path` → Grok sessionId glob → `~/.claude/projects/` scan |

`emit_deny` emits exactly ONE shape (detected from Grok env), never two JSON
objects: Claude parses stdout as a single JSON document, so a second object
would break its parse and fail-open the deny.

---

## Setup

### Install (one-time)

```bash
bash ~/projects/claude-harness/install-grok-hooks.sh
```

This:
1. Backs up any existing `~/.grok/hooks/harness.json`
2. Generates `harness.json` from `plugins/cross_cli_hooks.json` (`grok` section)
   + each plugin's `hooks/hooks.json` (event/matcher/timeout SSOT — the same
   files that drive Claude via `sync_hooks_to_live.py`)
3. Validates and installs it atomically

Global hooks are always trusted — **no interactive trust step** (unlike Codex).

To add/remove a Grok hook: edit the `grok` section of
`plugins/cross_cli_hooks.json`, re-run the installer. Script *content* changes
need no re-run — `harness.json` references repo paths directly.

### Avoid double-fire

Grok also scans `~/.claude/settings.json` by default (`[compat.claude] hooks =
true`). If those Claude entries include the same harness hooks, a single Bash
call runs each guard **twice**. Add to `~/.grok/config.toml`:

```toml
[compat.claude]
hooks = false
```

so only the native `harness.json` set fires. (Alternative: remove the harness
hook block from `~/.claude/settings.json`, but disabling compat is cleaner and
reversible.)

### Verify

```bash
grok /hooks    # harness.json appears under Global

# deny fires (expect {"decision":"deny",...}):
printf '%s' '{"toolName":"run_terminal_command","toolInput":{"command":"sops -d x.enc.yaml"}}' \
  | GROK_SESSION_ID=test bash plugins/harness-core/hooks/bash_command_guard.sh

# drift between overlay and installed harness.json:
bash scripts/check_cross_cli_hooks.sh --live
```

---

## What each hook does in Grok

The authoritative set lives in `plugins/cross_cli_hooks.json` (`grok` section):

| Hook | Event | Grok note |
|---|---|---|
| `sanada_autobackup.sh` | PreToolUse / Bash | silent backup insurance |
| `bash_command_guard.sh` | PreToolUse / Bash | `emit_deny` → `{"decision":"deny"}` |
| `branch_policy_guard.sh` | PreToolUse / Bash | main-commit/push gate |
| `pg_rotation_propagation_guard.sh` | PreToolUse / Bash | rotation→propagation gate |
| `pipeline_preflight_gate.sh` | PreToolUse / Bash | blocks via `exit 2` (Grok honors as deny); reason on stderr scrollback |
| `phase_review_gate.sh` | PreToolUse / Bash | same `exit 2` block path |
| `long_task_advisor.sh` | PreToolUse / Bash | advisory (Grok may ignore PreToolUse additionalContext) |
| `credential_value_scrub.sh` | PostToolUse / Bash | `active_jsonl` resolves Grok `chat_history.jsonl` by sessionId |
| `admission_reminder.sh` | UserPromptSubmit | context injection — **Verifier must confirm** Grok honors passive-event stdout |

---

## Differences vs Claude Code

| Aspect | Claude Code | Grok |
|---|---|---|
| Hook config | `~/.claude/settings.json` | `~/.grok/hooks/harness.json` (global) |
| Format | JSON (settings wrapper) | JSON (`{"hooks":{...}}`) |
| Trust step | none | none for global hooks (`~/.grok/hooks/`) |
| Deny shape | `hookSpecificOutput.permissionDecision` | `{"decision":"deny","reason":...}` |
| Payload keys | `tool_input` / `tool_name` / `session_id` | `toolInput` / `toolName` / `sessionId` |
| Session JSONL | `~/.claude/projects/<hash>/*.jsonl` | `~/.grok/sessions/<enc-workspace>/<sid>/chat_history.jsonl` |
| Error handling | deny on non-zero | **fail-open** on anything but explicit deny |

---

## Known limitations (Phase 1 scope)

実機で要確認の残点。Verifier は §Verification record の該当行で pass/fail を記録すること。

1. **`UserPromptSubmit` の `additionalContext` 注入 — Grok が honor するか未確認。**
   `admission_reminder.sh` は Claude 形式の `hookSpecificOutput.additionalContext`
   を stdout に出すが、Grok user guide は passive event の stdout を無視すると明記している。
   wiring 自体は入っている（overlay + installer）。注入が効かない場合は Phase 1 の
   機能退化であり、block 系には影響しない。Verifier: プロンプトに admission keyword を
   含め、次ターン context に reminder が載るか確認（§Verification record の
   `admission keyword → inject` 行）。

2. **Read/Write guard — Phase 1.5。** `parse_tool_file_path` / `parse_tool_content` は
   `lib.sh` に準備済みだが、`credential_file_read_guard.sh` 等の matcher は
   `cross_cli_hooks.json` の `grok` セクションに未登録（Grok の `read_file` /
   `search_replace` の実 payload でフィールド名を1回ログ取得してから overlay に足す）。

その他（block 動作は確認済み想定、reason の見え方のみ）:

- **`pipeline_preflight_gate` / `phase_review_gate` deny reason** — 既存の `exit 2`
  パス。structured `reason` ではなく stderr + scrollback annotation に載る。block 自体は効く。

---

## Troubleshooting

**Grok payload passes a dangerous command**
→ Confirm `lib.sh` is the ported version: `parse_tool_command` must read
  `.toolInput.command`. Test with the camelCase payload above.

**`active_jsonl` returns empty under Grok**
→ Needs `GROK_SESSION_ID` (runner-injected) or `.sessionId` in the payload, and
  an existing `~/.grok/sessions/*/<sid>/chat_history.jsonl`.

**Same guard fires twice per command**
→ Double-fire from `[compat.claude] hooks = true`. Set it to `false` in
  `~/.grok/config.toml`.

**Hook not listed in `/hooks`**
→ Press `r` in the Hooks tab to reload from disk, or confirm `harness.json` is
  valid: `jq empty ~/.grok/hooks/harness.json`.

**Debug logs**
→ `RUST_LOG=debug GROK_LOG_FILE=/tmp/grok.log grok`, then read `/tmp/grok.log`.

---

## Verification record

_(Filled by the Grok Verifier per the port SKILL §6.7.)_

## Verification record (Grok v0.2.82, harness-grok, 2026-07-03) — **Verifier GREEN (Phase 1)**

- [x] install-grok-hooks.sh — `wrote ~/.grok/hooks/harness.json (set: 9 hooks)` (re-run after `[compat.claude] hooks = false`)
- [x] /hooks shows harness.json (Global) — `grok inspect`: 9× active `user` hooks (no `[claude]`); all `~/.claude/settings.json` harness hooks show `[disabled]`; `jq empty` OK
- [x] sops -d → deny — hook layer: Grok payload stdin sim → `{"decision":"deny",...}`; session layer: headless `grok -p 'Execute: sops -d …'` refused before Bash (CLAUDE.md SOPS rail — defense in depth; hook runner deny not separately isolated in that probe)
- [x] benign echo → pass — empty stdout on Grok payload stdin sim
- [x] admission keyword → inject — **Phase 1 accepted**: `admission_reminder.sh` emits `additionalContext` on `UserPromptSubmit` stdin sim; headless probe with `credential leak` did **not** surface scrub/rotate phrasing in the model reply → treat as **Known limitation #1** (passive stdout may be ignored); wiring is harmless
- [x] fake sk-ant → scrubbed in chat_history.jsonl — `active_jsonl` → `~/.grok/sessions/.../<sid>/chat_history.jsonl`; PostToolUse sim (`toolResponse.stdout` leak) → scrub warning + no `sk-ant-api03-FAKE` residue in live file
- [x] check_cross_cli_hooks.sh --live (grok) — 9 hook basenames match overlay; installer uses absolute paths (`bash /home/.../plugins/...`) so byte-identical diff vs overlay's `bash plugins/...` is expected. Unrelated codex drift still fails whole `--live` (duplicate `codex_session_start.sh`)
- [x] no double-fire — `~/.grok/config.toml`: `[compat.claude] hooks = false`; `grok inspect`: 0 enabled `[claude]` Bash matchers; `bash_command_guard` active only via `harness.json`

**Sign-off notes**

- Phase 1 **block + scrub** verified under Grok payload shape (`lib.sh` + overlay + installer).
- Known limitations #1–#2 remain documented above; they do not block Phase 1 ship.
- Operator close-out: ff-merge to `main` + tag (out of Verifier scope).
