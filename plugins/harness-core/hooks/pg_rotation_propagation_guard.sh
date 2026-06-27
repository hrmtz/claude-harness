#!/bin/bash
# PreToolUse Bash hook: PG rotation -> propagation enforcement.
#
# User structural request (2026-06-27, separate from the #27 audit findings):
# rotating a PG credential WITHOUT propagating the new value to EVERY consumer
# (mars canonical / talisker / zetithnas + edge workers, i.e. all llm.enc.yaml
# copies) leaves apps connecting with the now-dead credential -> Mafutsu
# (mafutsu.com) PRODUCTION OUTAGE. "rotate" and "propagate to all consumers" must
# be ONE atomic operation. Enforce structurally (this hook), not behaviorally.
#
# Gate design (deliberately strong, but does not kill a legitimate rotation):
# a rotation-shaped command is DENIED with a high-visibility propagation reminder
# UNLESS prefixed with PG_ROTATION_PROPAGATION_ACK=1 — forcing a conscious
# confirmation that propagation is part of the same operation. ack + re-run = OK.
# (Same idiom as the credential-read ack in bash_command_guard.sh.)
#
# NB: this hook only guarantees the REMINDER/gate. Whether _rotate_mars_pg_roles.sh
# actually distributes to every consumer is a separate concern (tracked separately).

source "$(dirname "$0")/lib.sh"

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$CMD" ] && exit 0

# read-only / informational invocations do not rotate anything -> never gate
echo "$CMD" | grep -qE -- '(--dry-run|--help|(^|[[:space:]])-h([[:space:]]|$))' && exit 0

# rotation-shaped patterns (case-insensitive). Specific known entrypoints + the
# generic SQL password-change, kept tight to avoid false positives on unrelated
# commands that merely contain "rotate" (e.g. log rotation).
ROT_PATTERNS='(_rotate_mars_pg_roles\.sh|autorotate_leaked_cred|ALTER[[:space:]]+(ROLE|USER)[[:space:]]+[^;]*[[:space:]]+PASSWORD|rotate[._-][a-z0-9_]*(pg|postgres|role|cred))'

if echo "$CMD" | grep -qiE "$ROT_PATTERNS"; then
    # conscious-confirmation bypass: propagation is included in this operation
    if echo "$CMD" | grep -qE '^[[:space:]]*PG_ROTATION_PROPAGATION_ACK=1([[:space:]]|$)'; then
        hook_log "pg_rotation_propagation_guard" "ACK present — rotation+propagation acknowledged, allowing"
        exit 0
    fi
    hook_log "pg_rotation_propagation_guard" "rotation-shaped command gated pending propagation ack"
    MSG="⚠ PG rotation 検知 — rotation は「新 cred を全 consumer に propagate」するまで未完了。
古い cred で接続している app が落ちる → Mafutsu (mafutsu.com) 本番停止。

propagate 先 (全部): mars (canonical) / talisker / zetithnas + edge workers
  = 全 llm.enc.yaml copy に新 cred を同期するまでが 1 セット。

この operation に propagation が含まれているか確認:
  • 含まれている → 先頭に PG_ROTATION_PROPAGATION_ACK=1 を付けて再実行
  • 含まれていない → 同手順に propagation を追加してから実行
（rotation 自体は禁止していない。propagation 同梱の意識的確認を求めているだけ。）"
    jq -n --arg msg "$MSG" '{
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": $msg
        }
    }'
    exit 0
fi
exit 0
