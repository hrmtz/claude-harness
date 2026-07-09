#!/usr/bin/env bash
# ghost_inject.sh — SessionStart hook for hippocampus companion context.
#
# claude-harness and hippocampus-mcp are designed to work well together, but a
# standalone claude-harness install must not wire a dead local path. This hook
# auto-detects the companion at $HOME/projects/hippocampus-mcp or accepts
# HARNESS_HIPPOCAMPUS_HOME. Set HARNESS_HIPPOCAMPUS_SECRETS if credentials live
# outside the companion repo. Missing companion/secrets => silent no-op.
#
# Outputs JSON with hookSpecificOutput.additionalContext for SessionStart hook.
# Always exits 0; empty additionalContext on any failure (= fail-closed).
set -uo pipefail

HOOK_INPUT=$(cat 2>/dev/null || true)
CWD=$(printf '%s' "$HOOK_INPUT" | jq -r '.cwd // .workspace.current_dir // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
export CURRENT_PROJECT_DIR="$CWD"

HIPPOCAMPUS_HOME="${HARNESS_HIPPOCAMPUS_HOME:-$HOME/projects/hippocampus-mcp}"
CREDS_DIR="${HARNESS_HIPPOCAMPUS_CREDS_DIR:-${CREDS_DIR:-$HIPPOCAMPUS_HOME/secrets}}"
SECRETS="${HARNESS_HIPPOCAMPUS_SECRETS:-$CREDS_DIR/hippocampus.enc.yaml}"
LOG="$HOME/.local/log/ghost_inject.log"
SCRIPT="${HARNESS_GHOST_INJECT_SCRIPT:-$HIPPOCAMPUS_HOME/scripts/ghost_context_inject.py}"
PYTHON="${HARNESS_HIPPOCAMPUS_PYTHON:-$HIPPOCAMPUS_HOME/.venv/bin/python3}"

[ "${HARNESS_HIPPOCAMPUS_INJECT_DISABLE:-0}" = "1" ] && exit 0
[ -x "$PYTHON" ] || exit 0
[ -f "$SCRIPT" ] || exit 0
[ -f "$SECRETS" ] || exit 0
[ -f "$SOPS_AGE_KEY_FILE" ] || exit 0

mkdir -p "$(dirname "$LOG")"

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
