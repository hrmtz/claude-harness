#!/usr/bin/env bash
# check_early_check_timer.sh — PreToolUse Write/Edit hook
#
# Warns when a polling loop (bash or Python) is written WITHOUT a heartbeat call.
# Missing heartbeat = silent stall looks identical to normal progress.
# Root incident: shard2 watcher looped 8h with no alert (2026-06-07).
#
# Warn-only (exit 0): does NOT block. The goal is a nudge, not a gate.
# Blocking is handled by Fix A (check_zsh_reserved_vars.sh) for the zsh crash class.
#
# Detects:
#   bash/sh: while.*do | until.*do | watch -n [0-9]
#   python:  while True: ... time.sleep(N
#
# Heartbeat markers (any one is sufficient):
#   safety-rails-beat | discord_notify | discord-bot | _early_check_timer
#
# Comment-line bypass fix: strips comment lines BEFORE the heartbeat check
# so a comment like `# uses safety-rails-beat` cannot falsely satisfy the check.
#
# Layer: L2 structural (harness-time hook)
# Companion: templates/_watcher_template.sh (primary prevention — use this instead)

set -uo pipefail

payload=$(head -c 131072 || true)
tool=$(echo "$payload" | jq -r '.tool_use_name // .tool_name // ""' 2>/dev/null || echo "")
file_path=$(echo "$payload" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")

# Scope: Write or Edit only
[[ "$tool" == "Write" || "$tool" == "Edit" ]] || exit 0

# Extract content being written/edited
if [[ "$tool" == "Write" ]]; then
    content=$(echo "$payload" | jq -r '.tool_input.content // ""' 2>/dev/null)
else
    content=$(echo "$payload" | jq -r '.tool_input.new_string // ""' 2>/dev/null)
fi
[[ -n "$content" ]] || exit 0

# ============================================================
# Block A: bash/sh polling loop detection
# ============================================================
if [[ "$file_path" == *.sh || "$file_path" == *.bash ]]; then
    # Strip comment lines before pattern matching.
    # This prevents `# uses safety-rails-beat` from satisfying the heartbeat check.
    clean=$(echo "$content" | grep -v '^[[:space:]]*#')

    # Detect polling loop patterns
    if echo "$clean" | grep -qE 'while[[:space:]]+.+[[:space:]]do|until[[:space:]]+.+[[:space:]]do|watch[[:space:]]+-n[[:space:]]+[0-9]'; then
        # Check for heartbeat (on non-comment lines only)
        if ! echo "$clean" | grep -qE 'safety-rails-beat|discord_notify|discord-bot|_early_check_timer'; then
            echo "⚠️  polling loop in ${file_path:-<new file>} — add safety-rails-beat call inside the loop"
            echo "   Example: safety-rails-beat \"\$LABEL\" \"\$i\" \"\$TOTAL\" 2>/dev/null || true"
            echo "   See: claude-harness/templates/_watcher_template.sh for a complete starting point"
        fi
    fi
fi

# ============================================================
# Block B: Python polling loop detection
# ============================================================
if [[ "$file_path" == *.py ]]; then
    clean=$(echo "$content" | grep -v '^[[:space:]]*#')

    # Detect: while True: combined with time.sleep(N)
    if echo "$clean" | grep -qE 'while[[:space:]]+True[[:space:]]*:' && \
       echo "$clean" | grep -qE 'time\.sleep\([0-9]'; then
        # Check for heartbeat
        if ! echo "$clean" | grep -qE '_early_check_timer|safety-rails-beat'; then
            echo "⚠️  Python polling loop in ${file_path:-<new file>} — add _early_check_timer or safety-rails-beat call inside the loop"
            echo "   Example: from _early_check_timer import start_early_check; start_early_check(label)"
        fi
    fi
fi

exit 0
