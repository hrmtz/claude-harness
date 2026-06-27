#!/usr/bin/env bash
# ghost_inject.sh — SessionStart hook for cross-project ghost layer
#
# Phase 2.2 of docs/GHOST_LAYER_DESIGN.md v4 in ~/projects/hippocampus-mcp/
#
# Outputs JSON with hookSpecificOutput.additionalContext for SessionStart hook.
# Always exits 0; empty additionalContext on any failure (= fail-closed).
set -uo pipefail

export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
export CURRENT_PROJECT_DIR="$PWD"

CREDS_DIR="${CREDS_DIR:-$HOME/projects/creds-migration/secrets-template}"
SECRETS="$CREDS_DIR/hippocampus.enc.yaml"
LOG="$HOME/.local/log/ghost_inject.log"
SCRIPT="$HOME/projects/hippocampus-mcp/scripts/ghost_context_inject.py"
PYTHON="$HOME/projects/hippocampus-mcp/.venv/bin/python3"

mkdir -p "$(dirname "$LOG")"

# Forward Claude Code hook JSON stdin to python (= session_id + cwd + chassis
# detection via transcript_path). Empty on smoke / manual run.
HOOK_INPUT=$(cat 2>/dev/null || true)

# 5s budget; empty context on any failure
ctx=$(echo -n "$HOOK_INPUT" | timeout 5s \
    sops exec-env "$SECRETS" \
    "$PYTHON $SCRIPT" 2>>"$LOG" || true)

# only emit JSON when we have content (= avoid noise for empty inject)
if [ -n "$ctx" ]; then
    jq -n --arg ctx "$ctx" '{
      hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: $ctx
      }
    }'
fi
