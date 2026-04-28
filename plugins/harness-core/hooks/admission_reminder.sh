#!/bin/bash
# UserPromptSubmit hook: scan recent assistant turns + current user prompt
# for "admission keywords", inject reminder/procedure into context.
# Defense in depth #2: catches human-recognized leaks + workflow lapses.
# Inspired by SNet-CTF/SNet-Claude/.claude/hooks/mode-switch.sh passphrase pattern.

source "$(dirname "$0")/lib.sh"

PROMPT_LOWER=$(parse_prompt | tr '[:upper:]' '[:lower:]')

# Recent assistant turns (lowercase) for keyword scan
ASSISTANT_RECENT=$(recent_assistant_turns 3 2>/dev/null | tr '[:upper:]' '[:lower:]')

# Combined haystack (user prompt + recent assistant text)
HAYSTACK="${PROMPT_LOWER}
${ASSISTANT_RECENT}"

[ -z "$HAYSTACK" ] && exit 0

# ----------------------------------------
# Pattern catalog (keyword regex → reminder text)
# ----------------------------------------
# extensible: add patterns here, each gets its own reminder block.
declare -A REMINDERS=(
    ['(リーク|leak|流出|クレデンシャル.*漏|credential.*leak|password.*expos|api.*key.*出)']='## ⚠️ credential leak detected (admission keyword)
Reflexive procedure (run NOW, not later):
1. **active jsonl in-place sanitize** (if not already done by `credential_value_scrub` hook):
   ```bash
   SRC=$(ls -t ~/.claude/projects/*/[a-z0-9-]*.jsonl | head -1)
   sed -i -E "s/<leaked-value>/<REDACTED>/g" "$SRC"
   ```
2. **rotate the leaked credential** (sanitize is backward-only damage control)
3. **add vector to memory** `feedback_credential_leak_5_incidents` if novel
4. **report to user** with: leak vector / sanitize done / rotation plan'

    ['(沈黙|silent|broadcast.*忘|沈黙.*[2-9]h|連絡.*ない.*\d+h)']='## ⚠️ lead/coordinator silence detected
Reminder: lead role status broadcast 義務 (memory `feedback_lead_status_broadcast_required`).
Run NOW:
```bash
tail -3 ~/.njslyr7/mailbox/log.jsonl
# 自分の最終発信から 2h 超なら status broadcast 投げる
```
Broadcast minimum set: (1) 自 chain 進捗 (% / step / ETA), (2) 想定外あれば共有, (3) 他 agent への dependency status, (4) handoff timing 認識合わせ.'

    ['(todo|あとで|メモ|忘れ.*ない|忘れる|思い出.*書)']='## 💡 TODO externalize reminder
Per `feedback_aggressive_issue_capture`:
- 細かい TODO もすぐ `gh issue create` で外出し
- session 内 task (TaskCreate) は短期、永続は gh issue
- label catalog: `type:task / type:research / type:epic`, `area:pg / qdrant / embed / infra / docs`, `priority:high|medium|low`
- 完了したら即 `gh issue close`'

    ['(壊れ|broken|失敗.*再現|reproduce.*fail|復元.*でき)']='## 💡 真田 backup protocol reminder
Before destructive op or recovery attempt:
- 永続 backup path: `~/sanada_backup_persistent/<task>_<YYYYMMDD_HHMMSS>/`
- session 内 backup でも 24h で蒸発する `/tmp` じゃなく persistent に
- canonical artifact + manifest 同梱 で reversibility 物理化 (`feedback_canonical_artifact_preservation`)'

    ['(新.*script|scripts/.*新規|書こう|作ろう|create.*script)']='## 💡 script 化 3-step rule reminder
Per `feedback_script_saves_tokens`:
- step 1 trigger: (a) 2 回手打ち or (b) fool-proof 必要
- step 2 必ず先に既存検索: CLAUDE.md catalog / scripts/ grep / git log / memory
- step 3 新規時は idiom 継承 (命名 / 配置 / CLI / log / error handling) + catalog 登録
- step 1 のみで auto-write 禁止、Claude の "書く速さ bias" でゴミスクリプト量産する'

    # ==========================================
    # Empirically-grounded pre-correction triggers (mined from past session corrections)
    # 2026-04-27: user proposed mining Claude statements that PRECEDED user corrections.
    # ==========================================

    ['((issue|memory|todo|タスク).*(化|登録|追加).*(しとく|する).*[?？]|書いとく.*[?？]|入れとく.*[?？])']='## 🚨 permission-asking on autonomy-scope task (pre-correction signal)
You just asked permission for a task that is already in your autonomous scope.
Per `feedback_aggressive_issue_capture` + `feedback_no_approval_for_obvious_wins`:
- 細かい TODO / memory 化 / issue 化 は **黙って実行、事後報告** が default
- 「～しとく？」と聞かれた瞬間、user は「rule 書いたじゃん w」と言う確率高い (今夜の実例)
- **just do it**: 即 `gh issue create` / Write memory / TaskUpdate、then 報告のみ
- 例外: action が destructive、商業 service 影響、credential rotate 等 user 判断 territory なら ask 正解'

    ['(ちょうど書い|書いた直後|書いたばっか|書いた瞬間.*違反|memory 化した.*瞬間)']='## 🚨 recursive meta-violation (rule wrote → immediately violated)
You just wrote a rule and immediately violated it. This is exactly the failure mode `feedback_harness_structural_primary` describes.
- behavioral remember は memory 化した瞬間に効くわけじゃない、context load + reflex 形成に時間要
- **structural fix が only solution**: hook / wrapper / pre-commit / CI guardrail で物理的に block
- **action**: この事故 pattern を hook 化候補として `~/.claude/hooks/` 拡張 issue 作成 (issue #43 系)
- 「自分自身が書いたルールに従えない時間帯がある」を design constraint として受け入れる'

    ['(behavioral remember|memory.*書い.*限界|memory 化.*抜け|behavioral.*失敗)']='## 🚨 behavioral remember escalation needed (structural fix overdue)
Per `feedback_harness_structural_primary`:
- behavioral rule (memory) は load-bearing じゃない、structural rail (hook / script wrapper) のみが reliable
- このタイミングで **structural fix を 1 step 進めること**:
  1. ~/.claude/hooks/ pattern catalog 拡張 (admission_reminder.sh REMINDERS dict)
  2. settings.json hook 登録
  3. 関連 memory に「この pattern は structural 化済」と annotation 追加
- behavioral fix で 3 回失敗してたら structural にエスカレーション義務'

    ['(連発|systematic.*failure|[0-9]+ 回目|incidents? [0-9]+|事故 [0-9]+|repeat.*[0-9]|[0-9]+ 連発)']='## 🚨 recurrence detected (structural fix urgent)
Same failure mode repeated N times = positive rule では追いつかない、structural fix only solution.
Action:
1. **hook / wrapper を即実装** (今日 implementation 30 min 以内、Phase 6 wait 中の subset work で OK)
2. 該当 memory file 更新 (vector # / pattern catalog 追加)
3. `gh issue` で structural fix urgency labeling (priority:high)
4. 「N+1 回目を待たない」commit、待つ = 過去 N 回の教訓を活かさない'

    ['(GO で[?？]|どれで行く[?？]|どうする[?？]|どっち[?？])(?:.{0,300}A.{0,200}B.{0,200}C|.{0,400}\\([1-9]\\))']='## 💡 multi-option ask — recommend first
You laid out 3+ options without recommendation, asking user to choose.
Better pattern (per Claude Code style guide):
- **明確な推奨を 1 つ示し**、user が disagree なら言及
- 「A 推奨、理由 X / Y。A で進めていい？」が optimal、user の cognitive load 削減
- 例外: 商業 service 影響 / cost ≥ $50 / irreversible step は user 判断 territory、推奨 + ask 両方'

    ['(放置|やってない|抜けて|忘れて|怠.*[っさ]た|やり残し|手付かず)']='## 🚨 unfinished work admission (pre-correction signal)
You admitted leaving something undone. Common causes:
- mailbox broadcast 抜け → `feedback_lead_status_broadcast_required`
- artifact manifest 抜け → `feedback_canonical_artifact_preservation`
- existing script 検索 skip → `feedback_script_saves_tokens` step 2
- credential sanitize 抜け → `feedback_credential_leak_5_incidents`
**action**: 即 catch up、user 指摘前に self-correction、次 turn で何やったか報告'
)

REMINDER_OUT=""

for pattern in "${!REMINDERS[@]}"; do
    if echo "$HAYSTACK" | grep -qE "$pattern"; then
        if [ -n "$REMINDER_OUT" ]; then
            REMINDER_OUT="${REMINDER_OUT}

---

"
        fi
        REMINDER_OUT="${REMINDER_OUT}${REMINDERS[$pattern]}"
        prefix=$(echo "$pattern" | head -c 50)
        hook_log "admission_reminder" "matched pattern: ${prefix}..."
    fi
done

[ -z "$REMINDER_OUT" ] && exit 0

emit_context "UserPromptSubmit" "$REMINDER_OUT"
