#!/usr/bin/env bash
# tmux_self_name.sh — SessionStart hook (claude chassis).
# Wraps tmux_self_name_core.sh markdown in hookSpecificOutput JSON envelope.
set -uo pipefail

HOOK_INPUT=$(cat 2>/dev/null || true)
SESSION_ID=$(echo "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)

CORE="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/hooks/tmux_self_name_core.sh"
[ -x "$CORE" ] || exit 0

ctx=$("$CORE" --chassis claude --session-id "$SESSION_ID" 2>/dev/null || true)

if [ -n "$ctx" ]; then
    jq -n --arg ctx "$ctx" '{
      hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: $ctx
      }
    }'
fi
