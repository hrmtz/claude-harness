#!/bin/bash
# PreToolUse Bash hook: dangerous command pattern を実行前に block
# Defense in depth: behavioral remember (CLAUDE.md) を structural rail で固定
# 2026-04-27 dawn、credential leak #1-#13 連発 → input gate level で防衛

source "$(dirname "$0")/lib.sh"

INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

[ -z "$CMD" ] && exit 0

# ----------------------------------------
# Pre-process: strip documentation-context argument bodies before pattern match.
# Issue #1: commit messages / issue bodies that DESCRIBE dangerous patterns by
# name should not trip the guard — patterns reflect actual command invocation
# intent, not literal mentions in -m/--message/-b/--body/--title args.
# Note: `bash -c 'inner cmd'` and `sh -c '...'` are NOT stripped because those
# inner strings ARE real command invocations.
# ----------------------------------------
SCRUBBED="$CMD"
if command -v perl >/dev/null 2>&1; then
    SCRUBBED=$(perl -0777 -pe '
        s/(--?(?:m|message|b|body|title))\s+"(?:[^"\\]|\\.)*"/${1} _MSG_REDACTED_/g;
        s/(--?(?:m|message|b|body|title))\s+'\''(?:[^'\''\\]|\\.)*'\''/${1} _MSG_REDACTED_/g;
    ' <<< "$CMD")
else
    # Fallback (single-line only; multi-line heredoc messages may slip past).
    SCRUBBED=$(echo "$CMD" | sed -E 's/(-{1,2}(m|message|b|body|title))[[:space:]]+"[^"]*"/\1 _MSG_REDACTED_/g; s/(-{1,2}(m|message|b|body|title))[[:space:]]+'\''[^'\'']*'\''/\1 _MSG_REDACTED_/g')
fi

# ----------------------------------------
# Block pattern catalog (regex → reason)
# ----------------------------------------
# POSIX ERE-safe patterns、separator は ::: (regex alternation `|` との衝突回避)
declare -a PATTERNS_REASONS=(
    'sops[[:space:]]+(-d|--decrypt)([[:space:]]|$):::sops -d / --decrypt は positive rule 違反、sops edit か sops exec-env <file> <cmd> を使う'
    'docker[[:space:]]+(container[[:space:]]+)?inspect.*--format.*\.Config\.Env:::docker inspect --format で Env 全 dump は credential 全文露出 (#9 vector)'
    'env[[:space:]]*\|[[:space:]]*grep.*(KEY|TOKEN|PASSWORD|SECRET):::env grep KEY/TOKEN/PASSWORD は値露出 (#1-#5)、env | cut -d= -f1 で key 名のみ'
    'bash[[:space:]]+-x.*(printf|echo).*\$[A-Z_]+:::bash -x で env 変数 expansion は会話ログ焼付 (#2 vector)'
    'cat[[:space:]].*(\.env|\.env\.[a-z]+|\.aws/credentials)([[:space:]]|$):::env / credentials の cat は平文露出'
    '(head|tail)[[:space:]]+.+\.enc\.(yaml|json):::sops encrypted file の head/tail は意味なし、plain file 誤読 vector'
    'curl.*-H.*Bearer[[:space:]]+[A-Za-z0-9_+/=-]{30,}:::curl inline Bearer token は cmdline 焼付、-H "Authorization: Bearer \$TOKEN" 形式に'
    'rclone.*--s3-access-key-id[[:space:]]+[A-Za-z0-9]+:::rclone --s3-access-key-id inline は cmdline 露出 (#12 寸前)'
    'curl.*\?api[_-]?key=:::URL query に api_key= は server log 焼付'
    'tail[[:space:]].+(rclone\.conf|\.netrc|\.aws/credentials):::credential file tail は平文露出'
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
        hook_log "bash_command_guard" "BLOCKED pattern: ${prefix}..."
    fi
done

if [ "$VIOLATION_FOUND" -eq 1 ]; then
    # block via JSON response (permissionDecision: deny)
    jq -n --arg msg "$(printf "🛡 Bash command blocked by credential leak guard:\n${VIOLATION_MSGS}\nReview CLAUDE.md SOPS section + memory \`feedback_credential_leak_5_incidents\` for safe alternatives.")" '{
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": $msg
        }
    }'
    exit 0
fi

# allow (no output, exit 0)
exit 0
