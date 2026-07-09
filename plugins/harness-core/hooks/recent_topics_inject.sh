#!/usr/bin/env bash
# recent_topics_inject.sh — SessionStart hook for hippocampus recent-topic inject.
#
# Pairs with ghost_inject.sh. The hippocampus-mcp companion is optional:
# auto-detect at $HOME/projects/hippocampus-mcp, override with
# HARNESS_HIPPOCAMPUS_HOME, and silently no-op when missing. Set
# HARNESS_HIPPOCAMPUS_SECRETS if credentials live outside the companion repo.
#
# Triple-gated:
#   1. personal.feature_flags.conversation_project_inject = TRUE (= DB)
#   2. current_project IN personal.conversation_inject_allowlist (= DB)
#   3. env HIPPOCAMPUS_PERSONAL_INJECT_DISABLE != "1" (= per-session kill switch)
#
# Always exits 0; empty additionalContext on any failure (= never blocks
# session startup).
set -uo pipefail

HOOK_INPUT=$(cat 2>/dev/null || true)
CWD=$(printf '%s' "$HOOK_INPUT" | jq -r '.cwd // .workspace.current_dir // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
export CURRENT_PROJECT_DIR="$CWD"

HIPPOCAMPUS_HOME="${HARNESS_HIPPOCAMPUS_HOME:-$HOME/projects/hippocampus-mcp}"
CREDS_DIR="${HARNESS_HIPPOCAMPUS_CREDS_DIR:-${CREDS_DIR:-$HIPPOCAMPUS_HOME/secrets}}"
SECRETS="${HARNESS_HIPPOCAMPUS_SECRETS:-$CREDS_DIR/hippocampus.enc.yaml}"
LOG="$HOME/.local/log/recent_topics_inject.log"
SCRIPT="${HARNESS_RECENT_TOPICS_SCRIPT:-$HIPPOCAMPUS_HOME/scripts/recent_topics_inject.py}"
PYTHON="${HARNESS_HIPPOCAMPUS_PYTHON:-$HIPPOCAMPUS_HOME/.venv/bin/python3}"

[ "${HARNESS_HIPPOCAMPUS_INJECT_DISABLE:-0}" = "1" ] && exit 0
[ "${HIPPOCAMPUS_PERSONAL_INJECT_DISABLE:-0}" = "1" ] && exit 0
[ -x "$PYTHON" ] || exit 0
[ -f "$SCRIPT" ] || exit 0
[ -f "$SECRETS" ] || exit 0
[ -f "$SOPS_AGE_KEY_FILE" ] || exit 0

mkdir -p "$(dirname "$LOG")"

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
