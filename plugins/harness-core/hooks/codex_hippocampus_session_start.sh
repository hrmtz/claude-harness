#!/usr/bin/env bash
# Optional Codex adapter for the hippocampus-mcp companion SessionStart hook.
# Native plugin installs cannot embed a machine-specific companion path in
# hooks.json, so resolve it at runtime and silently no-op when it is absent.
set -uo pipefail

# Second instance of the same misread (#177): PLUGIN_ROOT was the "native Codex
# plugin" signal here too, and harness-hook fabricated it on every chassis, so
# this codex-only adapter ran inside Claude sessions instead of no-opping.
# Prefer the explicit chassis; keep PLUGIN_ROOT as the fallback for a plugin
# host that predates it.
case "${HARNESS_CHASSIS:-}" in
    codex) : ;;
    "")    [ -n "${PLUGIN_ROOT:-}" ] || exit 0 ;;
    *)     exit 0 ;;
esac
HOOK_INPUT=$(cat 2>/dev/null || true)
HIPPOCAMPUS_HOME="${HARNESS_HIPPOCAMPUS_HOME:-${HIPPOCAMPUS_HOME:-$HOME/projects/hippocampus-mcp}}"
SCRIPT="$HIPPOCAMPUS_HOME/scripts/hooks/codex_session_start.sh"

[ -f "$SCRIPT" ] || exit 0
printf '%s' "$HOOK_INPUT" | bash "$SCRIPT"
