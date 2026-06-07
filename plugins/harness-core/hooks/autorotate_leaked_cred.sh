#!/bin/bash
# autorotate_leaked_cred.sh — step 3 of the credential-leak chain: CLOSE THE LOOP.
#
# Chain: scrubber detects+sanitizes (step 1) → followup files incident (step 2) →
# THIS script rotates the leaked credential (step 3). Until now step 3 was manual
# ("rotation tracked in the issue"); this makes the safe case autonomous.
#
# Policy (deliberately conservative — a leak-DETECTION false-positive must never
# take down production):
#   - NON-PROD PG role  → fully autonomous rotate (no redeploy needed, zero outage).
#   - PROD PG role / bare prs / API key → DO NOT auto-rotate. Rotating these needs a
#     rolling redeploy (PG) or provider-side action (API key); a hook must not
#     restart prod or call a provider unattended. Escalate HARD instead so a human/
#     agent does it with the Phase 2 runbook.
#
# Invoked DETACHED (setsid </dev/null) by credential_value_scrub.sh so rotation
# latency (~20s) never blocks the PostToolUse hook.
#
# Inputs (env) — NEVER a credential value, by construction:
#   LEAK_ROLE        PG role name parsed from a leaked DSN (a username, not a secret), or empty
#   LEAK_CLASS       pg_dsn | api_key | unknown
#   LEAK_SESSION_ID  session id (dedup + audit)
#
# Fail-safe: every error path logs + exit 0. Never disrupt the session.
set -u

LOG_DIR="$HOME/.claude/state/hook_logs"
STATE_DIR="$HOME/.claude/state/credential_scrub"
ROT_MARK_DIR="$STATE_DIR/rotated"
mkdir -p "$LOG_DIR" "$ROT_MARK_DIR" 2>/dev/null

_log() { echo "[$(date +%F_%T)] [autorotate] $*" >> "$LOG_DIR/hooks.log" 2>/dev/null; }

LEAK_ROLE="${LEAK_ROLE:-}"
LEAK_CLASS="${LEAK_CLASS:-unknown}"
LEAK_SESSION_ID="${LEAK_SESSION_ID:-unknown}"
DRYRUN="${AUTOROTATE_DRYRUN:-0}"   # 1 = decide + log only, no real rotation (for tests)

# Project-specific rotation backend (PRS-LLM / mars). Absent on other projects → PG
# rotation simply unavailable here (escalate instead).
ROT_SCRIPT="$HOME/projects/PRS-LLM-dev/scripts/_rotate_mars_pg_roles.sh"

NON_PROD_ROLES=" prs_owner prs_migration prs_ingest prs_bench prs_zombie_canceler "
PROD_ROLES=" prs_prod_pro prs_prod_chat prs_prod_search prs_auth prs "

notify() {  # best-effort Discord, never fail the script
    command -v discord-bot >/dev/null 2>&1 && timeout 15 discord-bot post PRS-LLM "$1" >/dev/null 2>&1
    return 0
}

escalate() {  # $1 = reason; mark the incident issue + Discord, no rotation
    local reason="$1"
    _log "ESCALATE (no auto-rotate): $reason"
    notify "🔐 **credential leak — manual rotation required**
$reason
session=\`$LEAK_SESSION_ID\`. Runbook: \`scripts/_rotate_mars_pg_roles.sh\` (#263 Phase 2 for prod roles)."
    # append to the rolling incident issue if followup recorded one
    local last; last=$(cat "$STATE_DIR/last_issue" 2>/dev/null)
    if [ -n "$last" ] && command -v gh >/dev/null 2>&1; then
        timeout 20 gh issue comment -R hrmtz/claude-harness "$last" \
            --body "⚠️ **auto-rotate DECLINED — manual action needed.** $reason (session \`$LEAK_SESSION_ID\`). Non-prod roles auto-rotate; this class doesn't (needs rolling redeploy / provider-side rotation)." >/dev/null 2>&1
    fi
}

# ── only the project host (chichibu: has sops age key + ssh mars) can rotate ──
if [ ! -f "$ROT_SCRIPT" ]; then
    escalate "PG rotation backend not present on this host ($(hostname -s 2>/dev/null)); class=$LEAK_CLASS role=${LEAK_ROLE:-?}"
    exit 0
fi

# ── non-PG classes: not auto-rotatable here ──────────────────────────────────
if [ "$LEAK_CLASS" != "pg_dsn" ] || [ -z "$LEAK_ROLE" ]; then
    escalate "leak class=\`$LEAK_CLASS\` is not an auto-rotatable PG role (API key / unknown → provider-side rotation)"
    exit 0
fi

# ── dedup: one rotation per role per session ─────────────────────────────────
MARK="$ROT_MARK_DIR/${LEAK_SESSION_ID}_${LEAK_ROLE}"
if [ -f "$MARK" ]; then
    _log "dedup hit: $LEAK_ROLE already rotated this session; skipping"
    exit 0
fi

# ── classify role ────────────────────────────────────────────────────────────
case "$PROD_ROLES" in
    *" $LEAK_ROLE "*)
        escalate "PROD-serving role \`$LEAK_ROLE\` leaked — rotation needs ALTER + CF LB drain rolling restart (talisker→laddie). Not auto-rotating to avoid self-inflicted outage."
        touch "$MARK" 2>/dev/null
        exit 0 ;;
esac
case "$NON_PROD_ROLES" in
    *" $LEAK_ROLE "*) : ;;   # eligible for autonomous rotation
    *)
        escalate "role \`$LEAK_ROLE\` is not in the known non-prod rotation set; rotate manually after confirming it's safe"
        exit 0 ;;
esac

# ── autonomous rotation (non-prod, zero-outage) ──────────────────────────────
_log "auto-rotating non-prod role: $LEAK_ROLE (session=$LEAK_SESSION_ID, dryrun=$DRYRUN)"
if [ "$DRYRUN" = "1" ]; then
    _log "DRYRUN: would run $ROT_SCRIPT --roles $LEAK_ROLE --execute"
    echo "DRYRUN decision: AUTO-ROTATE non-prod role '$LEAK_ROLE'"
    touch "$MARK" 2>/dev/null
    exit 0
fi

if timeout 120 bash "$ROT_SCRIPT" --roles "$LEAK_ROLE" --execute >> "$LOG_DIR/autorotate_${LEAK_ROLE}.log" 2>&1; then
    touch "$MARK" 2>/dev/null
    _log "✅ auto-rotated $LEAK_ROLE"
    notify "🔐✅ **leaked credential auto-rotated**: non-prod PG role \`$LEAK_ROLE\` rotated + propagated (chichibu/mars/talisker). Exposed value is now dead. session=\`$LEAK_SESSION_ID\`."
    last=$(cat "$STATE_DIR/last_issue" 2>/dev/null)
    [ -n "$last" ] && command -v gh >/dev/null 2>&1 && \
        timeout 20 gh issue comment -R hrmtz/claude-harness "$last" \
            --body "🔐✅ **auto-rotated** non-prod role \`$LEAK_ROLE\` (session \`$LEAK_SESSION_ID\`). Exposed password dead; new value propagated to all llm.enc.yaml copies. No manual action needed for this role." >/dev/null 2>&1
else
    _log "✗ auto-rotate FAILED for $LEAK_ROLE — see autorotate_${LEAK_ROLE}.log"
    escalate "AUTO-ROTATE FAILED for non-prod role \`$LEAK_ROLE\` (see ~/.claude/state/hook_logs/autorotate_${LEAK_ROLE}.log) — rotate manually"
fi
exit 0
