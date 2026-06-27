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
# v2 (over-firing refinement): v1 matched on the rotation token appearing ANYWHERE
# in the command text, so a READ-ONLY command (grep/cat/sed -n the script, or even
# discussing it in a commit/issue body) tripped the deny -- the #26/#35 over-firing
# failure class. v2 fires ONLY on EXECUTION INTENT:
#   - the rotation script invoked in COMMAND position (start / after a separator /
#     via bash|sh|source), not as an argument to a reader; OR
#   - a password-change SQL actually submitted via psql.
# Read/search/pager verbs (grep/cat/sed/less/...) and message-body args
# (-m/--body/--title) are excluded so inspecting or talking about rotation is fine.
#
# Gate: deny (with a high-visibility propagation reminder) unless prefixed
# PG_ROTATION_PROPAGATION_ACK=1. ack + re-run proceeds -- does not kill legit rotation.

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

# Strip message/body argument values so DISCUSSING rotation (git commit -m "...",
# gh issue --body '...', --title) never trips the guard (over-fire lineage #26/#36).
S="$CMD"
if command -v perl >/dev/null 2>&1; then
    S=$(perl -0777 -pe '
        s/(--?(?:m|message|b|body|title))\s+"(?:[^"\\]|\\.)*"/${1} _MSG_/g;
        s/(--?(?:m|message|b|body|title))\s+'\''(?:[^'\''\\]|\\.)*'\''/${1} _MSG_/g;
    ' <<< "$CMD")
else
    S=$(echo "$CMD" | sed -E 's/(-{1,2}(m|message|b|body|title))[[:space:]]+"[^"]*"/\1 _MSG_/g; s/(-{1,2}(m|message|b|body|title))[[:space:]]+'\''[^'\'']*'\''/\1 _MSG_/g')
fi

# Leading verb (after `env` and VAR=val prefixes). If the command is a SIMPLE
# (no chain operator) read/search/pager/VCS/metadata command, it is INSPECTING,
# not executing a rotation -> allow. The no-chain guard is essential: `cd x &&
# _rotate...sh` leads with `cd` but DOES execute, so it must fall through to the
# execution-position check below rather than being skipped here.
HAS_CHAIN=0
echo "$S" | grep -qE '(\|\||&&|[;|&])' && HAS_CHAIN=1
if [ "$HAS_CHAIN" -eq 0 ]; then
    LEAD=$(echo "$S" | sed -E 's/^[[:space:]]*(env[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*//' | awk '{print $1}')
    case "$LEAD" in
        grep|egrep|fgrep|rg|ag|cat|bat|head|tail|less|more|sed|awk|gawk|ls|find|stat|file|wc|cut|tr|sort|uniq|column|jq|yq|echo|printf|test|'['|man|which|type|vim|view|nano|emacs|diff|colordiff|git|gh|cd|pwd|tree|du|nl|tac|od|xxd|hexdump|strings)
            exit 0 ;;
    esac
fi

FIRE=0
# (A) rotation SCRIPT in execution position: command start, or after a control
# operator / shell runner (bash|sh|zsh|source|.|exec|eval) / opening quote, with
# optional env-assignment and path prefixes. Excludes the script appearing as a
# reader's argument (e.g. `grep _rotate_..._roles.sh` — grep is filtered above and
# there is no separator/runner before the name).
if echo "$S" | grep -qE '(^|[;&|(`"'\'']|&&|\|\|)[[:space:]]*((env|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+)[[:space:]]+)*((bash|sh|zsh|source|exec|eval|\.)[[:space:]]+)?((env|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+)[[:space:]]+)*(\./|[^[:space:];&|`"'\'']*/)?(_rotate_mars_pg_roles|autorotate_leaked_cred)\.sh'; then
    FIRE=1
fi
# (B) password-change SQL actually executed via psql (psql present AND ALTER ROLE/
# USER ... PASSWORD in the same command). `grep "ALTER ROLE..."` is excluded by the
# leading-verb filter; `echo`/commit-body are excluded above.
if echo "$S" | grep -qiE '\bpsql\b' && echo "$S" | grep -qiE 'ALTER[[:space:]]+(ROLE|USER)[[:space:]]+[^;]*[[:space:]]+PASSWORD'; then
    FIRE=1
fi

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
