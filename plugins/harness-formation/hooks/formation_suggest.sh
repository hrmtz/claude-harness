#!/usr/bin/env bash
# formation_suggest.sh — UserPromptSubmit hook
#
# Detects natural-language triggers in user prompts that imply a long-running
# peer-pane worker pattern, and injects a single keyword (`formation skill`) into
# Claude's context as a hint to invoke the formation skill.
#
# Trigger keywords are high-confidence worker-spawn phrasing such as
# "裏のお前", "裏のclaude", "裏でやって", etc.
#
# Modes:
#   FORMATION_SUGGEST_MODE=shadow  (default) — log match only, no inject
#   FORMATION_SUGGEST_MODE=active           — log + inject `formation skill`
#
# Switch to active after 24h shadow run with no false-positive complaints.

set -uo pipefail

source "$(dirname "$0")/../../harness-core/hooks/lib.sh"

HOOK_INPUT=$(cat)
export HOOK_INPUT

PROMPT="$(parse_prompt)"
[ -z "$PROMPT" ] && PROMPT="$HOOK_INPUT"
MODE="${FORMATION_SUGGEST_MODE:-shadow}"
LOG="${FORMATION_SUGGEST_LOG:-$HOME/.local/log/formation_suggest.log}"
mkdir -p "$(dirname "$LOG")"

# High-confidence triggers (mined from real user prompts):
#   group 1: 裏の/他の/別の/違う/もう一人の + claude/おまえ/お前/キミ/君
#   group 2: 裏で + やる/走らせる
#   group 3: 並行/並列 + claude/task/やる/走らせ
#   group 4: 別セッション/別 pane + claude/統合
#   group 5: direct keyword (formation skill / spawn worker)
TRIGGERS='(裏の|他の|別の|違う|もう一人の)(claude|おまえ|お前|キミ|君)|裏で(やっ|や[らり]|走らせ)|並[行列](で|して|に).*(claude|task|やる|走らせ)|別(セッション|pane).*(claude|統合)|formation skill|spawn.*worker'

if echo "$PROMPT" | grep -qiE "$TRIGGERS"; then
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  matched_phrase="$(echo "$PROMPT" | grep -oiE "$TRIGGERS" | head -1)"
  printf '%s\tmode=%s\tmatched=%q\tprompt_excerpt=%q\n' \
    "$ts" "$MODE" "$matched_phrase" "${PROMPT:0:120}" >> "$LOG"

  if [[ "$MODE" == "active" ]]; then
    # Single-keyword inject. Claude sees it as additional context and triggers
    # the formation skill.
    emit_context "UserPromptSubmit" "formation skill"
  fi
fi

exit 0
