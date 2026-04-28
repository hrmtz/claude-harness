# harness-core

Three Claude Code hooks that ship together as one defense-in-depth layer against credential leaks and workflow lapses.

## Hooks

### 1. `credential_value_scrub.sh` — PostToolUse / Bash

Scans every Bash tool output for credential value patterns. If matched, **sanitizes the active session jsonl in-place** with `sed -i` (replacing the value with `<REDACTED>`) and emits a warning to Claude.

**Pattern catalog** (extend in the script):

- `sk-ant-*`, `sk-or-*`, `sk_live_*` — Anthropic / OpenRouter / Stripe
- `tskey-*` — Tailscale auth keys
- `AKIA[0-9A-Z]{16}` — AWS access keys
- `eyJ...` — JWT (Turso etc.)
- `gh[ps]_*` — GitHub tokens
- `POSTGRES_PASSWORD=`, `PGPASSWORD=`, `ANTHROPIC_API_KEY=`, `OPENAI_API_KEY=`, `GEMINI_API_KEY=`, `CF_API_TOKEN=` — common env-var-shaped leaks

Allowlist filters out placeholders (`<REDACTED>`, `placeholder`, `example`, `changeme`, `<your-key>`, `YOUR_*`).

> **Important**: This hook is **backward-only damage control**. It sanitizes the session jsonl after the leak. **You still need to rotate the credential** — the value may have already flowed into log shippers, prompt caches, or telemetry.

### 2. `bash_command_guard.sh` — PreToolUse / Bash

Blocks dangerous Bash commands **before** they run, with a deny response and an explanation of the safer alternative.

**Block catalog**:

- `sops -d` / `sops --decrypt` → use `sops edit` or `sops exec-env <file> <cmd>`
- `docker inspect --format '{{.Config.Env}}'` → exposes all container env
- `env | grep KEY/TOKEN/PASSWORD/SECRET` → use `env | cut -d= -f1` for key names only
- `bash -x ... printf $VAR` → `bash -x` expands env vars into transcript
- `cat .env*` / `cat .aws/credentials` → plaintext exposure
- `head/tail *.enc.yaml` → reading sops-encrypted files as plain
- `curl -H "Authorization: Bearer <inline-token>"` → cmdline retention
- `rclone --s3-access-key-id <inline>` → cmdline retention
- `curl '...?api_key=...'` → URL query lands in server logs
- `tail rclone.conf / .netrc / .aws/credentials`

Returns `permissionDecision: deny` via `hookSpecificOutput`. The user / Claude sees the block reason and rewrites the command.

### 3. `admission_reminder.sh` — UserPromptSubmit

When the **user prompt OR the last 3 assistant turns** contain certain admission keywords, injects a reflexive procedure into context as `additionalContext`.

**Pattern catalog** (excerpt):

| Trigger | Reminder |
|---|---|
| `リーク \| leak \| credential.*leak \| password.*expos \| api.*key.*出` | sanitize active jsonl + rotate + log to memory |
| `沈黙 \| silent \| broadcast.*忘 \| 沈黙.*\dh` | lead role status broadcast required (mailbox tail + 4-item broadcast set) |
| `todo \| あとで \| メモ \| 忘れ.*ない` | `gh issue create` externalize, don't keep in head |
| `壊れ \| broken \| 復元.*でき` | persistent backup path reminder |
| `(issue\|memory\|todo\|タスク).*(化\|登録).*(しとく\|する).*\?` | autonomy-scope tasks: just do it, don't ask permission |
| `ちょうど書いた直後.*違反 \| memory 化した瞬間` | recursive meta-violation → structural fix urgent |
| `連発 \| systematic.*failure \| N 回目` | recurrence detected → hook / wrapper required |
| `放置 \| やってない \| 抜けて \| 忘れて` | unfinished work admission → catch up before user calls it out |

Some patterns are project-specific (memory file names, mailbox path) — feel free to fork and adapt the `REMINDERS` dict for your own workflow.

## Manual config (without plugin install)

If you don't want to use the plugin marketplace, add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": "bash ~/.claude/hooks/bash_command_guard.sh", "timeout": 5 }]
    }],
    "PostToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": "bash ~/.claude/hooks/credential_value_scrub.sh", "timeout": 10 }]
    }],
    "UserPromptSubmit": [{
      "hooks": [{ "type": "command", "command": "bash ~/.claude/hooks/admission_reminder.sh", "timeout": 5 }]
    }]
  }
}
```

Then copy the four scripts (`lib.sh`, `credential_value_scrub.sh`, `bash_command_guard.sh`, `admission_reminder.sh`) into `~/.claude/hooks/`.

## Dependencies

- `bash` (5+)
- `jq` — for parsing hook stdin and emitting `hookSpecificOutput` JSON
- `sed` — for in-place jsonl sanitize
- `grep` — POSIX ERE (`grep -E`)

All standard on Linux / macOS. Windows users should use WSL2.

## State / logging

Hooks write logs to `~/.claude/state/hook_logs/hooks.log` and use `~/.claude/state/` for transient state. Existing logs are append-only.

## Customization

- **Add a credential pattern**: edit `credential_value_scrub.sh` `PATTERNS` array, format `'<regex>|<replacement>'`
- **Add a block rule**: edit `bash_command_guard.sh` `PATTERNS_REASONS` array, format `'<regex>:::<explanation>'`
- **Add an admission keyword**: edit `admission_reminder.sh` `REMINDERS` dict, format `['<regex>']='<reminder text>'`

After editing, restart Claude Code (or just start a new session — hooks are loaded at session init).

## Why these three together?

They're **three layers of the same defense**:

1. **Pre-execution gate** (bash_command_guard) — blocks the obvious bad commands before they run
2. **Post-execution sanitize** (credential_value_scrub) — catches what slipped through, scrubs the transcript
3. **Reflexive reminder** (admission_reminder) — when the agent or user **notices something went wrong**, force the recovery procedure into context immediately, don't wait for memory recall

Each layer covers what the others miss. Removing any one creates a gap.
