#!/bin/bash
# SessionStart hook: temporal anchor を出力 (= hallucination 防止 structural rail).
#
# 動機: 5/13 session で claude が mafutsu (= 1 ヶ月 product) を 「半年来」 と
# 2 回 fabricate、 textbook layer (= 5 日前 ingest) を 「半年投資」 と誤表現。
# memory feedback_temporal_claims_require_grounding を本人が焼いた上で破った。
# session 開始時に絶対座標 (= today / project age 等) を context に injectし、
# 時系列 claim を 「感覚」 でなく ground-truth に anchor させる。
#
# 出力は SessionStart additionalContext 経由で 1 度だけ session 頭に表示。

set -uo pipefail

TODAY=$(date '+%Y-%m-%d')
TODAY_TS=$(date +%s)

# cwd 経由で project root 推定 (= claude code 起動時の cwd を参照)
PROJ_ROOT=""
for candidate in "$PWD" "$HOME/projects/PRS-LLM-dev" "$HOME/projects/PRS-LLM"; do
    if [ -d "$candidate/.git" ]; then
        PROJ_ROOT="$candidate"
        break
    fi
done

if [ -z "$PROJ_ROOT" ]; then
    # not in known project、 silent skip (= 他 project 環境で noise 出さない)
    exit 0
fi

# project 初 commit / last commit
FIRST_COMMIT=$(cd "$PROJ_ROOT" && git log --reverse --format=%ai 2>/dev/null | head -1 | awk '{print $1}')
LAST_COMMIT=$(cd "$PROJ_ROOT" && git log -1 --format=%ai 2>/dev/null | awk '{print $1}')

# age 日数
AGE_DAYS=""
if [ -n "$FIRST_COMMIT" ]; then
    FIRST_TS=$(date -d "$FIRST_COMMIT" +%s 2>/dev/null || echo 0)
    if [ "$FIRST_TS" -gt 0 ]; then
        AGE_DAYS=$(( (TODAY_TS - FIRST_TS) / 86400 ))
    fi
fi

# 直近 1 週間 commit 数 (= 活動量参考)
RECENT_COMMITS=$(cd "$PROJ_ROOT" && git log --since "1 week ago" --oneline 2>/dev/null | wc -l)

# 重要 memory file の最終更新 (= 「最近 X した」 の verification 用)
MEM_DIR="$HOME/.claude/projects/-home-hrmtz-projects-PRS-LLM-dev/memory"
RECENT_MEM=""
if [ -d "$MEM_DIR" ]; then
    RECENT_MEM=$(ls -t "$MEM_DIR"/feedback_*.md 2>/dev/null | head -3 | while read f; do
        basename=$(basename "$f" .md)
        mtime=$(stat -c %y "$f" 2>/dev/null | awk '{print $1}')
        echo "  - ${basename} (${mtime})"
    done)
fi

# output JSON for SessionStart additionalContext
cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "## 📅 temporal anchor — hallucination 防止 structural rail\n\n- **TODAY**: ${TODAY}\n- **project root**: ${PROJ_ROOT}\n- **mafutsu age**: ${AGE_DAYS:-?} days (= 初 commit ${FIRST_COMMIT:-?})\n- **last commit**: ${LAST_COMMIT:-?}\n- **last 7d activity**: ${RECENT_COMMITS} commits\n- **recent memory files**:\n${RECENT_MEM}\n\n**時系列 claim (= 「N 日前」 「半年来」 「1 年前」 「最近」 等) は必ず ground-truth (= git log / memory mtime / file ctime / 上記 anchor) で verify、 感覚 fabrication 禁止 (= memory feedback_temporal_claims_require_grounding)**\n\n禁止語彙 default (= verification 通過しない限り使うな):\n- 「半年来」「半年がかり」「1 年来」「久しぶり」「ここ最近」 等の漠然 expression\n- 「N 日前」 等の具体数字も memory / git log での裏付け必須\n"
  }
}
EOF
