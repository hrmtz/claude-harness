#!/usr/bin/env bash
# long_task_advisor.sh — Bash 実行直前に「これ長 task では?」を検知して
# wrapper 経由 (= ~/.claude/bin/long-task.sh) を Claude に推奨。
#
# 既 wrapper 経由なら何もしない (= silent)。
# 既知 long task pattern にだけ反応する (= over-warn しない)。
#
# input: stdin に Claude Code から PreToolUse hook payload (JSON)。
#        .tool_input.command に bash command 文字列。
# output: hookSpecificOutput.additionalContext で Claude に注入 reminder。

set -euo pipefail

payload=$(head -c 65536 || true)
# Cross-CLI: Claude/Codex .tool_input.command // Grok .toolInput.command.
cmd=$(echo "$payload" | jq -r '.tool_input.command // .toolInput.command // ""' 2>/dev/null || echo "")

if [[ -z "$cmd" ]]; then exit 0; fi
if echo "$cmd" | grep -qE 'long-task\.sh|long_task\.sh'; then exit 0; fi

# 既知 long-running pattern (= 数分〜数時間 想定)
LONG_RE='docker compose up|docker build|wrangler deploy|wrangler pages deploy|pnpm (migrate|deploy)|npx prisma migrate|node .*scripts/(import-|migrate-|batch-|build-r2|enrich-).*\.mjs|node .*\.mjs.*--limit [0-9]{3,}|rclone copy|aws s3 sync|wrangler r2 object'

if ! echo "$cmd" | grep -qE "$LONG_RE"; then
	exit 0
fi

MSG="⏱ long-task pattern detected. Prefer a supervised long-task wrapper with dry-run, bounded polling, and anomaly checks. Example: \`~/.claude/bin/long-task.sh --dry-arg --dry --limit-arg --limit -- <cmd> [args]\`. For background work use \`--background\`; emergency bypass only with \`--skip-dry\`."

jq -n --arg msg "$MSG" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    additionalContext: $msg
  }
}'
