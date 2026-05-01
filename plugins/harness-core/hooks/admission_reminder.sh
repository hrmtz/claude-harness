#!/bin/bash
# UserPromptSubmit hook: scan user prompt for admission keywords,
# inject terse retreat-counter reminder.
#
# Design: HOOK_OUTPUT_DESIGN.md (silent on success, terse on failure,
# polarity retreat-counter, top-1 match only).
#
# 2026-05-01 audit:
# - Removed assistant-turn scan (was self-amplifying loop source)
# - Top-1 match only (was stacking 3-5 reminders, compounding context contamination)
# - Each reminder: action only, polarity-flipped close, no admonishment markers

source "$(dirname "$0")/lib.sh"

PROMPT_LOWER=$(parse_prompt | tr '[:upper:]' '[:lower:]')

[ -z "$PROMPT_LOWER" ] && exit 0

# ----------------------------------------
# Pattern catalog (regex → terse reminder)
# ----------------------------------------
# Format: action lines + retreat-counter closing line.
# All prose stays generation-context-safe (no "blocked", "violation",
# "denied", emoji warnings, ALL-CAPS, or memory-file references).
declare -A REMINDERS=(
    ['(リーク|leak|流出|クレデンシャル.*漏|credential.*leak|password.*expos|api.*key.*出)']='Sanitize active jsonl + rotate the credential.
ls -t ~/.claude/projects/*/[a-z0-9-]*.jsonl | head -1 → sed -i '"'"'s/<value>/<REDACTED>/g'"'"'.
早期発見できた、立て直せる。'

    ['(沈黙|silent|broadcast.*忘|連絡.*ない.*\d+h)']='Mailbox tail + 4-item broadcast (進捗 / 想定外 / 依存 / handoff).
tail -3 ~/.njslyr7/mailbox/log.jsonl で起点確認、自分の最終発信から 2h 超なら post.
声出した方が場が動く。'

    ['(todo|あとで|メモ|忘れ.*ない|忘れる|思い出.*書)']='gh issue create で外出し、頭から下ろす。
スッキリした方が次の手が出る。'

    ['(壊れ|broken|失敗.*再現|reproduce.*fail|復元.*でき)']='Persistent backup path: ~/sanada_backup_persistent/<task>_<YYYYMMDD_HHMMSS>/.
こんなこともあろうかと言える状態にしとけ、それで十分。'

    ['(新.*script|scripts/.*新規|書こう|作ろう|create.*script)']='既存検索先: catalog grep / scripts/ grep / git log / memory grep.
既存 idiom (命名 / CLI / log) 継承で書き始める方が早い。'

    ['((issue|memory|todo|タスク).*(化|登録|追加).*(しとく|する).*[?？]|書いとく.*[?？]|入れとく.*[?？])']='Autonomy-scope task は黙ってやって事後報告で OK.
Just do it、ask 不要。'

    ['(ちょうど書い|書いた直後|書いたばっか|書いた瞬間.*違反|memory 化した.*瞬間)']='Hook / wrapper 化で structural rail に上げる.
Memory 暗記より構造で押さえる方が確実。'

    ['(behavioral remember|memory.*書い.*限界|memory 化.*抜け|behavioral.*失敗)']='Structural rail へ昇格 (hook / wrapper / pre-commit / CI guardrail).
3 回失敗したら次は構造で潰す、それで上達する。'

    ['(連発|systematic.*failure|[0-9]+ 回目|incidents? [0-9]+|事故 [0-9]+|repeat.*[0-9]|[0-9]+ 連発)']='Hook / wrapper を即実装 (≤ 30 min)、N+1 回目を待たない.
100 回叩く前に構造で先回り。'

    ['(GO で[?？]|どれで行く[?？]|どうする[?？]|どっち[?？])(.{0,300}A.{0,200}B.{0,200}C|.{0,400}\([1-9]\))']='1 つ推奨で出す方が user 楽.
「A 推奨、理由 X、A で進めていい?」が optimal。'

    ['(放置|やってない|抜けて|忘れて|怠.*[っさ]た|やり残し|手付かず)']='即 catch up + 報告で先回り.
User 指摘待つよりこっちが速い、それで信頼が積み上がる。'
)

# Match top-1 only (no stacking — multiple reminders compound context contamination).
REMINDER_OUT=""

for pattern in "${!REMINDERS[@]}"; do
    if echo "$PROMPT_LOWER" | grep -qE "$pattern"; then
        REMINDER_OUT="${REMINDERS[$pattern]}"
        prefix=$(echo "$pattern" | head -c 50)
        hook_log "admission_reminder" "matched pattern: ${prefix}..."
        break
    fi
done

[ -z "$REMINDER_OUT" ] && exit 0

emit_context "UserPromptSubmit" "$REMINDER_OUT"
