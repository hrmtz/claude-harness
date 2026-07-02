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

CORE_HOOKS="${HARNESS_CORE_HOOKS:-$HOME/projects/claude-harness/plugins/harness-core/hooks}"
RAILS_HOOKS="${HARNESS_RAILS_HOOKS:-$HOME/projects/claude-harness/plugins/harness-rails/hooks}"

# Order matters: insurance first, then gates, then hints.
GUARD_HOOKS=(
    "$CORE_HOOKS/sanada_autobackup.sh"
    "$CORE_HOOKS/bash_command_guard.sh"
    "$CORE_HOOKS/branch_policy_guard.sh"
    "$CORE_HOOKS/pg_rotation_propagation_guard.sh"
    "$RAILS_HOOKS/pipeline_preflight_gate.sh"
    "$RAILS_HOOKS/phase_review_gate.sh"
)

HINT_HOOKS=(
    "$CORE_HOOKS/long_task_advisor.sh"
)

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
for hook in "${GUARD_HOOKS[@]}"; do
    case "$hook" in
        */sanada_autobackup.sh)
            run_hook "$hook" "$PAYLOAD" >/dev/null 2>&1 || true
            ;;
    esac
done

# ── gates ──
for hook in "${GUARD_HOOKS[@]}"; do
    [[ "$hook" == */sanada_autobackup.sh ]] && continue
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
