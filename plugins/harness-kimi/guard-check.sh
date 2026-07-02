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

log_guard() {
    local msg="$1"
    local logdir="$HOME/.kimi-code/harness-guard"
    mkdir -p "$logdir"
    echo "[$(date +%F_%T)] $msg" >> "$logdir/guarded-bash.log"
}

# Builtin hook set — the source of truth if the overlay is missing OR
# unparseable. Order matters: insurance first, then gates, then hints.
CORE_HOOKS="${HARNESS_CORE_HOOKS:-$PLUGINS_DIR/harness-core/hooks}"
RAILS_HOOKS="${HARNESS_RAILS_HOOKS:-$PLUGINS_DIR/harness-rails/hooks}"
builtin_hooks() {
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
}

# Hook set comes from the cross-CLI overlay (gh #55) so Claude/Codex/Kimi stay
# in sync. A malformed overlay must NOT silently empty the gate list (that would
# fail open) — parse into a variable, check jq's exit AND that gates is
# non-empty, and fall back to the builtin list with a loud warning otherwise.
if [[ -f "$OVERLAY" ]]; then
    _ins=$(jq -r '.kimi.insurance[]?' "$OVERLAY" 2>/dev/null); _ins_rc=$?
    _gts=$(jq -r '.kimi.gates[]?'     "$OVERLAY" 2>/dev/null); _gts_rc=$?
    _hnt=$(jq -r '.kimi.hints[]?'     "$OVERLAY" 2>/dev/null); _hnt_rc=$?
    if [[ $_ins_rc -ne 0 || $_gts_rc -ne 0 || $_hnt_rc -ne 0 || -z "$_gts" ]]; then
        echo "⚠️  harness-kimi guard: $OVERLAY unparseable or has no kimi.gates — using builtin hook set." >&2
        log_guard "overlay parse failed (rc ins=$_ins_rc gts=$_gts_rc hnt=$_hnt_rc, gates_empty=$([[ -z "$_gts" ]] && echo 1 || echo 0)) — builtin fallback"
        builtin_hooks
    else
        mapfile -t INSURANCE_HOOKS < <(printf '%s\n' "$_ins" | sed "/^$/d;s|^|$PLUGINS_DIR/|")
        mapfile -t GATE_HOOKS      < <(printf '%s\n' "$_gts" | sed "/^$/d;s|^|$PLUGINS_DIR/|")
        mapfile -t HINT_HOOKS      < <(printf '%s\n' "$_hnt" | sed "/^$/d;s|^|$PLUGINS_DIR/|")
    fi
    unset _ins _gts _hnt _ins_rc _gts_rc _hnt_rc
else
    builtin_hooks
fi

# HOOK_STDERR is set by run_hook to the hook's stderr (kept separate so that
# diagnostic/warning lines never corrupt the JSON on stdout — code-review #52).
HOOK_STDERR=""
run_hook() {
    local hook="$1"
    local stdin="$2"
    if [[ ! -x "$hook" && ! -f "$hook" ]]; then
        HOOK_STDERR=""
        return 0
    fi
    local errfile rc
    errfile=$(mktemp)
    # Run hook with guard-active so any bash subprocess spawned by the hook
    # is not re-intercepted (BASH_ENV or PATH shim) and uses the real bash.
    # stdout (the permissionDecision JSON) is captured by the caller via $();
    # stderr goes to errfile so is_deny_json parses clean JSON.
    printf '%s' "$stdin" | HARNESS_KIMI_GUARD_ACTIVE=1 "$REAL_BASH" "$hook" 2>"$errfile"
    rc=$?
    HOOK_STDERR=$(cat "$errfile" 2>/dev/null)
    rm -f "$errfile"
    return $rc
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
    # A configured gate that is MISSING must be loud, not silently skipped —
    # otherwise a moved repo / absent plugin turns the guard into a no-op that
    # still logs "allowed" (code-review #52 finding). Warn and continue (rather
    # than fail-closed) to keep the "rail, not sandbox" contract.
    if [[ ! -f "$hook" ]]; then
        echo "⚠️  harness-kimi guard: gate hook missing, NOT enforced: $hook" >&2
        log_guard "gate MISSING (not enforced): $hook"
        continue
    fi

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
        [[ -n "$HOOK_STDERR" ]] && echo "$HOOK_STDERR" >&2
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
