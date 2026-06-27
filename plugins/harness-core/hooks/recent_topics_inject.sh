#!/usr/bin/env bash
# recent_topics_inject.sh — SessionStart hook for Phase 6 personal corpus inject.
#
# epic #13 / issue #19 — pairs with ~/.claude/hooks/ghost_inject.sh.
# ghost layer injects cross-project agent memory (= rules / preferences);
# this hook injects current-project recent conversation topics (= what we
# were talking about).
#
# Triple-gated:
#   1. personal.feature_flags.conversation_project_inject = TRUE (= DB)
#   2. current_project IN personal.conversation_inject_allowlist (= DB)
#   3. env HIPPOCAMPUS_PERSONAL_INJECT_DISABLE != "1" (= per-session kill switch)
#
# Always exits 0; empty additionalContext on any failure (= never blocks
# session startup).
set -uo pipefail

export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
export CURRENT_PROJECT_DIR="$PWD"

CREDS_DIR="${CREDS_DIR:-$HOME/projects/creds-migration/secrets-template}"
SECRETS="$CREDS_DIR/hippocampus.enc.yaml"
LOG="$HOME/.local/log/recent_topics_inject.log"
SCRIPT="$HOME/projects/hippocampus-mcp/scripts/recent_topics_inject.py"
PYTHON="$HOME/projects/hippocampus-mcp/.venv/bin/python3"

mkdir -p "$(dirname "$LOG")"

# Capture Claude Code's hook JSON stdin (= session_id + cwd + hook_event_name +
# source + transcript_path) to forward to the python script.
# Fallback to empty if not a hook context (= manual smoke).
HOOK_INPUT=$(cat 2>/dev/null || true)

# 5s budget; empty context on any failure.
ctx=$(echo -n "$HOOK_INPUT" | timeout 5s \
    sops exec-env "$SECRETS" \
    "$PYTHON $SCRIPT" 2>>"$LOG" || true)

if [ -n "$ctx" ]; then
    jq -n --arg ctx "$ctx" '{
      hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: $ctx
      }
    }'
fi
