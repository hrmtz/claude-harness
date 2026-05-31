#!/bin/bash
# Shared utilities for harness hooks (Claude Code + Codex).
#
# Codex compat: set HOOK_INPUT="$(cat)" at the top of each hook script before
# calling any lib function. lib functions prefer HOOK_INPUT over stdin so that
# stdin is not consumed twice across multiple calls (e.g. parse_tool_output +
# active_jsonl in the same script). Hooks that don't set HOOK_INPUT fall back
# to reading stdin directly (backward-compatible with older Claude Code style).

CLAUDE_HOME="$HOME/.claude"
STATE_DIR="$CLAUDE_HOME/state"
LOG_DIR="$CLAUDE_HOME/state/hook_logs"
mkdir -p "$STATE_DIR" "$LOG_DIR"

# ----------------------------------------
# Active session jsonl path resolver
# ----------------------------------------
# Prefers transcript_path from hook JSON context (works for both Claude Code and
# Codex). Falls back to scanning ~/.claude/projects/ for Claude Code sessions
# that don't set HOOK_INPUT.
active_jsonl() {
    if [ -n "${HOOK_INPUT:-}" ]; then
        local tp
        tp=$(printf '%s' "$HOOK_INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
        if [ -n "$tp" ] && [ -f "$tp" ]; then
            echo "$tp"
            return 0
        fi
    fi
    ls -t "$CLAUDE_HOME"/projects/*/[a-z0-9-]*.jsonl 2>/dev/null | head -1
}

# ----------------------------------------
# Parse user prompt from hook stdin (UserPromptSubmit, SessionStart, etc.)
# ----------------------------------------
parse_prompt() {
    local input
    if [ -n "${HOOK_INPUT:-}" ]; then
        input="$HOOK_INPUT"
    else
        input=$(cat)
    fi
    printf '%s' "$input" | jq -r '.prompt // .input // .content // .message // empty' 2>/dev/null \
        | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

# Parse tool output from PostToolUse hook stdin.
#
# Shape-agnostic (issue #7): the named-field extraction below handles the
# success-shaped tool_response (clean per-field text, real newlines — best for
# the keyword/YAML scanners). But exit-non-zero Bash calls deliver the output in
# an *error-wrapped* tool_response whose payload lives under a different field
# (or carries it as a bare string), so named-field extraction returns empty and
# the whole scan silently skips. The trailing `tostring` fallback serializes the
# ENTIRE tool_response regardless of shape, so any leaked credential text is
# still surfaced to the scanner. Redundant with the named fields on success
# (harmless — grep just matches twice); load-bearing on failure.
parse_tool_output() {
    local input
    if [ -n "${HOOK_INPUT:-}" ]; then
        input="$HOOK_INPUT"
    else
        input=$(cat)
    fi
    # The `?` after each index suppresses jq's "cannot index string with X" error
    # when tool_response is a bare string (an error-wrapped shape) — without it the
    # whole filter aborts before the tostring fallback runs.
    printf '%s' "$input" | jq -r '
        (.tool_response.stdout? // empty),
        (.tool_response.stderr? // empty),
        (.tool_response.output? // empty),
        (.tool_response.content? // empty),
        (.tool_response | select(. != null) | tostring)
    ' 2>/dev/null
}

# ----------------------------------------
# Emit hookSpecificOutput JSON (for context injection or blocking)
# ----------------------------------------
emit_context() {
    local event="$1" content="$2"
    jq -n --arg ctx "$content" --arg ev "$event" '{
        "hookSpecificOutput": {
            "hookEventName": $ev,
            "additionalContext": $ctx
        }
    }'
}

# ----------------------------------------
# Log hook events for debug + audit
# ----------------------------------------
hook_log() {
    local hook_name="$1"
    shift
    local msg="$*"
    echo "[$(date +%F_%T)] [$hook_name] $msg" >> "$LOG_DIR/hooks.log"
}

# ----------------------------------------
# Recent assistant turns from active jsonl
# ----------------------------------------
recent_assistant_turns() {
    local n="${1:-3}"
    local jsonl
    jsonl=$(active_jsonl)
    [ -z "$jsonl" ] && return 1
    [ ! -f "$jsonl" ] && return 1
    tac "$jsonl" 2>/dev/null \
        | jq -r 'select(.type == "assistant") | .message.content[]?.text // empty' 2>/dev/null \
        | head -n "$n"
}
