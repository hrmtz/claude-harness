#!/bin/bash
# install-codex-hooks.sh — wire harness hooks into ~/.codex/config.toml
#
# Idempotent. Re-run after updating hook scripts.
# After running: open Codex, press Tab, Enter to review hooks, then 't' to trust.

set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_CONFIG="$HOME/.codex/config.toml"
HOOK_DIR="$HARNESS_DIR/plugins/harness-core/hooks"

# ---- prerequisites ----------------------------------------------------------
if ! command -v codex >/dev/null 2>&1; then
    echo "error: codex CLI not found. Install it first." >&2
    exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq not found." >&2
    exit 1
fi

mkdir -p "$(dirname "$CODEX_CONFIG")"
touch "$CODEX_CONFIG"

# ---- enable plugin_hooks feature flag ---------------------------------------
codex features enable plugin_hooks 2>/dev/null || true
echo "feature: plugin_hooks enabled"

# ---- remove any existing harness or test hook blocks from config.toml -------
# Strategy: strip all [[hooks.*]] sections and [hooks.state] then re-add clean.
# Use python3 for reliable TOML section manipulation.
python3 - "$CODEX_CONFIG" <<'PYEOF'
import sys, re

path = sys.argv[1]
with open(path) as f:
    content = f.read()

# Remove harness hook blocks (everything between [[hooks.]] and the next
# top-level section that is NOT [hooks.*]) plus [hooks.state.*] entries.
# Split on lines, filter out harness-injected blocks.
lines = content.splitlines(keepends=True)
out = []
skip = False
for line in lines:
    # Start of a hooks section → begin skip
    if re.match(r'^\s*\[\[hooks\.', line) or re.match(r'^\s*\[hooks\.', line):
        skip = True
        continue
    # New top-level non-hooks section → end skip
    if re.match(r'^\s*\[(?!hooks\.)', line) and not re.match(r'^\s*\[\[', line):
        skip = False
    if not skip:
        out.append(line)

# Remove trailing blank lines then write
result = ''.join(out).rstrip('\n') + '\n'
with open(path, 'w') as f:
    f.write(result)
print(f"cleared old hooks sections from {path}")
PYEOF

# ---- append fresh harness hooks block ---------------------------------------
cat >> "$CODEX_CONFIG" << TOML

# harness-core hooks — written by install-codex-hooks.sh
# To trust: open Codex → Tab → Enter on PreToolUse row → t → Esc

[[hooks.PreToolUse]]
matcher = "Bash"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "bash ${HOOK_DIR}/bash_command_guard.sh"
timeout = 5

[[hooks.PostToolUse]]
matcher = "Bash"

[[hooks.PostToolUse.hooks]]
type = "command"
command = "bash ${HOOK_DIR}/credential_value_scrub.sh"
timeout = 10

[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "bash ${HOOK_DIR}/admission_reminder.sh"
timeout = 5
TOML

echo "wrote hooks to $CODEX_CONFIG"

# ---- verify config parses ---------------------------------------------------
if codex features list >/dev/null 2>&1; then
    echo "config OK (codex features list succeeded)"
else
    echo "warning: codex config validation failed — check $CODEX_CONFIG" >&2
fi

# ---- instructions -----------------------------------------------------------
cat <<MSG

Install complete. One-time trust step required:

  1. Run: codex <any prompt>
  2. Press Tab to open the Hooks panel
  3. Press Enter on each event row that shows "Review" > 0
  4. Press 't' to trust the hook
  5. Press Esc to close

Hooks are then active for all future Codex sessions.
Hook scripts: ${HOOK_DIR}/
MSG
