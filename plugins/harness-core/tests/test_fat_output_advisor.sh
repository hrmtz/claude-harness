#!/bin/bash
# fat_output_advisor.sh (claude-harness#218 変更 3) の単体テスト。
# advisory 発火 (ADVISE) / 非発火 (SILENT) / session cooldown を検証。
# Run: bash plugins/harness-core/tests/test_fat_output_advisor.sh
set -u
HOOK="$(cd "$(dirname "$0")/../hooks" && pwd)/fat_output_advisor.sh"
export XDG_RUNTIME_DIR="$(mktemp -d)"
trap 'rm -rf "$XDG_RUNTIME_DIR"' EXIT
PASS=0; FAIL=0; N=0

run() { # $1=session $2=cmd
  printf '%s' "{\"session_id\":\"$1\",\"tool_input\":{\"command\":$(printf '%s' "$2" | jq -Rs .)}}" \
    | bash "$HOOK" 2>/dev/null
}
advises() { run "$1" "$2" | grep -q 'additionalContext'; }
check() { # $1=ADVISE|SILENT $2=cmd
  N=$((N+1)); local sess="t$N"
  if advises "$sess" "$2"; then r=ADVISE; else r=SILENT; fi
  if [ "$r" = "$1" ]; then PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s %s\n' "$r" "$2"
  else FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m expected %s got %s : %s\n' "$1" "$r" "$2"; fi; }

echo "== ADVISE: unbounded fat-output commands =="
check ADVISE "gh pr diff 229"
check ADVISE "gh pr diff"
check ADVISE "gh issue list"
check ADVISE "gh pr list --state open"
check ADVISE "git log"
check ADVISE "git log --stat dev..main"
check ADVISE "formation status"

echo "== SILENT: already narrowed =="
check SILENT "gh pr diff 229 --name-only"
check SILENT "gh pr diff 229 | head -100"
check SILENT "gh issue list --limit 10"
check SILENT "gh issue list -L 5"
check SILENT "gh pr list -L20"
check SILENT "git log -n 5"
check SILENT "git log -n5"
check SILENT "git log --oneline -3"
check SILENT "git log --max-count=10"
check SILENT "git log | head -20"
check SILENT "formation status | grep worker-2"

echo "== SILENT: unrelated commands (over-warn しない) =="
check SILENT "ls -la"
check SILENT "gh pr view 229"
check SILENT "git logs-helper.sh"
check SILENT "echo formation statuses"

echo "== cooldown: 同一 session 同一 pattern は 1 回だけ =="
N=$((N+1))
if advises "cool1" "gh pr diff 1" && ! advises "cool1" "gh pr diff 2"; then
  PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m 2nd gh_pr_diff in same session suppressed\n'
else
  FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m cooldown not enforced\n'; fi
N=$((N+1))
if advises "cool1" "git log"; then
  PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m different pattern same session still advises\n'
else
  FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m different pattern wrongly suppressed\n'; fi
N=$((N+1))
if advises "cool2" "gh pr diff 3"; then
  PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m same pattern new session advises\n'
else
  FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m new session wrongly suppressed\n'; fi

echo "== fail-open: broken payload =="
N=$((N+1))
out=$(printf 'not json' | bash "$HOOK" 2>/dev/null); rc=$?
if [ $rc -eq 0 ] && [ -z "$out" ]; then
  PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m broken payload exits 0 silently\n'
else
  FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m broken payload rc=%s out=%s\n' "$rc" "$out"; fi

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
