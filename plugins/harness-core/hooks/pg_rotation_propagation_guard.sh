#!/bin/bash
# PreToolUse Bash hook: PG rotation -> propagation enforcement.
#
# User structural request (2026-06-27, separate from the #27 audit findings):
# rotating a PG credential WITHOUT propagating the new value to EVERY consumer
# (mars / talisker / zetithnas + edge llm.enc.yaml copies) drops apps onto a dead
# credential -> Mafutsu (mafutsu.com) PRODUCTION OUTAGE. Make "rotate" and
# "propagate to all consumers" one atomic operation, structurally.
#
# v4 (over-fire fix, gh #<over-fire-issue>): v3 split the command on ; && | and
# judged each segment, but the splitter ignored quoting, so a trigger word inside a
# QUOTED ARGUMENT (gh issue --body "..._rotate...", mailbox-send "%32" "...;_rotate...")
# became a false execution segment and got blocked — stopping real work. v4 looks
# ONLY at the LEADING command (the program actually executed): the rotation script as
# the leading token (or via bash/sh), or psql as the leading token running an
# ALTER ROLE/USER ... PASSWORD. A trigger word merely appearing as an argument never
# fires. Trade-off: a chained rotation (`cd x && _rotate...`) is under-detected — the
# reminder is advisory and over-fire blocking real work is the worse failure.
#
# Gate: deny (with a propagation reminder) unless prefixed PG_ROTATION_PROPAGATION_ACK=1.

source "$(dirname "$0")/lib.sh"

# Cross-CLI: parse_tool_command handles Claude/Codex + Grok payload shapes.
HOOK_INPUT=$(cat); export HOOK_INPUT
CMD=$(parse_tool_command)
[ -z "$CMD" ] && exit 0

# conscious-confirmation bypass
if printf '%s' "$CMD" | grep -qE '^[[:space:]]*PG_ROTATION_PROPAGATION_ACK=1([[:space:]]|$)'; then
    hook_log "pg_rotation_propagation_guard" "ACK present — allowing"
    exit 0
fi
# read-only / informational invocations never rotate
printf '%s' "$CMD" | grep -qE -- '(--dry-run|--help|(^|[[:space:]])-h([[:space:]]|$))' && exit 0

# Strip message/body argument VALUES so discussing rotation (commit -m / gh --body /
# --title) never trips the guard (over-fire lineage). Belt-and-suspenders on top of
# the leading-command-only logic below.
S="$CMD"
if command -v perl >/dev/null 2>&1; then
    S=$(perl -0777 -pe '
        s/(--?(?:m|message|b|body|title))\s+"(?:[^"\\]|\\.)*"/${1} _MSG_/g;
        s/(--?(?:m|message|b|body|title))\s+'\''(?:[^'\''\\]|\\.)*'\''/${1} _MSG_/g;
    ' <<< "$CMD")
else
    S=$(printf '%s' "$CMD" | sed -E 's/(-{1,2}(m|message|b|body|title))[[:space:]]+"[^"]*"/\1 _MSG_/g; s/(-{1,2}(m|message|b|body|title))[[:space:]]+'\''[^'\'']*'\''/\1 _MSG_/g')
fi

ROT_SCRIPTS='_rotate_mars_pg_roles\.sh|autorotate_leaked_cred\.sh'

# Leading command token of the WHOLE command (after env/VAR= prefixes) = the program
# actually executed. No operator splitting -> a trigger inside a quoted argument can
# never become the leading token.
# FIRST LINE only (v4.1 fix): on a multi-line command, awk '{print $1}' would return
# the first token of EVERY line, so a later line like `OLD=..._rotate_..sh` (a var
# assignment, not an execution) falsely matched. The leading command is the first
# token of the first line; later lines are separate commands and are under-detected
# by design (advisory reminder; over-fire blocking real work is the worse failure).
first=$(printf '%s' "$S" | sed -n '1p')
body=$(printf '%s' "$first" | sed -E 's/^[[:space:]]*(env[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*//')
t1=$(printf '%s' "$body" | awk 'NR==1{print $1}'); t1="${t1##*/}"
t2=$(printf '%s' "$body" | awk 'NR==1{print $2}'); t2="${t2##*/}"

FIRE=0
# (A) the rotation script is the executed program (leading token, or bash/sh <script>)
printf '%s' "$t1" | grep -qE "^($ROT_SCRIPTS)$" && FIRE=1
case "$t1" in
    bash|sh|zsh|dash|ksh|source|.|exec) printf '%s' "$t2" | grep -qE "^($ROT_SCRIPTS)$" && FIRE=1 ;;
esac
# (B) psql is the executed program AND it submits an ALTER ROLE/USER ... PASSWORD
if printf '%s' "$t1" | grep -qx 'psql' \
   && printf '%s' "$S" | grep -qiE 'ALTER[[:space:]]+(ROLE|USER)[[:space:]]+[^;]*[[:space:]]+PASSWORD'; then
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
（rotation 自体は禁止していない。読む/検索/言及/引数に語が入るだけのコマンドは block しない。）"
    emit_deny "$MSG"
fi
exit 0
