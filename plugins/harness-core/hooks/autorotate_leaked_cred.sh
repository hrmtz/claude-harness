#!/bin/bash
# autorotate_leaked_cred.sh — step 3 of the credential-leak chain: CLOSE THE LOOP.
#
# Chain: scrubber detects+sanitizes (step 1) → followup files incident (step 2) →
# THIS script rotates the leaked credential (step 3). Until now step 3 was manual
# ("rotation tracked in the issue"); this makes the safe case autonomous.
#
# Policy (deliberately conservative — a leak-DETECTION false-positive must never
# take down production OR be remotely inducible into rotating canonical mars):
#   - NON-PROD PG role  → SOURCE-TRUST gated (gh #41): auto-rotate ONLY when the leak
#     came from a POSITIVELY-identified trusted local credential op (single no-chain
#     command whose leading verb is sops exec-env / pg_dump(all) / the rotation
#     script). Everything else needs a per-role human-ack marker; commands matching
#     the untrusted denylist (external fetch / mailbox / transcript) escalate with a
#     clearer message. AUTO is allowlist-based (hard to forge), NOT "not-denylisted"
#     — so an evasive external fetch lands in ambiguous→ack, never auto (codex #41).
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
LEAK_TRUST="${LEAK_TRUST:-ambiguous}"   # trusted | untrusted | ambiguous (classified by credential_value_scrub.sh from the producing command; AUTO requires "trusted")
DRYRUN="${AUTOROTATE_DRYRUN:-0}"   # 1 = decide + log only, no real rotation (for tests)

# Project-specific rotation backend (PRS-LLM / mars). Absent on other projects → PG
# rotation simply unavailable here (escalate instead).
# gh #45: repointed from the DEPRECATED _rotate_mars_pg_roles.sh (2026-06-08 prod
# auth/RAG outage: incomplete distribution — no container env_file, skipped
# pg_premium :5435, mars-only verify) to the hardened v2 (env_file distribute +
# force-recreate, pg:5434 + pg_premium:5435 ALTER, per-origin /health/ready
# fail-closed verify). Arg contract verified compatible: autorotate passes only
# `--roles <role> --execute` for NON-prod roles (prod -> escalate), so v2's
# ROTATE_PROD_I_UNDERSTAND gate is never hit; --no-laddie defaults off (full distro).
ROT_SCRIPT="$HOME/projects/PRS-LLM-dev/scripts/_rotate_pg_roles_v2.sh"

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

# ── SOURCE-TRUST GATE (gh #41 refine — supersedes the blanket human-ack) ──────
# The self-DoS root cause is NOT "auto" — it is a leak DETECTION induced from an
# ATTACKER-CONTROLLABLE source (external fetch / peer mailbox / transcript /
# arbitrary read output). So gate on SOURCE TRUST, not a blanket ack (which killed
# the autonomous fast incident-response the operator wants to keep):
#   • untrusted source -> NEVER auto-rotate; escalate.            (advisory denylist)
#   • trusted source   -> auto-rotate. autonomous, no ack. (ALLOWLIST: a single,
#                         no-chain/no-subshell command whose bare leading verb is
#                         pg_dump/pg_dumpall, or `sops exec-env` wrapping one.)
#   • ambiguous source -> human-ack marker fallback (everything not positively trusted).
# LEAK_TRUST is classified upstream by credential_value_scrub.sh -> classify_leak_trust().
# LOAD-BEARING (codex #41): AUTO is allowlist-based, so an evasive external fetch
# (python/node/openssl/base64/git, or a wrapper/process-sub) is NOT trusted and lands
# in ambiguous -> ack, never auto. Full-safe (blanket human-gate) was rejected by the
# operator because it kills autonomous rotation; this preserves it only for the narrow
# provably-safe case.
if [ "$LEAK_TRUST" = "untrusted" ]; then
    escalate "non-prod role \`$LEAK_ROLE\` leaked from an UNTRUSTED source (external fetch / mailbox / transcript) — auto-rotation REFUSED to prevent attacker-induced self-DoS (gh #41). Rotate manually via runbook only if the leak is real: \`$ROT_SCRIPT --roles $LEAK_ROLE --execute\`."
    touch "$MARK" 2>/dev/null
    exit 0
fi
if [ "$LEAK_TRUST" != "trusted" ]; then
    # AMBIGUOUS (not positively trusted, not untrusted): human-ack fallback ONLY.
    # Corroboration-by-local-file was REMOVED after cross-family review (codex REJECT,
    # gh #41): the leaked DSN's host is canonical mars, whose @host:port/db tail is
    # ubiquitous in local config, so corroboration granted near-automatic trust and —
    # combined with the necessarily-incomplete untrusted denylist — bridged evasive
    # external fetches (python/openssl/git/ssh/base64) into auto-rotation. AUTO now
    # requires a POSITIVELY-identified trusted-op (see scrub classifier); nothing
    # else auto-rotates without an explicit human ack.
    ACK_DIR="$STATE_DIR/rotate_approved"; ACK_FILE="$ACK_DIR/$LEAK_ROLE"; acked=0
    # atomic one-shot claim of a file ack (mv wins for exactly one racer)
    if [ -f "$ACK_FILE" ] && mv "$ACK_FILE" "$ACK_FILE.consumed.$$" 2>/dev/null; then
        acked=1; rm -f "$ACK_FILE.consumed.$$" 2>/dev/null
    elif [ "${AUTOROTATE_ACK_ROLE:-}" = "$LEAK_ROLE" ]; then
        acked=1
    fi
    if [ "$acked" != "1" ]; then
        escalate "non-prod role \`$LEAK_ROLE\` leaked from a source that is not a positively-trusted local credential op — human ack required: \`mkdir -p $ACK_DIR && touch $ACK_FILE\` then re-invoke, or runbook \`$ROT_SCRIPT --roles $LEAK_ROLE --execute\`."
        # do NOT set dedup $MARK — the re-invoke after ack must not short-circuit
        exit 0
    fi
    _log "ambiguous source, human ack consumed for $LEAK_ROLE → proceeding"
fi
_log "source-trust gate passed (trust=$LEAK_TRUST) for $LEAK_ROLE — proceeding to rotation"

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
