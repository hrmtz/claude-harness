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
# v3 (over-firing refinement, #26/#35 class): only EXECUTION intent fires, decided
# PER COMMAND SEGMENT. Earlier versions scanned the whole command string, so a
# read (grep/cat/sed the script) — or even a chained command that merely *mentions*
# the script as an argument — tripped the deny. v3 splits the command on control
# operators (; && || | newline) and, for each segment, fires ONLY when:
#   - the segment's LEADING token (after env/VAR= prefixes, and after a bash|sh|
#     source|. runner) IS the rotation script itself (basename match), i.e. the
#     script is being RUN, not passed as an argument to grep/cat/etc; OR
#   - the segment runs psql AND contains an ALTER ROLE/USER ... PASSWORD.
# Reading, searching, paging, or discussing rotation never fires.
#
# Gate: deny (with a high-visibility propagation reminder) unless prefixed
# PG_ROTATION_PROPAGATION_ACK=1. ack + re-run proceeds -- does not kill legit rotation.
#
# RESIDUAL (honest): not a shell parser. `bash -c "<script> ..."` (script inside a
# -c string) and `cat <script> | bash` (piping content to an interpreter) are not
# decoded and may slip; they are covered by the value-scrub / human review layers.

source "$(dirname "$0")/lib.sh"

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$CMD" ] && exit 0

# conscious-confirmation bypass: propagation is included in this operation
if echo "$CMD" | grep -qE '^[[:space:]]*PG_ROTATION_PROPAGATION_ACK=1([[:space:]]|$)'; then
    hook_log "pg_rotation_propagation_guard" "ACK present — rotation+propagation acknowledged, allowing"
    exit 0
fi

# read-only / informational invocations never rotate
echo "$CMD" | grep -qE -- '(--dry-run|--help|(^|[[:space:]])-h([[:space:]]|$))' && exit 0

ROT_SCRIPTS='_rotate_mars_pg_roles.sh|autorotate_leaked_cred.sh'

FIRE=0
# split on control operators into one segment per line, then judge each segment by
# its LEADING token (execution position) only.
SEGMENTS=$(printf '%s' "$CMD" | sed -E 's/(\|\||&&)/\n/g; s/[;|&]/\n/g')
while IFS= read -r seg; do
    [ -z "${seg//[[:space:]]/}" ] && continue
    # strip leading `env` and VAR=val assignments
    body=$(printf '%s' "$seg" | sed -E 's/^[[:space:]]*(env[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*//')
    tok1=$(printf '%s' "$body" | awk '{print $1}')
    tok1base="${tok1##*/}"               # basename (handles ./x and path/x)
    # (A) leading token IS the rotation script -> executing it
    if printf '%s' "$tok1base" | grep -qE "^($ROT_SCRIPTS)$"; then FIRE=1; break; fi
    # (A2) leading token is an interpreter/runner and the NEXT token is the script
    case "$tok1base" in
        bash|sh|zsh|dash|ksh|source|.|exec|eval)
            tok2=$(printf '%s' "$body" | awk '{print $2}')
            tok2base="${tok2##*/}"
            if printf '%s' "$tok2base" | grep -qE "^($ROT_SCRIPTS)$"; then FIRE=1; break; fi
            ;;
    esac
    # (B) password-change SQL actually executed via psql in this segment
    if printf '%s' "$seg" | grep -qiE '\bpsql\b' \
       && printf '%s' "$seg" | grep -qiE 'ALTER[[:space:]]+(ROLE|USER)[[:space:]]+[^;]*[[:space:]]+PASSWORD'; then
        FIRE=1; break
    fi
done <<EOF
$SEGMENTS
EOF

if [ "$FIRE" -eq 1 ]; then
    hook_log "pg_rotation_propagation_guard" "rotation EXECUTION gated pending propagation ack"
    MSG="⚠ PG rotation 検知 — rotation は「新 cred を全 consumer に propagate」するまで未完了。
古い cred で接続している app が落ちる → Mafutsu (mafutsu.com) 本番停止。

propagate 先 (全部): mars (canonical) / talisker / zetithnas + edge workers
  = 全 llm.enc.yaml copy に新 cred を同期するまでが 1 セット。

この operation に propagation が含まれているか確認:
  • 含まれている → 先頭に PG_ROTATION_PROPAGATION_ACK=1 を付けて再実行
  • 含まれていない → 同手順に propagation を追加してから実行
（rotation 自体は禁止していない。propagation 同梱の意識的確認を求めているだけ。
　読む/検索/言及するだけのコマンドは block しない。）"
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
