#!/bin/bash
# Shared utilities for ~/.claude/hooks/*
# Adapted from SNet-CTF/SNet-Claude/.claude/hooks/lib.sh (2026-04-27)
# Used by global Claude Code hooks for credential leak prevention etc.

CLAUDE_HOME="$HOME/.claude"
STATE_DIR="$CLAUDE_HOME/state"
LOG_DIR="$CLAUDE_HOME/state/hook_logs"
mkdir -p "$STATE_DIR" "$LOG_DIR"

# ----------------------------------------
# Active session jsonl path resolver
# ----------------------------------------
# Find the active session jsonl for the CURRENT project (most recently modified).
# Returns empty string if not found.
active_jsonl() {
    # Project dirs are under ~/.claude/projects/, named by hashed CWD.
    # We can't reliably know "current" project from a hook context,
    # so we pick the most recently modified jsonl across all projects.
    ls -t "$CLAUDE_HOME"/projects/*/[a-z0-9-]*.jsonl 2>/dev/null | head -1
}

# ----------------------------------------
# Parse user prompt from hook stdin (UserPromptSubmit, SessionStart, etc.)
# ----------------------------------------
# Hook stdin is JSON like {"prompt": "...", ...}.
parse_prompt() {
    local input
    input=$(cat)
    echo "$input" | jq -r '.prompt // .content // .message // empty' 2>/dev/null \
        | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

# Parse tool_response.stdout/stderr/output from PostToolUse hook stdin.
parse_tool_output() {
    local input
    input=$(cat)
    # PostToolUse hook input: {"tool_name": "Bash", "tool_response": {"stdout": "...", "stderr": "..."}}
    echo "$input" | jq -r '
        .tool_response.stdout // empty,
        .tool_response.stderr // empty,
        .tool_response.output // empty,
        .tool_response.content // empty
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
# Get the last N assistant message bodies from active jsonl.
# Useful for keyword admission detection.
recent_assistant_turns() {
    local n="${1:-3}"
    local jsonl
    jsonl=$(active_jsonl)
    [ -z "$jsonl" ] && return 1
    [ ! -f "$jsonl" ] && return 1
    # Each line is a JSON event. Look for type=assistant, get message.content (or text).
    tac "$jsonl" 2>/dev/null \
        | jq -r 'select(.type == "assistant") | .message.content[]?.text // empty' 2>/dev/null \
        | head -n "$n"
}
