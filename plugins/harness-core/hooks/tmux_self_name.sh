#!/usr/bin/env bash
# tmux_self_name.sh — SessionStart hook (claude chassis).
# Wraps tmux_self_name_core.sh markdown in hookSpecificOutput JSON envelope.
set -uo pipefail

HOOK_INPUT=$(cat 2>/dev/null || true)
SESSION_ID=$(echo "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)

# hooks.json is shared with the native Codex plugin, so this one entry has to
# reach two different identity adapters. Which one is a fact about the host, and
# a host that knows says so: HARNESS_CHASSIS is set by the codex-side wiring.
#
# It used to be inferred from PLUGIN_ROOT being set. That is a Codex-native
# variable, but harness-hook — the dispatcher Claude's own settings.json routes
# through — exported it unconditionally, so *every* Claude session took this
# branch, ran the codex adapter, and was refused by ownership for having a
# claude process in its ancestry. Claude panes went unnamed and, where no claude
# ancestor existed, were labelled `codex-<codename>` instead (#177).
#
# PLUGIN_ROOT remains an accepted signal for a genuine plugin host that has not
# been taught the explicit one; harness-hook no longer fabricates it.
HARNESS_CHASSIS="${HARNESS_CHASSIS:-}"
CODEX_ADAPTER="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}/hooks/codex_tmux_self_name.sh"
if { [ "$HARNESS_CHASSIS" = "codex" ] || { [ -z "$HARNESS_CHASSIS" ] && [ -n "${PLUGIN_ROOT:-}" ]; }; } \
   && [ -x "$CODEX_ADAPTER" ]; then
    printf '%s' "$HOOK_INPUT" | bash "$CODEX_ADAPTER"
    exit 0
fi

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
