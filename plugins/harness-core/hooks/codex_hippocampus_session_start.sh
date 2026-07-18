#!/usr/bin/env bash
# Optional Codex adapter for the hippocampus-mcp companion SessionStart hook.
# Native plugin installs cannot embed a machine-specific companion path in
# hooks.json, so resolve it at runtime and silently no-op when it is absent.
set -uo pipefail

[ -n "${PLUGIN_ROOT:-}" ] || exit 0
HOOK_INPUT=$(cat 2>/dev/null || true)
HIPPOCAMPUS_HOME="${HARNESS_HIPPOCAMPUS_HOME:-${HIPPOCAMPUS_HOME:-$HOME/projects/hippocampus-mcp}}"
SCRIPT="$HIPPOCAMPUS_HOME/scripts/hooks/codex_session_start.sh"

[ -f "$SCRIPT" ] || exit 0
printf '%s' "$HOOK_INPUT" | bash "$SCRIPT"
