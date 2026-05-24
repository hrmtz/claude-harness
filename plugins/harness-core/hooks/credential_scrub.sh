#!/bin/bash
# Unified credential scrubber (= revision after dual-magi round 1)
# Single PostToolUse hook replacing the L2/L3 split. Sources lib.sh for
# canonical hook semantics (transcript_path, parse_tool_output, hook_log).
#
# Operational kill switch: touch ~/.claude/hooks/credential_scrub.disabled
# Deps preflight: python3 must be on PATH; yaml + (optional) blake3 modules.
# Fail-safe: any unexpected error → exit 0 with hook_log entry; never blocks.

# NOTE: no `set -e` — we want fail-safe semantics, not error propagation.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ----------------------------------------------------------------------------
# Operational kill switch
# ----------------------------------------------------------------------------
if [ -f "$HOME/.claude/hooks/credential_scrub.disabled" ]; then
    exit 0
fi

# ----------------------------------------------------------------------------
# Dependency preflight (= fail-safe if missing)
# ----------------------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || exit 0

# ----------------------------------------------------------------------------
# Source lib.sh for parse_tool_output / active_jsonl / hook_log / emit_context
# ----------------------------------------------------------------------------
if [ -f "$HOME/.claude/hooks/lib.sh" ]; then
    # shellcheck source=/dev/null
    source "$HOME/.claude/hooks/lib.sh"
else
    exit 0  # fail-safe: lib.sh absent → can't operate
fi

# Read stdin once into HOOK_INPUT (lib.sh helpers prefer this over re-reading stdin)
HOOK_INPUT=$(cat)
export HOOK_INPUT

# Resolve canonical inputs via lib.sh
TRANSCRIPT_PATH=$(active_jsonl)
TOOL_OUTPUT=$(parse_tool_output)

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    hook_log "credential_scrub" "no transcript_path resolved; skip"
    exit 0
fi

# H8 (round 2): lib.sh's parse_tool_output emits .tool_response.content as raw
# JSON when content is an array (= multi-part assistant message). Pass the raw
# HOOK_INPUT to Python so it can do its own deep-text extraction (recursive
# .content[].text walk) without modifying lib.sh which is shared across hooks.
export CREDENTIAL_SCRUB_TOOL_OUTPUT="$TOOL_OUTPUT"
export CREDENTIAL_SCRUB_TRANSCRIPT="$TRANSCRIPT_PATH"
export CREDENTIAL_SCRUB_RAW_INPUT="$HOOK_INPUT"

exec python3 "$HERE/credential_scrub.py"
