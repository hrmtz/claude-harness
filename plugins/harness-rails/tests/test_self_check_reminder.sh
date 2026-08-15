#!/usr/bin/env bash
set -euo pipefail

HOOK="$(cd "$(dirname "$0")/../hooks" && pwd)/self_check_reminder.sh"
failures=0

run_hook() {
  printf '%s' "$1" | bash "$HOOK"
}

assert_empty() {
  local label=$1 payload=$2 output
  output=$(run_hook "$payload")
  if [[ -n "$output" ]]; then
    echo "FAIL: $label expected silence" >&2
    failures=$((failures + 1))
  fi
}

assert_contains() {
  local label=$1 payload=$2 needle=$3 output
  output=$(run_hook "$payload")
  if ! jq -e --arg needle "$needle" '.hookSpecificOutput.additionalContext | contains($needle)' <<<"$output" >/dev/null; then
    echo "FAIL: $label missing: $needle" >&2
    failures=$((failures + 1))
  fi
}

assert_not_contains() {
  local label=$1 payload=$2 needle=$3 output
  output=$(run_hook "$payload")
  if jq -e --arg needle "$needle" '.hookSpecificOutput.additionalContext | contains($needle)' <<<"$output" >/dev/null; then
    echo "FAIL: $label unexpectedly contained: $needle" >&2
    failures=$((failures + 1))
  fi
}

assert_empty "foreground" '{"tool_input":{"run_in_background":false,"command":"CREATE INDEX idx ON t(id)"}}'
assert_empty "unmatched background" '{"tool_input":{"run_in_background":true,"command":"echo done"}}'
assert_empty "self-check recursion" '{"tool_input":{"run_in_background":true,"command":"sleep 300; check_status"}}'

docker_payload='{"tool_input":{"run_in_background":true,"command":"docker build ."}}'
assert_contains "monitor-only" "$docker_payload" "5 min 後 self-check"
assert_not_contains "monitor-only" "$docker_payload" "次工程を下見"

index_payload='{"tool_input":{"run_in_background":true,"command":"psql -c CREATE INDEX idx ON chunks(id)"}}'
assert_contains "long wait monitor" "$index_payload" "10 min 後 self-check"
assert_contains "long wait recon" "$index_payload" "推定待ち時間 >=30分"
assert_contains "long wait branch check" "$index_payload" "実際の分岐 / 固定値 / テストの lockstep"
assert_contains "long wait no opportunistic fix" "$index_payload" "その場で直さず"

camel_payload='{"toolInput":{"runInBackground":true,"command":"rclone sync src dst"}}'
assert_contains "cross-cli camelCase" "$camel_payload" "次工程を下見"

snake_camel_payload='{"tool_input":{"runInBackground":true,"command":"python scripts/ingest_papers.py --batch"}}'
assert_contains "snake envelope camelCase" "$snake_camel_payload" "次工程を下見"

assert_contains "embed path" '{"tool_input":{"run_in_background":true,"command":"bash scripts/embed_chunks.sh"}}' "次工程を下見"
assert_contains "export path" '{"tool_input":{"run_in_background":true,"command":"nohup ./run_export.sh"}}' "次工程を下見"
assert_contains "module token" '{"tool_input":{"run_in_background":true,"command":"python -m prs_ingest"}}' "次工程を下見"
assert_contains "quoted sops job path" '{"tool_input":{"run_in_background":true,"command":"sops exec-env secrets.enc.yaml '\''python scripts/export_db.py'\''"}}' "次工程を下見"

shell_export_payload='{"tool_input":{"run_in_background":true,"command":"nohup bash -c export CUDA=0; sleep 10"}}'
assert_contains "shell export still monitored" "$shell_export_payload" "5 min 後 self-check"
assert_not_contains "shell export is not job recon" "$shell_export_payload" "次工程を下見"

assert_not_contains "terraform is monitor-only" '{"tool_input":{"run_in_background":true,"command":"terraform apply"}}' "次工程を下見"

if (( failures > 0 )); then
  exit 1
fi
echo "self_check_reminder: PASS"
