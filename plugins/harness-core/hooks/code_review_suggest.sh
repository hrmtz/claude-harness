#!/bin/bash
# code_review_suggest.sh — UserPromptSubmit hook
#
# Detects session-end / commit-push intent and injects a /code-review suggestion.
# Design: suggest only (not block). One terse context injection on keyword match.
#
# Triggers: セッション終了 / お疲れ様 / 一段落 / commit push / push / 今日はここまで
# Output: one-line suggestion via emit_context

source "$(dirname "$0")/lib.sh"

HOOK_INPUT=$(cat)
export HOOK_INPUT

PROMPT_LOWER=$(parse_prompt | tr '[:upper:]' '[:lower:]')
[ -z "$PROMPT_LOWER" ] && exit 0

# Session-end or commit-push keywords
if echo "$PROMPT_LOWER" | grep -qiE \
  'セッション終了|お疲れ様|一段落|commit.*push|push.*commit|今日はここまで|これで終わり|終了します|お疲れ|wrap.*up|end.*session'; then
  # Check if there are uncommitted changes or commits ahead of origin — only suggest if there's something to review
  DIFF_STAT=$(cd "${PWD}" 2>/dev/null && git diff main...HEAD --stat 2>/dev/null | tail -1)
  if [ -n "$DIFF_STAT" ]; then
    emit_context "UserPromptSubmit" "変更がある場合は /code-review を実行して commit 前に確認。
$(echo "$DIFF_STAT" | sed 's/^/  /')"
  fi
fi

exit 0
