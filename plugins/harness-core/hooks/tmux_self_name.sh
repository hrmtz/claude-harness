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
#
# Grok's Claude-compat loader is the exceptional shared-config path: it executes
# entries from ~/.claude/settings.json without the Grok installer's stamp, but
# the Grok runner injects GROK_HOOK_EVENT/GROK_SESSION_ID even for compat hooks.
# Treat that runtime-owned signal as grok only when no explicit stamp exists.
# This makes the compat hook reach the grok adapter regardless of whether it
# runs before or after Grok's native SessionStart hook (#183).
HARNESS_CHASSIS="${HARNESS_CHASSIS:-}"
if [ -z "$HARNESS_CHASSIS" ] \
   && { [ -n "${GROK_HOOK_EVENT:-}" ] || [ -n "${GROK_SESSION_ID:-}" ]; }; then
    HARNESS_CHASSIS=grok
fi
# Config-layer hooks get no plugin-root injection, so on the direct fallback
# path (harness-hook absent or failing its id check) both root variables are
# empty and the adapter path degraded to "/hooks/codex_tmux_self_name.sh" — an
# explicit codex chassis then produced no codex identity at all. This file sits
# beside the adapter in every layout, so resolve from here when no host said.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}"
if [ -n "$ADAPTER_ROOT" ] && [ -x "$ADAPTER_ROOT/hooks/codex_tmux_self_name.sh" ]; then
    CODEX_ADAPTER="$ADAPTER_ROOT/hooks/codex_tmux_self_name.sh"
else
    CODEX_ADAPTER="$HERE/codex_tmux_self_name.sh"
fi
GROK_ADAPTER="$HERE/grok_tmux_self_name.sh"
if { [ "$HARNESS_CHASSIS" = "codex" ] || { [ -z "$HARNESS_CHASSIS" ] && [ -n "${PLUGIN_ROOT:-}" ]; }; } \
   && [ -x "$CODEX_ADAPTER" ]; then
    printf '%s' "$HOOK_INPUT" | bash "$CODEX_ADAPTER"
    exit 0
fi
if [ "$HARNESS_CHASSIS" = "grok" ] && [ -x "$GROK_ADAPTER" ]; then
    printf '%s' "$HOOK_INPUT" | bash "$GROK_ADAPTER"
    exit 0
fi

# A declared but unsupported chassis is not evidence for Claude. Refuse to
# fabricate a claude identity and leave its own adapter/launcher to claim it.
case "$HARNESS_CHASSIS" in
    ""|claude) ;;
    *) exit 0 ;;
esac

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
