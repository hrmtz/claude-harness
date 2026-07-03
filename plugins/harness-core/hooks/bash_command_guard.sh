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

# Cross-CLI: parse_tool_command handles Claude/Codex `.tool_input.command` AND
# Grok `.toolInput.command`. HOOK_INPUT is set so the helper doesn't re-read stdin.
HOOK_INPUT=$(cat); export HOOK_INPUT
CMD=$(parse_tool_command)

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
# De-obfuscation pass (issue #36 cross-family REVISE, HIGH):
# the basename layer below matches a *literal contiguous* `.env` / `credentials.<ext>`,
# so reads assembled via shell token construction slip past it. Strip the most
# common, cheap-to-reverse obfuscations into DEOBF so the obvious bypasses surface:
#   cat .e"nv"            → quote-splicing          → strip quotes
#   cat ${PWD}/.e${X:-nv} → ${VAR}/${VAR<op>word}   → collapse param-expansion
#   cat .e${X-nv}         → ${VAR-w} / ${VAR:=w} …  → all :-/-/:=/=/:+/+ op forms
#   cat $'\056env'        → ANSI-C \056 (octal '.') → decode \056 / \x2e to '.'
# Patterns are matched against BOTH SCRUBBED and DEOBF (OR) so quote-dependent
# patterns keep matching SCRUBBED while obfuscated reads get caught in DEOBF.
# RESIDUAL (honest scope note): this is ONE defence-in-depth layer, not a shell
# parser. Genuine token *concatenation* (`open("."+"env")`), command substitution
# (`cat $(printf .env)`), base64/hex-piped reconstruction, and `eval`-built strings
# are NOT decoded here and remain the job of the value-scrub + autorotate layers.
DEOBF=$(echo "$SCRUBBED" | sed -E '
    s/\$\{IFS\}/ /g;
    s/\$IFS([^A-Za-z0-9_]|$)/ \1/g;
    s/\\0?56/./g;
    s/\\x2[eE]/./g;
    s/\$\{[A-Za-z_][A-Za-z0-9_]*:?[-=+]([^}]*)\}/\1/g;
    s/\$\{[A-Za-z_][A-Za-z0-9_]*\}//g;
    s/\$'\''//g;
    s/\$"//g;
    s/['\''"]//g;
')

# ----------------------------------------
# Pattern → alternative action catalog.
# Format: <regex>:::<terse alternative action>[:::<flags>]
# Reason field is action-only: tells the agent what to do, not what was wrong.
# flags (optional, comma-separated) — issue #36 cross-family REVISE (MED):
#   ack  → HRMTZ_ACK_CRED_READ=1 may consciously bypass THIS pattern. Previously
#          ack keyed off the reason *substring* containing the token, which
#          silently whitelisted any pattern whose prose happened to mention it.
#          The bypass is now explicit per-pattern metadata, decoupled from prose.
#   meta → non-reading verbs on the operand are allowed for THIS pattern: pure
#          metadata (test/[/ls/find/stat/git status) plus literal-print verbs
#          (echo/printf, which print their args and cannot read file content).
#          Actual readers (cat/grep/python -c …) stay blocked regardless of verb.
# ----------------------------------------
declare -a PATTERNS_REASONS=(
    # === A 系 ===
    'sops[[:space:]]+(-d|--decrypt)([[:space:]]|$):::sops edit <file> または sops exec-env <file> '"'"'<cmd>'"'"' で行ける'
    'docker[[:space:]]+(container[[:space:]]+)?inspect.*--format.*\.Config\.Env:::compose env_file 経由か sops exec-env で env 参照'
    'env[[:space:]]*\|[[:space:]]*(grep|awk|sed|fgrep|egrep|rg|tr|head|tail):::env | cut -d= -f1 で key 名のみ、値は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'
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
    '(^|[^a-zA-Z_/])(cat|head|tail|less|more|bat)[[:space:]]+[^|]*?(/\.env([[:space:]]|$|\.(common|prod|production|local|dev|staging|hetzner|laddie|chichibu|zetithnas|talisker|mars|farm)([[:space:]]|$))|rclone\.conf|/\.netrc|/\.aws/credentials|\.cloudflared/[^[:space:]]+\.json|\.pem([[:space:]]|$)|\.key([[:space:]]|$)|\.p12([[:space:]]|$)):::sops exec-env <file> '"'"'env | cut -d= -f1'"'"' で key 名のみ取得、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'
    # 2026-05-13 incident #22: Magi 2 agent が `grep KEY .env` 実行、 マッチ行全体が stdout に出て value 露出
    # 旧 comment 「grep -n KEY .env で line 番号のみ」 は誤、 grep default は match line 全文表示で value 焼く
    # → grep/egrep/fgrep/rg/awk/sed の credential file 直接 read も block、 ack-bypass 経路一本化
    '(^|[^a-zA-Z_/])(grep|egrep|fgrep|rg|awk|sed)[[:space:]]+[^|]*?(/\.env([[:space:]]|$|\.(common|prod|production|local|dev|staging|hetzner|laddie|chichibu|zetithnas|talisker|mars|farm)([[:space:]]|$))|rclone\.conf|/\.netrc|/\.aws/credentials|\.cloudflared/[^[:space:]]+\.json|\.pem([[:space:]]|$)|\.key([[:space:]]|$)|\.p12([[:space:]]|$)):::sops exec-env <file> '"'"'env | grep -c <KEY>'"'"' で件数のみ確認、 key 名 list は cut -d= -f1、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'
    # 2026-06-27 issue #36 (cross-family red-team, all Claude lenses missed):
    # 上記 cat/grep 統合 pattern は slash-prefixed path (/.env) と enumerated reader
    # (cat/head/grep/awk/...) 限定。 → `cat .env` / `grep KEY .env` (bare relative)、
    # `python3 -c 'open(".env").read()'` / node / ruby 等 non-enumerated reader が
    # すり抜け。 reader 非依存・basename ベースで credential file operand (.env / .env.*
    # / credentials.<ext>) を捕捉する補完層。 leading/trailing は path 境界 (空白・引用符
    # ・/・( ・= ・`<` redirection (codex #42: cat<.env 無空白 redirect) ・shell metachar)
    # に固定し、 `printenv` / `exec-env` / `environment.md`
    # / `.environment` / `echo "loading credentials"` 等 substring 'env'/'credentials'
    # の誤爆を回避 (literal '.env' = dot 必須、 credentials は拡張子必須)。
    '(^|[[:space:]/"(=;|&<'\''])(\.env(\.[A-Za-z0-9_-]+)*|credentials\.[A-Za-z0-9_-]+)([[:space:]/")><;|&'\'']|$):::reader 問わず credential file (.env / .env.* / credentials.<ext>) 直接読みは sops exec-env <file> で env 経由、 key 名のみは env | cut -d= -f1、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack,meta,tmpl'
    'sops[[:space:]]+exec-env[[:space:]].+['\''"].*[[:space:]]*(curl|wget|http|axios)[[:space:]]:::scripts/ に repo-baked script 置いて sops exec-env <file> <script-path> で呼ぶ'

    # === B 系 (#B1-B15) ===
    # 2026-05-31 issue #10: keyword-gated だと `printenv MARS_POSTGRES_URL` (KEY/TOKEN 等
    # を含まない secret var 名) や `printenv | grep -i postgres` がすり抜け。 L45 (env) と
    # 対称に target-agnostic 化 — printenv の任意 var print / 任意 filter pipe / bare dump を捕捉。
    'printenv([[:space:]]+[A-Za-z_][A-Za-z0-9_]*|[[:space:]]*\||[[:space:]]*$):::env | cut -d= -f1 で key 名のみ、値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'
    'echo[[:space:]].*\$\{?[A-Z_]*(TOKEN|PASSWORD|PASSWD|SECRET|CRED)[A-Z_]*\}?:::[ -n "\$X" ] && echo set で bool 確認'
    'printf.*\$\{?[A-Z_]*(TOKEN|PASSWORD|PASSWD|SECRET|CRED)[A-Z_]*\}?:::[ -n "\$X" ] && echo set で bool 確認'
    '(declare|typeset|export)[[:space:]]+-p[[:space:]]+[A-Z_]*(KEY|TOKEN|PASSWORD|PASSWD|SECRET|CRED)[A-Z_]*([[:space:]]|$):::env | cut -d= -f1 で key 名のみ取れる'
    # 2026-05-31 issue #10: set-pipe を任意 filter target に broaden (旧 grep|head|tail|awk|sed
    # 限定だと `set | rg postgres` 等がすり抜け)。 bare `set` 検出は `set -e`/`set -x` を誤爆
    # しないよう ($|;|\n) 終端を維持。
    '(^|;|&&|[[:space:]])set[[:space:]]*($|;|\n)|(^|;|&&|[[:space:]])set[[:space:]]*\|:::env | cut -d= -f1 で key 名のみ取れる (set は env+func 全 dump で過剰)'
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
    'rclone[[:space:]]+config[[:space:]]+(show|dump)([[:space:]]|$):::rclone listremotes で remote 名のみ、 field 一覧は rclone config show <remote> | awk -F= '"'"'/=/ {gsub(/ /,"",$1); print $1}'"'"'、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # preemptive #25: aws configure get/list で AWS SecretAccessKey 平文 stdout
    'aws[[:space:]]+configure[[:space:]]+(get|list)([[:space:]]|$):::aws configure list-profiles で profile 名のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # preemptive #26: gh auth token で GitHub PAT 平文 stdout
    'gh[[:space:]]+auth[[:space:]]+token([[:space:]]|$):::gh auth status で login 状態のみ確認、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # preemptive #27: gcloud auth print-(access|identity|refresh)-token で credential 平文 stdout
    'gcloud[[:space:]]+auth[[:space:]]+print-(access-token|identity-token|refresh-token)([[:space:]]|$):::gcloud auth list で account 名のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # preemptive #28: doctl auth list/token で DigitalOcean PAT 平文 stdout
    'doctl[[:space:]]+auth[[:space:]]+(list|token)([[:space:]]|$):::doctl account get で account 確認、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # preemptive #29: kubectl config view --raw で client-cert + bearer token 全 dump (--raw が sanitize 解除)
    'kubectl[[:space:]]+config[[:space:]]+view.*--raw:::kubectl config view (--raw 抜き) で sanitized 表示、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # preemptive #30: kubectl get secret -o yaml/json で base64-encoded secret 全 dump (= base64 -d で trivial 復元)
    'kubectl[[:space:]]+get[[:space:]]+secret.*-o[[:space:]]+(yaml|json):::kubectl get secret で metadata のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # preemptive #31: docker secret/config inspect で stored credential 露出
    'docker[[:space:]]+(secret|config)[[:space:]]+inspect([[:space:]]|$):::docker (secret|config) ls で ID のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # preemptive #32: flyctl auth token で Fly.io PAT 平文 stdout
    'flyctl[[:space:]]+auth[[:space:]]+token([[:space:]]|$):::flyctl auth whoami で login 状態のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # preemptive #33: vercel env pull で env 全 download to .env (file 経路だが downstream cat で leak risk 同等)
    'vercel[[:space:]]+env[[:space:]]+pull([[:space:]]|$):::vercel env ls で key 名のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass (file 経路でも downstream leak risk 同等):::ack'

    # preemptive #34: pass show / pass -c で stored credential 平文 stdout (-c は clipboard だが pipe で stdout 化可)
    'pass[[:space:]]+(show|-c)([[:space:]]|$):::pass ls で key list のみ、 値必要時は HRMTZ_ACK_CRED_READ=1 で意識的 bypass:::ack'

    # === D 系 (#35、 2026-05-31 DSN-with-creds-in-argv) ===
    # issue #6 / #8 incident 群: password 入り DSN URI を psql 等の argv に直書きすると
    # (a) process-listing (ps aux | grep) で argv の password が露出、 (b) command 自体が
    # 焼き付く。 PostScrub の DSN catalog (postgresql://user:pass@) と対称な prevent 層。
    # `psql "$POSTGRES_URL"` 等の env 展開形は command text に password literal を含まない
    # ので誤爆しない — literal な user:pass@ を書いた時だけ発火。
    '(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp|libsql)://[^:/@[:space:]]+:[^@[:space:]]+@:::password 入り DSN を argv に直書きせず、 sops exec-env <file> '"'"'psql "\$POSTGRES_URL" ...'"'"' で env 経由注入 (argv に password を出さない)'
)

VIOLATION_FOUND=0
VIOLATION_MSGS=""

# ack bypass (issue #36 REVISE, MED): command が `HRMTZ_ACK_CRED_READ=1 ...` で
# 始まる場合、 entry の flags に `ack` を明示した pattern のみ bypass する。 旧実装は
# reason prose に "HRMTZ_ACK_CRED_READ=1" の substring が出るかで判定していたため、
# 文言を変えると意図せず whitelist が増減する fragile coupling だった。 → flags で明示化。
ACK_BYPASS=0
if echo "$CMD" | grep -qE '^[[:space:]]*HRMTZ_ACK_CRED_READ=1[[:space:]]+'; then
    ACK_BYPASS=1
fi

# metadata allow (issue #36 REVISE, MED): basename-based cred-file pattern は
# operation 非依存なので `test -f .env` / `ls .env` / `git status -- .env` /
# `find . -name .env` / `stat .env`、 さらに literal を print するだけの
# `echo "loading .env"` / `printf "%s" .env` まで over-block する。 → 先頭が
# (optional `env [VAR=val]...` prefix 付き) non-reading verb で、 かつ read を
# chain しうる operator (pipe/;/&&/`/$(/-exec) を含まない時のみ、 flags に `meta`
# を持つ pattern を skip。 echo/printf は arg を print するだけで file 内容を読めない
# ので安全 (`echo $(cat .env)` は $( 除外で依然 block)。 read verb (cat/grep/
# python -c ...) や chained read (`ls .env && cat .env`) は依然 block (= 安全側)。
META_ALLOW=0
if echo "$DEOBF" | grep -qE '^[[:space:]]*(env[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*(test|\[|ls|find|stat|echo|printf|git[[:space:]]+status)([[:space:]]|$)' \
   && ! echo "$DEOBF" | grep -qE '[|;&`]|\$\(|-exec'; then
    META_ALLOW=1
fi

# template-file allow (FP regression from the #36 basename pattern, issue #47):
# .env.example / .sample / .template / .dist / .test / .local-example are credential
# TEMPLATES (no real secrets) — the Read-tool guard (credential_file_read_guard.sh)
# already exempts these suffixes. The #36 basename pattern over-blocks them for reader
# verbs (`cat .env.example`). Strip template-suffixed tokens (anchored at a token
# boundary so `.env.exampleXYZ` is NOT treated as a template) and re-test the basename
# pattern: exempt only if no real cred-file reference survives. Robust to mixed reads
# like `cat .env.example .env` — the real .env survives the strip and still blocks.
TEMPLATE_ALLOW=0
_CRED_BASENAME_RE='(^|[[:space:]/"(=;|&<'\''])(\.env(\.[A-Za-z0-9_-]+)*|credentials\.[A-Za-z0-9_-]+)([[:space:]/")><;|&'\'']|$)'
# terminator = the SAME boundary set the basename pattern uses; crucially it EXCLUDES
# '.', so a template token only counts when it is the FINAL filename segment. This
# blocks codex cross-family finding (issue #47): `.env.test.local` / `.env.example.bak`
# are NOT pure templates (real secrets possible) and must still block. `/` stays a
# terminator so `.env.example/.env` strips to `ENVTMPL/.env` whose inner real `.env`
# the re-test still catches.
if echo "$DEOBF" | grep -qE '\.env\.(example|sample|template|dist|test|local-example)([[:space:]/")><;|&'\'']|$)'; then
    _STRIPPED=$(echo "$DEOBF" | sed -E 's#\.env\.(example|sample|template|dist|test|local-example)([[:space:]/")><;|&'\'']|$)#ENVTMPL\2#g')
    if ! echo "$_STRIPPED" | grep -qE "$_CRED_BASENAME_RE"; then
        TEMPLATE_ALLOW=1
    fi
fi

for entry in "${PATTERNS_REASONS[@]}"; do
    pattern="${entry%%:::*}"
    rest="${entry#*:::}"
    reason="${rest%%:::*}"
    flags=""
    [ "$rest" != "$reason" ] && flags="${rest#*:::}"
    # Match against SCRUBBED (literal form) OR DEOBF (de-obfuscated form) so the
    # common token-construction bypasses surface without losing quote-dependent
    # patterns that only match the literal form.
    if echo "$SCRUBBED" | grep -qE "$pattern" || echo "$DEOBF" | grep -qE "$pattern"; then
        if [ "$ACK_BYPASS" -eq 1 ] && [[ ",$flags," == *",ack,"* ]]; then
            hook_log "bash_command_guard" "BYPASS via HRMTZ_ACK_CRED_READ=1 (ack flag) for cred-read pattern"
            continue
        fi
        if [ "$META_ALLOW" -eq 1 ] && [[ ",$flags," == *",meta,"* ]]; then
            hook_log "bash_command_guard" "ALLOW pure-metadata verb (meta flag) for cred-file pattern"
            continue
        fi
        if [ "$TEMPLATE_ALLOW" -eq 1 ] && [[ ",$flags," == *",tmpl,"* ]]; then
            hook_log "bash_command_guard" "ALLOW credential-template file (.env.example etc.) for cred-file pattern"
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
    # emit_deny picks the CLI-correct shape (Claude/Codex hookSpecificOutput vs
    # Grok {"decision":"deny"}) and exits 0.
    emit_deny "$MSG"
fi

exit 0
