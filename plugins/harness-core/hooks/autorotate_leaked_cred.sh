#!/bin/bash
# autorotate_leaked_cred.sh — step 3 of the credential-leak chain: CLOSE THE LOOP.
#
# Chain: scrubber detects+sanitizes (step 1) → followup files incident (step 2) →
# THIS script rotates the leaked credential (step 3). Until now step 3 was manual
# ("rotation tracked in the issue"); this makes the safe case autonomous.
#
# Policy (deliberately conservative — a leak-DETECTION false-positive must never
# take down production OR be remotely inducible into rotating canonical mars):
#   - NON-PROD PG role  → eligible, but HUMAN-GATED (gh #33/#41): rotate only with an
#     explicit per-role ack (marker file / AUTOROTATE_ACK_ROLE). Without ack →
#     escalate with a runbook command. This is because a leak DETECTION can be forced
#     by attacker-controlled tool output, so detection alone must not --execute.
#   - PROD PG role / bare prs / API key → never auto-rotate (rolling redeploy /
#     provider-side action). Escalate HARD so a human does it with the Phase 2 runbook.
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
            --body "⚠️ **auto-rotate DECLINED — manual action needed.** $reason (session \`$LEAK_SESSION_ID\`). Since gh #33/#41 ALL rotations are human-gated (a leak detection can be attacker-induced), so no credential is rotated without an explicit ack / runbook run." >/dev/null 2>&1
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
    *" $LEAK_ROLE "*) : ;;   # non-prod: ELIGIBLE, but still gated below
    *)
        escalate "role \`$LEAK_ROLE\` is not in the known non-prod rotation set; rotate manually after confirming it's safe"
        exit 0 ;;
esac

# ── HUMAN GATE (gh #33/#41) ──────────────────────────────────────────────────
# Non-prod eligibility is NECESSARY but NOT SUFFICIENT. A leak DETECTION can be
# induced by attacker-controlled tool output (poisoned file / WebFetch / MCP /
# mailbox body), so even a non-prod role must NOT auto --execute a real rotation
# against canonical mars without an explicit human ack. This closes the remotely-
# inducible self-DoS observed during the #27 audit.
#   ack = a marker file a human creates (PREFERRED — atomic, one-shot, audited):
#         touch "$ROT_MARK_DIR/../rotate_approved/<role>"
#   or   = AUTOROTATE_ACK_ROLE=<role> in the invocation env (escape hatch; NOT
#          one-shot — only set it for a single deliberate invocation, never export
#          it broadly, or repeated detections could rotate repeatedly).
# Without ack: escalate with a ready-to-run runbook command, mark dedup, exit 0.
ACK_DIR="$STATE_DIR/rotate_approved"
ACK_FILE="$ACK_DIR/$LEAK_ROLE"
acked=0
# Atomic one-shot claim of a file ack: mv succeeds for exactly one racer, so two
# concurrent autorotate processes can never both consume the same approval.
if [ -f "$ACK_FILE" ] && mv "$ACK_FILE" "$ACK_FILE.consumed.$$" 2>/dev/null; then
    acked=1; rm -f "$ACK_FILE.consumed.$$" 2>/dev/null
elif [ "${AUTOROTATE_ACK_ROLE:-}" = "$LEAK_ROLE" ]; then
    acked=1
fi
if [ "$acked" != "1" ]; then
    escalate "non-prod role \`$LEAK_ROLE\` leaked — HUMAN GATE held (gh #33/#41 self-DoS guard). Auto-rotation requires explicit approval. To approve: \`mkdir -p $ACK_DIR && touch $ACK_FILE\` then re-invoke, or run the runbook directly: \`$ROT_SCRIPT --roles $LEAK_ROLE --execute\`."
    # NOTE: deliberately do NOT set the dedup $MARK here — otherwise the very
    # re-invoke the human is told to do would short-circuit on the dedup check.
    exit 0
fi
_log "human ack consumed for $LEAK_ROLE — proceeding to gated rotation"

# ── gated rotation (non-prod, zero-outage, human-approved) ───────────────────
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
