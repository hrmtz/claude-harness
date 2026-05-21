#!/usr/bin/env bash
# self_check_reminder.sh — PostToolUse Bash hook
# Bash を run_in_background=true で kick した時、 self-check を schedule する
# よう reminder を additionalContext に注入。
#
# Trigger: tool_input.run_in_background == true AND command が long-running 兆候
# (nohup / vastai / embed / export / build / rclone / psql COPY 等)
#
# Skip: command 自体が self-check (= sleep N; check pattern) なら no-op
#
# Why: feedback_self_check_inflight_workers + feedback_early_bug_check の 5 min
# early-check rule を behavioral でなく structural 層に刻む (= 新機能化)。
# Memory に書いただけだと session 跨ぎで drift する (2026-05-21 regression 確認)。

set -euo pipefail

payload=$(head -c 65536 || true)
is_bg=$(echo "$payload" | jq -r '.tool_input.run_in_background // false' 2>/dev/null || echo "false")
cmd=$(echo "$payload" | jq -r '.tool_input.command // ""' 2>/dev/null || echo "")

# Not a background Bash → exit silent
[[ "$is_bg" == "true" ]] || exit 0
[[ -n "$cmd" ]] || exit 0

# This very command IS a self-check (sleep N; then poll, or sleep N\n... newline-sep)
# Match: starts with `sleep [0-9]+` followed by space/semicolon/newline (= self-check pattern)
# Newline-separated multi-cmd is the common Bash heredoc style for orch self-check
if echo "$cmd" | head -c 200 | grep -qE '^sleep [0-9]+($| |;|&&)|self.check|self_check'; then
  exit 0
fi

# Long-running signature (= 5+ min walltime tendency)
LONG_RE='nohup|setsid|vastai|FlagEmbedding|model\.encode|--device cuda|psql.*COPY|rclone (copy|sync)|docker (build|push|pull)|gh workflow|terraform apply|pg_dump|pg_restore|pg_basebackup|CREATE INDEX|REINDEX|VACUUM FULL|ANALYZE.*paper_chunks|ssh.*bash.*\.sh|scp .* -P|build-r2|ingest|embed|export'

if ! echo "$cmd" | grep -qE "$LONG_RE"; then
  exit 0
fi

# Suggest follow-up self-check window. 5 min default for embed/export, longer for index/REINDEX.
window=5
if echo "$cmd" | grep -qiE 'CREATE INDEX|REINDEX|VACUUM FULL|pg_dump|pg_basebackup'; then
  window=10
fi

cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "⏱ long-running bg Bash kicked (匹配 long-running pattern). user の ack を待つな、 ${window} min 後 self-check を schedule して self-report しろ: \`Bash run_in_background: true, command: sleep $((window*60)); <status query here>\`。 task notification 来たら結果を user に proactive report。 Per feedback_self_check_inflight_workers + feedback_early_bug_check。"
  }
}
EOF
