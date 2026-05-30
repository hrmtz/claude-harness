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
    'env[[:space:]]*\|[[:space:]]*(grep|awk|sed|fgrep|egrep|rg|tr|head|tail):::env | cut -d= -f1 で key 名のみ、値は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'
    'bash[[:space:]]+-x.*(printf|echo).*\$[A-Z_]+:::set +x で expansion 抑制、必要なら [ -n "\$X" ] && echo set で bool 確認'
    # L47 (cat .env|.aws/credentials) は下記 cred-file-read 統合 pattern が subsume、 ack 経路一本化のため削除
    '(^|[^a-zA-Z_/])(head|tail)([[:space:]]+[^[:space:]&|;<>]+)+\.enc\.(yaml|json):::sops edit でそのまま開ける、preview 不要'
    'curl.*(-H[[:space:]]|--header[[:space:]]).*Bearer[[:space:]]+[A-Za-z0-9_+/=-]{30,}:::-H "Authorization: Bearer \$TOKEN" で env 経由 (cmdline 焼付回避)'
    'rclone.*--s3-access-key-id[[:space:]]+[A-Za-z0-9]+:::sops exec-env r2.enc.yaml '"'"'rclone ...'"'"' で env 経由'
    # 2026-05-03 incident #14: rclone -vv が "Setting access_key_id=..." を plaintext で log 出力
    # → verbose flag が env 値 expose、sops exec-env 組合せで credential leak path
    'rclone[[:space:]].*([[:space:]]|^)(-vv|-vvv|--verbose|-d|--debug)([[:space:]]|$):::rclone は plain (no -v) か -v 単発以下に。-vv/--debug は env 値 plaintext print。進捗 monitor は --stats=15s --progress 単独で十分'
    'curl.*\?api[_-]?key=:::-H "Authorization: Bearer \$KEY" で URL log 焼付回避'
    # L55 (tail rclone.conf|.netrc|.aws/credentials) は下記 cred-file-read 統合 pattern が subsume
    # 2026-05-11 incident #21: Read tool で .env 全 dump、 7 key 漏洩。 Bash 経路も同 risk
    # → cat/head/tail/less/more/bat の credential file 直接 dump を統合 block
    '(^|[^a-zA-Z_/])(cat|head|tail|less|more|bat)[[:space:]]+[^|]*?(/\.env([[:space:]]|$|\.(common|prod|production|local|dev|staging|hetzner|laddie|chichibu|zetithnas|talisker|mars|farm)([[:space:]]|$))|rclone\.conf|/\.netrc|/\.aws/credentials|\.cloudflared/[^[:space:]]+\.json|\.pem([[:space:]]|$)|\.key([[:space:]]|$)|\.p12([[:space:]]|$)):::sops exec-env <file> '"'"'env | cut -d= -f1'"'"' で key 名のみ取得、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'
    # 2026-05-13 incident #22: Magi 2 agent が `grep KEY .env` 実行、 マッチ行全体が stdout に出て value 露出
    # 旧 comment 「grep -n KEY .env で line 番号のみ」 は誤、 grep default は match line 全文表示で value 焼く
    # → grep/egrep/fgrep/rg/awk/sed の credential file 直接 read も block、 ack-bypass 経路一本化
    '(^|[^a-zA-Z_/])(grep|egrep|fgrep|rg|awk|sed)[[:space:]]+[^|]*?(/\.env([[:space:]]|$|\.(common|prod|production|local|dev|staging|hetzner|laddie|chichibu|zetithnas|talisker|mars|farm)([[:space:]]|$))|rclone\.conf|/\.netrc|/\.aws/credentials|\.cloudflared/[^[:space:]]+\.json|\.pem([[:space:]]|$)|\.key([[:space:]]|$)|\.p12([[:space:]]|$)):::sops exec-env <file> '"'"'env | grep -c <KEY>'"'"' で件数のみ確認、 key 名 list は cut -d= -f1、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'
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

    # 2026-05-04 incident #15: docker exec <container> env が container 内 env 全 dump
    # POSTGRES_PASSWORD 直撃、container 起動時の compose env_file → env 経路で内部に焼付済値を host scope に出した
    # → key 名のみ取得は cut -d= -f1、値要なら sops exec-env 経由で対象 env 注入
    'docker[[:space:]]+(container[[:space:]]+)?exec[[:space:]].+[[:space:]]env([[:space:]]*$|[[:space:]]+\|[[:space:]]*(grep|awk|sed|fgrep|egrep|rg|tr|head|tail)):::docker exec <ct> env | cut -d= -f1 で key 名のみ、値必要なら sops exec-env <file> '"'"'docker exec <ct> sh -c "..."'"'"' 経由'

    # === C 系 (#24-34、 2026-05-27 cli internal credential dump) ===
    # 既存 cat/head/tail rclone.conf は file 直接 read を block 済、 C 系は cli 内蔵 dump コマンド経路
    # incident #24 (= rclone config show で R2 secret 全 plain stdout、 acusis-migration R2 setup で踏んだ)
    # を契機に、 同種 cli internal dump コマンドを preemptive enum

    # incident #24: rclone config show/dump で secret_access_key 平文 stdout
    'rclone[[:space:]]+config[[:space:]]+(show|dump)([[:space:]]|$):::rclone listremotes で remote 名のみ、 field 一覧は rclone config show <remote> | awk -F= '"'"'/=/ {gsub(/ /,"",$1); print $1}'"'"'、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'

    # preemptive #25: aws configure get/list で AWS SecretAccessKey 平文 stdout
    'aws[[:space:]]+configure[[:space:]]+(get|list)([[:space:]]|$):::aws configure list-profiles で profile 名のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'

    # preemptive #26: gh auth token で GitHub PAT 平文 stdout
    'gh[[:space:]]+auth[[:space:]]+token([[:space:]]|$):::gh auth status で login 状態のみ確認、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'

    # preemptive #27: gcloud auth print-(access|identity|refresh)-token で credential 平文 stdout
    'gcloud[[:space:]]+auth[[:space:]]+print-(access-token|identity-token|refresh-token)([[:space:]]|$):::gcloud auth list で account 名のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'

    # preemptive #28: doctl auth list/token で DigitalOcean PAT 平文 stdout
    'doctl[[:space:]]+auth[[:space:]]+(list|token)([[:space:]]|$):::doctl account get で account 確認、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'

    # preemptive #29: kubectl config view --raw で client-cert + bearer token 全 dump (--raw が sanitize 解除)
    'kubectl[[:space:]]+config[[:space:]]+view.*--raw:::kubectl config view (--raw 抜き) で sanitized 表示、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'

    # preemptive #30: kubectl get secret -o yaml/json で base64-encoded secret 全 dump (= base64 -d で trivial 復元)
    'kubectl[[:space:]]+get[[:space:]]+secret.*-o[[:space:]]+(yaml|json):::kubectl get secret で metadata のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'

    # preemptive #31: docker secret/config inspect で stored credential 露出
    'docker[[:space:]]+(secret|config)[[:space:]]+inspect([[:space:]]|$):::docker (secret|config) ls で ID のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'

    # preemptive #32: flyctl auth token で Fly.io PAT 平文 stdout
    'flyctl[[:space:]]+auth[[:space:]]+token([[:space:]]|$):::flyctl auth whoami で login 状態のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'

    # preemptive #33: vercel env pull で env 全 download to .env (file 経路だが downstream cat で leak risk 同等)
    'vercel[[:space:]]+env[[:space:]]+pull([[:space:]]|$):::vercel env ls で key 名のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass (file 経路でも downstream leak risk 同等)'

    # preemptive #34: pass show / pass -c で stored credential 平文 stdout (-c は clipboard だが pipe で stdout 化可)
    'pass[[:space:]]+(show|-c)([[:space:]]|$):::pass ls で key list のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass'
)

VIOLATION_FOUND=0
VIOLATION_MSGS=""

# ack bypass: command が `HRMTZ_ACK_CRED_READ=1 ...` で始まる場合、
# reason に "HRMTZ_ACK_CRED_READ=1" を含む pattern (= credential file read 専用)
# のみ bypass する。 sops -d 等他の危険 pattern は依然として block。
ACK_BYPASS=0
if echo "$CMD" | grep -qE '^[[:space:]]*HRMTZ_ACK_CRED_READ=1[[:space:]]+'; then
    ACK_BYPASS=1
fi

for entry in "${PATTERNS_REASONS[@]}"; do
    pattern="${entry%%:::*}"
    reason="${entry#*:::}"
    if echo "$SCRUBBED" | grep -qE "$pattern"; then
        if [ "$ACK_BYPASS" -eq 1 ] && echo "$reason" | grep -q "HRMTZ_ACK_CRED_READ=1"; then
            hook_log "bash_command_guard" "BYPASS via HRMTZ_ACK_CRED_READ=1 for cred-file-read pattern"
            continue
        fi
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
