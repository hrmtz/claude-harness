#!/bin/bash
# PreToolUse Bash hook: redirect dangerous command patterns to safe alternatives.
#
# Design: HOOK_OUTPUT_DESIGN.md (silent on success, terse on failure,
# polarity retreat-counter — emit alternative action, not violation framing).
#
# 2026-04-27 dawn: credential leak #1-#13 連発 → input gate level で防衛
# 2026-04-29: B 系 patterns added from red team enumeration (#B1-B15)
# 2026-05-01 audit: prose rewritten per HOOK_OUTPUT_DESIGN — drop "blocked"
#                   wrapper, drop "Review CLAUDE.md..." trail, each pattern's
#                   reason now states the alternative action only.

source "$(dirname "$0")/lib.sh"

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

[ -z "$CMD" ] && exit 0

# ----------------------------------------
# Pre-process: strip documentation-context argument bodies before pattern match.
# Issue #1: commit messages / issue bodies that DESCRIBE patterns by name should
# not trip the guard. `bash -c '<inner>'` and `sh -c '<inner>'` are NOT stripped
# because those inner strings are real command invocations.
# ----------------------------------------
SCRUBBED="$CMD"
if command -v perl >/dev/null 2>&1; then
    SCRUBBED=$(perl -0777 -pe '
        s/(--?(?:m|message|b|body|title))\s+"(?:[^"\\]|\\.)*"/${1} _MSG_REDACTED_/g;
        s/(--?(?:m|message|b|body|title))\s+'\''(?:[^'\''\\]|\\.)*'\''/${1} _MSG_REDACTED_/g;
    ' <<< "$CMD")
else
    SCRUBBED=$(echo "$CMD" | sed -E 's/(-{1,2}(m|message|b|body|title))[[:space:]]+"[^"]*"/\1 _MSG_REDACTED_/g; s/(-{1,2}(m|message|b|body|title))[[:space:]]+'\''[^'\'']*'\''/\1 _MSG_REDACTED_/g')
fi

# ----------------------------------------
# Pattern → alternative action catalog.
# Format: <regex>:::<terse alternative action>
# Reason field is action-only: tells the agent what to do, not what was wrong.
# ----------------------------------------
declare -a PATTERNS_REASONS=(
    # === A 系 ===
    'sops[[:space:]]+(-d|--decrypt)([[:space:]]|$):::sops edit <file> または sops exec-env <file> '"'"'<cmd>'"'"' で行ける'
    'docker[[:space:]]+(container[[:space:]]+)?inspect.*--format.*\.Config\.Env:::compose env_file 経由か sops exec-env で env 参照'
    'env[[:space:]]*\|[[:space:]]*(grep|awk|sed|fgrep|egrep|rg|tr).*(KEY|TOKEN|PASSWORD|PASSWD|SECRET|CRED):::env | cut -d= -f1 で key 名のみ取れる'
    'bash[[:space:]]+-x.*(printf|echo).*\$[A-Z_]+:::set +x で expansion 抑制、必要なら [ -n "\$X" ] && echo set で bool 確認'
    'cat[[:space:]].*(\.env|\.env\.[a-z]+|\.aws/credentials)([[:space:]]|$):::sops exec-env <file> '"'"'<cmd>'"'"' で env 注入経由'
    '(^|[^a-zA-Z_/])(head|tail)([[:space:]]+[^[:space:]&|;<>]+)+\.enc\.(yaml|json):::sops edit でそのまま開ける、preview 不要'
    'curl.*(-H[[:space:]]|--header[[:space:]]).*Bearer[[:space:]]+[A-Za-z0-9_+/=-]{30,}:::-H "Authorization: Bearer \$TOKEN" で env 経由 (cmdline 焼付回避)'
    'rclone.*--s3-access-key-id[[:space:]]+[A-Za-z0-9]+:::sops exec-env r2.enc.yaml '"'"'rclone ...'"'"' で env 経由'
    # 2026-05-03 incident #14: rclone -vv が "Setting access_key_id=..." を plaintext で log 出力
    # → verbose flag が env 値 expose、sops exec-env 組合せで credential leak path
    'rclone[[:space:]].*([[:space:]]|^)(-vv|-vvv|--verbose|-d|--debug)([[:space:]]|$):::rclone は plain (no -v) か -v 単発以下に。-vv/--debug は env 値 plaintext print。進捗 monitor は --stats=15s --progress 単独で十分'
    'curl.*\?api[_-]?key=:::-H "Authorization: Bearer \$KEY" で URL log 焼付回避'
    'tail[[:space:]].+(rclone\.conf|\.netrc|\.aws/credentials):::sops exec-env で値直接参照 (file 内容露出不要)'
    'sops[[:space:]]+exec-env[[:space:]].+['\''"].*[[:space:]]*(curl|wget|http|axios)[[:space:]]:::scripts/ に repo-baked script 置いて sops exec-env <file> <script-path> で呼ぶ'

    # === B 系 (#B1-B15) ===
    'printenv[[:space:]]+[A-Z_]*(KEY|TOKEN|PASSWORD|PASSWD|SECRET|CRED)[A-Z_]*([[:space:]]|$):::env | cut -d= -f1 で key 名のみ取れる'
    'echo[[:space:]].*\$\{?[A-Z_]*(TOKEN|PASSWORD|PASSWD|SECRET|CRED)[A-Z_]*\}?:::[ -n "\$X" ] && echo set で bool 確認'
    'printf.*\$\{?[A-Z_]*(TOKEN|PASSWORD|PASSWD|SECRET|CRED)[A-Z_]*\}?:::[ -n "\$X" ] && echo set で bool 確認'
    '(declare|typeset|export)[[:space:]]+-p[[:space:]]+[A-Z_]*(KEY|TOKEN|PASSWORD|PASSWD|SECRET|CRED)[A-Z_]*([[:space:]]|$):::env | cut -d= -f1 で key 名のみ取れる'
    '(^|;|&&|[[:space:]])set[[:space:]]*($|;|\n)|set[[:space:]]+\|[[:space:]]*(grep|head|tail|awk|sed):::env | cut -d= -f1 で key 名のみ取れる (set は env+func 全 dump で過剰)'
    '(^|[^a-zA-Z_/])cat[[:space:]]+/proc/[^[:space:]]+/environ:::ps p <pid> -o comm,args で代替 (env 不要なら)'
    '(^|[^a-zA-Z_/])ps[[:space:]]+[a-z]*e[a-z]*([[:space:]]|$)|(^|[^a-zA-Z_/])ps[[:space:]]+-o[[:space:]]+[a-z,]*environ:::ps -o pid,comm,args で env 出さず取れる'
    'sops[[:space:]]+exec-env[[:space:]].+['\''"][[:space:]]*(python[3]?|node|deno|bun|ruby|perl|php|bash|sh|dash|zsh)[[:space:]]+-[ce]([[:space:]]|$):::scripts/ に repo-baked script 置いて sops exec-env <file> <script-path> で呼ぶ'
    'sops[[:space:]]+exec-env[[:space:]].+[^a-zA-Z_]eval[[:space:]]:::eval 抜きで script 化、sops exec-env <file> <script-path>'
    'sops[[:space:]]+exec-env[[:space:]].+['\''"][^'\''"]*[[:space:]]>[[:space:]]*[^[:space:]&|]:::redirect は plain text のみ、credential 値は file 化しない'
    'env[[:space:]]+>[[:space:]]*[^[:space:]&|]:::env | cut -d= -f1 > file で key 名のみ retain'
)

VIOLATION_FOUND=0
VIOLATION_MSGS=""

for entry in "${PATTERNS_REASONS[@]}"; do
    pattern="${entry%%:::*}"
    reason="${entry#*:::}"
    if echo "$SCRUBBED" | grep -qE "$pattern"; then
        VIOLATION_FOUND=1
        VIOLATION_MSGS="${VIOLATION_MSGS}- ${reason}\n"
        prefix=$(echo "$pattern" | head -c 40)
        hook_log "bash_command_guard" "matched pattern: ${prefix}..."
    fi
done

if [ "$VIOLATION_FOUND" -eq 1 ]; then
    # Redirect via deny: action-only bullet list + retreat-counter close.
    # `printf -- '%b'` so VIOLATION_MSGS leading "- " isn't parsed as a flag,
    # and so embedded \n escape sequences are interpreted as real newlines.
    MSG=$(printf -- '%b\n次これで行こう。' "$VIOLATION_MSGS")
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
