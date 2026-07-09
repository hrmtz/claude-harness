#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

set +e
HOME="$TMP/home" bash "$ROOT/scripts/backup_jsonl_to_r2.sh" >/tmp/backup_jsonl_stdout.$$ 2>/tmp/backup_jsonl_stderr.$$
rc=$?
set -e

if [ "$rc" -ne 2 ]; then
  echo "FAIL: expected rc=2 for missing backup config, got rc=$rc" >&2
  exit 1
fi
if ! grep -q "HARNESS_JSONL_BACKUP_DEST" /tmp/backup_jsonl_stderr.$$; then
  echo "FAIL: missing config error did not mention HARNESS_JSONL_BACKUP_DEST" >&2
  exit 1
fi
rm -f /tmp/backup_jsonl_stdout.$$ /tmp/backup_jsonl_stderr.$$
echo "backup_jsonl config: PASS"
