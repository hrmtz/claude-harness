#!/usr/bin/env bash
# formation_suggest.sh — UserPromptSubmit hook
#
# Detects natural-language triggers in user prompts that imply a long-running
# peer-pane worker pattern, and injects a single keyword (`njslyr7`) into
# Claude's context as a hint to invoke the formation skill.
#
# Trigger keywords were derived from real user utterances mined via hippocampus
# semantic search across past sessions (2026-02 to 2026-05). Patterns confirmed
# 100% formation-context: "裏のお前", "裏のclaude", "裏でやって", etc.
#
# Modes:
#   FORMATION_SUGGEST_MODE=shadow  (default) — log match only, no inject
#   FORMATION_SUGGEST_MODE=active           — log + inject `njslyr7`
#
# Switch to active after 24h shadow run with no false-positive complaints.

set -uo pipefail

PROMPT="$(cat)"
MODE="${FORMATION_SUGGEST_MODE:-shadow}"
LOG="${FORMATION_SUGGEST_LOG:-$HOME/.local/log/formation_suggest.log}"
mkdir -p "$(dirname "$LOG")"

# High-confidence triggers (mined from real user prompts):
#   group 1: 裏の/他の/別の/違う/もう一人の + claude/おまえ/お前/キミ/君
#   group 2: 裏で + やる/走らせる
#   group 3: 並行/並列 + claude/task/やる/走らせ
#   group 4: 特定 host (chichibu-win 等) + claude
#   group 5: 別セッション/別 pane + claude/統合
#   group 6: 直接 keyword (njslyr7, formation skill, spawn worker)
TRIGGERS='(裏の|他の|別の|違う|もう一人の)(claude|おまえ|お前|キミ|君)|裏で(やっ|や[らり]|走らせ)|並[行列](で|して|に).*(claude|task|やる|走らせ)|chichibu-?win.*claude|別(セッション|pane).*(claude|統合)|njslyr7|formation skill|spawn.*worker'

if echo "$PROMPT" | grep -qiE "$TRIGGERS"; then
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  matched_phrase="$(echo "$PROMPT" | grep -oiE "$TRIGGERS" | head -1)"
  printf '%s\tmode=%s\tmatched=%q\tprompt_excerpt=%q\n' \
    "$ts" "$MODE" "$matched_phrase" "${PROMPT:0:120}" >> "$LOG"

  if [[ "$MODE" == "active" ]]; then
    # Single-keyword inject. njslyr7 is the unambiguous formation-context
    # token that user reaches for when they want a peer-pane worker. Claude
    # sees it as additional context and triggers the formation skill.
    echo "njslyr7"
  fi
fi

exit 0
