# Watcher Guardrail Fixes v2 (claude-harness #15, #17)

_status: REVISED after dual-magi round 1 · 2026-06-07_

> **v2 changes (round 1 critical findings):**
> - Fix A: `TOOL_INPUT_FILE_PATH` is fake → stdin jq parse; PostToolUse → PreToolUse; shebang skip; regex narrowed
> - Fix B: copy-paste snippet ABOLISHED → `safety-rails-beat` CLI already exists at `~/.local/bin/safety-rails-beat`
> - Fix C: heartbeat detection broadened; `until.*do` added; comment-line bypass fixed
> - Fix D (new): `scripts/_watcher_template.sh` as PRIMARY prevention mechanism

## §0 Problem statement

Two consecutive silent failures on shard2 (2026-06-07):

1. **Failure 1**: Node stuck loading → watcher TIMEOUT → 1.5h idle billing
2. **Failure 2**: `status=$(vastai ...)` in zsh → `status` is read-only → silent crash → 8h idle

Root cause: **watcher scripts have no self-monitoring AND contain zsh reserved variable bugs**.

Also discovered: `_early_check_timer.py` was created as a guardrail, then immediately NOT used in the next watcher. **Guardrails that require manual opt-in will be missed.**

## §1 Four fixes (v2)

### Fix A: PreToolUse Write/Edit hook — detect zsh reserved variable assignments

When a bash/sh/zsh script is written or edited, scan content for dangerous assignments BEFORE the file lands.

**Verified read-only in zsh (confirmed on chichibu):**
- `status` — read-only (the actual crash culprit)
- `LINENO` — read-only
- `PPID` — read-only (`zsh -c 'PPID=$(cmd)'` → exit 1)
- `HISTCMD` — read-only

**Excluded (confirmed safe/assignable):**
- `SIGNAL`, `signals` — writable in zsh (doc was wrong; removed)
- `FUNCNAME` — bash-only concept, not zsh reserved
- `SECONDS` — assignable but resets timer (warn separately in template comment)

**Implementation:**
```bash
# PreToolUse:Write + Edit hook: check_zsh_reserved_vars.sh
# Read target path + content from stdin (canonical pattern per ssh_fanout_canonical_check.sh)
payload=$(head -c 131072 || true)
file=$(echo "$payload" | jq -r '.tool_input.file_path // ""' 2>/dev/null)
content=$(echo "$payload" | jq -r '.tool_input.content // .tool_input.new_string // ""' 2>/dev/null)

[[ -z "$content" ]] && exit 0
[[ "$content" == *'#!/bin/bash'* || "$content" == *'#!/usr/bin/env bash'* ]] && exit 0  # bash-only files: zsh vars are benign
[[ "$file" != *.sh && "$file" != *.bash && "$file" != *.zsh && "$file" != "" ]] && exit 0

# Strip comment lines before pattern match (avoid false-positives from doc comments about these vars)
clean=$(echo "$content" | grep -v '^[[:space:]]*#')
RESERVED='(^|[[:space:];|&])(status|LINENO|PPID|HISTCMD)='
if echo "$clean" | grep -qE "$RESERVED"; then
    echo "⚠️  zsh read-only variable assignment detected in $file"
    echo "   'status'/'LINENO'/'PPID'/'HISTCMD' are read-only in zsh → silent crash"
    echo "   Use: node_status / http_status / api_status / lineno / ppid etc."
    exit 2  # block (not warn-only: this caused 8h idle billing)
fi
exit 0
```

**False-positive mitigation:**
- `#!/bin/bash` shebang → skip (zsh reserved vars don't apply)
- Strip comment lines (avoid firing on `# never use status=` documentation)
- Compound names like `node_status=` or `h_status=` do NOT match (prefix anchored to space/semicolon/pipe/ampersand)

### Fix B: Use existing `safety-rails-beat` CLI — NOT copy-paste snippets

**Round 1 finding (F10/C2): `safety_rails.heartbeat` + `safety-rails-beat` CLI already exist at `~/.local/bin/safety-rails-beat`. Fix B is a POINTER to existing infrastructure, not new code.**

Every new bash watcher MUST call `safety-rails-beat` inside its polling loop:

```bash
# In watcher script — inside the polling loop:
safety-rails-beat "$LABEL" "$done" "$total" 2>/dev/null || true
```

For Python watcher scripts, use the existing `_early_check_timer.start_early_check()`.

**No copy-paste heartbeat snippets.** When the API changes, there is ONE file to update.

### Fix C: Extend `check_early_check_timer.sh` — cover bash polling loops

Extend the existing hook to also detect bash watcher patterns missing `safety-rails-beat` or `_early_check_timer`.

**v2 changes vs doc:**
- Detection: `while.*do|until.*do|watch -n [0-9]` (added `until.*do`, `watch -n`)
- Heartbeat check: `safety-rails-beat|discord_notify|discord-bot|_early_check_timer` (broadened, existing scripts match)
- Strip comment lines before heartbeat check (fix bypass-via-comment gap)
- Python `.py` files: check `while True:.*time\.sleep` for missing `_early_check_timer` import

```bash
# Addition to check_early_check_timer.sh (bash watcher block):
if [[ "$file" == *.sh || "$file" == *.bash ]]; then
    clean=$(echo "$content" | grep -v '^[[:space:]]*#')
    if echo "$clean" | grep -qE 'while[[:space:]]+[^;]+do|until[[:space:]]+[^;]+do|watch -n [0-9]'; then
        if ! echo "$clean" | grep -qE 'safety-rails-beat|discord_notify|discord-bot|_early_check_timer'; then
            echo "⚠️  polling loop in $file — add safety-rails-beat call inside the loop"
        fi
    fi
fi
```

### Fix D (new): `scripts/_watcher_template.sh` — PRIMARY prevention

Primary prevention: a canonical template that new watchers are copied from, containing correct patterns baked in. This fixes the 13 existing PRS-LLM-dev watchers that won't be caught by new-file hooks.

```bash
#!/usr/bin/env bash
# _watcher_template.sh — copy this for any vastai/long-running watcher
# IMPORTANT: do NOT use 'status' as a variable name in zsh (read-only → silent crash)
#            Use: node_status, http_status, api_status, etc.
# IMPORTANT: SECONDS= resets the elapsed timer — use separate vars for timeouts

LABEL="${1:-watcher}"
set -uo pipefail

for i in $(seq 1 60); do
    sleep 20

    # --- do your work here ---
    node_status=$(some_command)  # ← NOT 'status='
    # -------------------------

    safety-rails-beat "$LABEL" "$i" "60" 2>/dev/null || true  # heartbeat: T+5/T+10 Discord alert

    echo "[$(date +%H:%M)] iter=$i $node_status"
    [[ "$node_status" == "DONE" ]] && break
done
echo "watcher $LABEL complete"
```

## §2 Invariants

1. **No `status=` in bash/zsh** — enforced by Fix A (PreToolUse block)
2. **Every polling loop has safety-rails-beat** — enforced by Fix C (warn) + Fix D (template)
3. **No copy-paste heartbeat snippets** — Fix B is a pointer to `safety-rails-beat`
4. **Fix C fires before Fix B** — Fix C detection matches `safety-rails-beat` pattern, which Fix D's template already uses

## §3 Implementation order

1. **Fix D** (template, 20 min) — primary prevention, test fixture for Fix C
2. **Fix A** (PreToolUse hook, 20 min) — register in hooks.json
3. **Fix C** (extend existing hook, 15 min) — test against Fix D template (should be silent)
4. **Fix B** (doc update only: point to safety-rails-beat) — already exists, 5 min
5. Smoke test all 3 hooks against: `scripts/_watcher_template.sh` (no warnings), a watcher with `status=` (Fix A fires), a watcher with while loop and no heartbeat (Fix C fires)

## §4 Open questions (answered by round 1)

1. **SECONDS worth blocking?** — No. Warn in template comment only. Assignable in zsh, side effect is timer reset (confusing but not crash).
2. **Fix A warn-only vs blocking?** — **Block (exit 2)**. Only legitimate reason to assign `status=` is a naming mistake. The incident caused 8h idle billing.
3. **Other reserved vars?** — PPID, LINENO, HISTCMD added. SIGNAL/signals removed (writable). FUNCNAME is bash-only.
4. **Shared heartbeat library?** — **Already exists**: `safety-rails-beat` CLI. Use it.
