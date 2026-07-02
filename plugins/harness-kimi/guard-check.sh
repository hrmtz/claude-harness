#!/bin/bash
# guard-check.sh — harness-kimi guard core: run harness-core / harness-rails
# PreToolUse hooks against a single Bash command string.
#
# Shared by two interception layers (issue #52):
#   - guard-env.sh    BASH_ENV interception — catches absolute-path /bin/bash -c
#   - guarded-bash.sh PATH shim fallback — catches PATH-resolved `bash`
#
# Input:
#   HARNESS_KIMI_GUARD_CMD (env) — the command string to check
#   PWD                          — cwd recorded in the hook payload
# Output:
#   deny reasons / hints on stderr (stdout stays clean for the caller)
# Exit:
#   0 allow, 2 deny (or the hook's non-zero rc)
set -uo pipefail

CMD="${HARNESS_KIMI_GUARD_CMD:-}"
if [[ -z "$CMD" ]]; then
    exit 0
fi

REAL_BASH="${HARNESS_KIMI_REAL_BASH:-/bin/bash}"

# Build the PreToolUse-style JSON that harness-core hooks expect.
PAYLOAD=$(jq -n \
    --arg cmd "$CMD" \
    --arg cwd "$PWD" \
    '{tool_name:"Bash", tool_input:{command:$cmd}, cwd:$cwd}')

PLUGINS_DIR="${HARNESS_PLUGINS:-$HOME/projects/claude-harness/plugins}"
OVERLAY="$PLUGINS_DIR/cross_cli_hooks.json"

# Hook set comes from the cross-CLI overlay (gh #55) so Claude/Codex/Kimi
# stay in sync. Order matters: insurance first, then gates, then hints.
# Fallback to a builtin list if the overlay is missing (e.g. repo moved).
if [[ -f "$OVERLAY" ]]; then
    mapfile -t INSURANCE_HOOKS < <(jq -r '.kimi.insurance[]' "$OVERLAY" | sed "s|^|$PLUGINS_DIR/|")
    mapfile -t GATE_HOOKS      < <(jq -r '.kimi.gates[]'     "$OVERLAY" | sed "s|^|$PLUGINS_DIR/|")
    mapfile -t HINT_HOOKS      < <(jq -r '.kimi.hints[]'     "$OVERLAY" | sed "s|^|$PLUGINS_DIR/|")
else
    CORE_HOOKS="${HARNESS_CORE_HOOKS:-$PLUGINS_DIR/harness-core/hooks}"
    RAILS_HOOKS="${HARNESS_RAILS_HOOKS:-$PLUGINS_DIR/harness-rails/hooks}"
    INSURANCE_HOOKS=(
        "$CORE_HOOKS/sanada_autobackup.sh"
    )
    GATE_HOOKS=(
        "$CORE_HOOKS/bash_command_guard.sh"
        "$CORE_HOOKS/branch_policy_guard.sh"
        "$CORE_HOOKS/pg_rotation_propagation_guard.sh"
        "$RAILS_HOOKS/pipeline_preflight_gate.sh"
        "$RAILS_HOOKS/phase_review_gate.sh"
    )
    HINT_HOOKS=(
        "$CORE_HOOKS/long_task_advisor.sh"
    )
fi

log_guard() {
    local msg="$1"
    local logdir="$HOME/.kimi-code/harness-guard"
    mkdir -p "$logdir"
    echo "[$(date +%F_%T)] $msg" >> "$logdir/guarded-bash.log"
}

run_hook() {
    local hook="$1"
    local stdin="$2"
    if [[ ! -x "$hook" && ! -f "$hook" ]]; then
        return 0
    fi
    # Run hook with guard-active so any bash subprocess spawned by the hook
    # is not re-intercepted (BASH_ENV or PATH shim) and uses the real bash.
    printf '%s' "$stdin" | HARNESS_KIMI_GUARD_ACTIVE=1 "$REAL_BASH" "$hook" 2>&1
}

is_deny_json() {
    local text="$1"
    printf '%s' "$text" | jq -e '.hookSpecificOutput.permissionDecision == "deny"' >/dev/null 2>&1
}

extract_deny_reason() {
    local text="$1"
    printf '%s' "$text" | jq -r '.hookSpecificOutput.permissionDecisionReason // "blocked by harness guard"'
}

extract_hint() {
    local text="$1"
    printf '%s' "$text" | jq -r '.hookSpecificOutput.additionalContext // empty' 2>/dev/null
}

# ── insurance (never blocks) ──
for hook in "${INSURANCE_HOOKS[@]}"; do
    [[ ! -f "$hook" ]] && continue
    run_hook "$hook" "$PAYLOAD" >/dev/null 2>&1 || true
done

# ── gates ──
for hook in "${GATE_HOOKS[@]}"; do
    [[ ! -f "$hook" ]] && continue

    out=$(run_hook "$hook" "$PAYLOAD")
    rc=$?

    if is_deny_json "$out"; then
        reason=$(extract_deny_reason "$out")
        log_guard "$(basename "$hook"): deny -- $reason"
        echo "🚫 harness guard ($(basename "$hook")): $reason" >&2
        exit 2
    fi

    if [[ $rc -ne 0 ]]; then
        # Hooks like pipeline_preflight_gate print to stderr and exit 2.
        log_guard "$(basename "$hook"): non-zero exit $rc"
        echo "$out" >&2
        echo "🚫 harness guard ($(basename "$hook")) blocked this command (rc=$rc)." >&2
        exit "$rc"
    fi
done

# ── hints (never blocks) ──
for hook in "${HINT_HOOKS[@]}"; do
    [[ ! -f "$hook" ]] && continue
    out=$(run_hook "$hook" "$PAYLOAD")
    hint=$(extract_hint "$out")
    if [[ -n "$hint" ]]; then
        echo "⚠️  harness hint ($(basename "$hook")): $hint" >&2
    fi
done

log_guard "allowed: ${CMD:0:120}"
exit 0
