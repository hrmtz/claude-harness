#!/usr/bin/env bash
# fat_output_advisor.sh — 肥大 tool 出力を生む Bash command を実行直前に検知して
# 絞り方を additionalContext で提案する (= advisory only、deny しない)。
#
# claude-harness#218: tool_result 流入の 76% が Bash 出力。実測の常連 offender
# (gh pr diff 全件 / gh issue list 全件 / git log 無制限 / formation status 全 pane)
# にだけ反応する (= over-warn しない)。long_task_advisor.sh と同型。
#
# cooldown: 同一 pattern への提案は session あたり 1 回 (= 連発 noise 防止)。
# state: ${XDG_RUNTIME_DIR:-/tmp}/claude-fat-advisor/<session_id>
#
# input: stdin に PreToolUse hook payload (JSON)。
# output: hookSpecificOutput.additionalContext で Claude に注入 reminder。
# fail-open: 判定不能は無言 exit 0。

set -uo pipefail

payload=$(head -c 65536 || true)
# Cross-CLI: Claude/Codex .tool_input.command // Grok .toolInput.command.
cmd=$(echo "$payload" | jq -r '.tool_input.command // .toolInput.command // ""' 2>/dev/null || echo "")
[[ -z "$cmd" ]] && exit 0

session=$(echo "$payload" | jq -r '.session_id // ""' 2>/dev/null || echo "")
[[ -z "$session" ]] && session="ppid-$PPID"
state_dir="${XDG_RUNTIME_DIR:-/tmp}/claude-fat-advisor"
state_file="$state_dir/${session//[^a-zA-Z0-9_-]/_}"

pattern="" advice=""

if echo "$cmd" | grep -qE '\bgh pr diff\b'; then
    # 全 file 全 hunk が一括で返る。絞り済み (pipe / --name-only) は対象外
    if ! echo "$cmd" | grep -qE '\-\-name-only|\|'; then
        pattern="gh_pr_diff"
        advice="\`gh pr diff\` は全 file 全 hunk を一括で返す (実測 5-14k chars)。まず \`--name-only\` で file 一覧を取り、対象 file だけ \`| grep -A/-B\` や \`| head -N\` で絞るか、必要 file を直接 Read する方が context 効率が良い。"
    fi
elif echo "$cmd" | grep -qE '\bgh (issue|pr) list\b'; then
    if ! echo "$cmd" | grep -qE '\-L[[:space:]=]?[0-9]|--limit'; then
        pattern="gh_list"
        advice="\`gh issue list\` / \`gh pr list\` は default 30 件返す。目的の件数が分かっているなら \`--limit N\` (または \`-L N\`) を付けて絞ると context 効率が良い。--search / --label / --state での絞り込みも有効。"
    fi
elif echo "$cmd" | grep -qE '\bgit log\b'; then
    # -n 5 / -n5 / --max-count / -5 いずれか、または pipe 済みなら対象外
    if ! echo "$cmd" | grep -qE '\-n[[:space:]=]?[0-9]|--max-count|[[:space:]]-[0-9]+|\|'; then
        pattern="git_log"
        advice="\`git log\` は無制限に返す。\`-n N\` (例 \`-n 10\`) や \`--oneline -N\` を付けて必要件数だけ取ると context 効率が良い。"
    fi
elif echo "$cmd" | grep -qE '\bformation status\b'; then
    if ! echo "$cmd" | grep -qE '\|'; then
        pattern="formation_status"
        advice="\`formation status\` は全 worker 全行を返す。特定 pane だけ見たいなら \`| grep <worker-id>\` 等で絞ると context 効率が良い。"
    fi
fi

[[ -z "$pattern" ]] && exit 0

# cooldown: session あたり同一 pattern 1 回
mkdir -p "$state_dir" 2>/dev/null || exit 0
grep -qxF "$pattern" "$state_file" 2>/dev/null && exit 0
echo "$pattern" >> "$state_file" 2>/dev/null || true

jq -n --arg msg "📏 fat-output advisory: $advice (advisory only、このまま実行しても良い。#218 token 削減)" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    additionalContext: $msg
  }
}' 2>/dev/null || exit 0
