#!/usr/bin/env bash
# Regression: retention shares the writer lock and preserves seq high-water.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE="$(mktemp -d)"
cleanup() {
  [[ -n "${RETENTION_PID:-}" ]] && kill "$RETENTION_PID" 2>/dev/null || true
  rm -rf "$FIXTURE"
}
trap cleanup EXIT

MAILBOX="$FIXTURE/mailbox/log.jsonl"
FAKE_BIN="$FIXTURE/bin"
mkdir -p "$(dirname "$MAILBOX")" "$FAKE_BIN"
printf '%s\n' '#!/usr/bin/env bash' 'exit 1' > "$FAKE_BIN/pgrep"
chmod +x "$FAKE_BIN/pgrep"

append() {
  FORMATION_MAILBOX="$MAILBOX" PATH="$FAKE_BIN:/usr/bin:/bin" \
    bash -c 'source "$1"; mailbox_append fixture pane-1 "$2"' \
      _ "$HERE/../lib/mailbox.sh" "$1"
}

first="$(append one)"
second="$(append two)"
[[ "$first" -eq 1 && "$second" -eq 2 ]]

# Retention must wait on the exact lock used by mailbox_append. This prevents
# reading the old inode while a writer appends immediately before os.replace.
exec 9>"$MAILBOX.lock"
flock -x 9
FORMATION_MAILBOX="$MAILBOX" PATH="$FAKE_BIN:/usr/bin:/bin" \
  python3 "$HERE/../bin/mailbox-retention" --keep-days 0 --keep-tail 0 \
    >"$FIXTURE/retention.out" 2>&1 &
RETENTION_PID=$!
sleep 0.15
kill -0 "$RETENTION_PID"
flock -u 9
exec 9>&-
wait "$RETENTION_PID"
unset RETENTION_PID

[[ ! -s "$MAILBOX" ]]
[[ "$(cat "$MAILBOX.seq")" -eq 2 ]]
grep -Fq '2 lines -> keep 0, drop 2' "$FIXTURE/retention.out"
find "$(dirname "$MAILBOX")/archive" -name 'log_pretrim_*.jsonl.gz' -print -quit |
  grep -q .

# An empty retained log must not restart seq at 1 while cursors still point
# beyond it. The separate high-water file makes the next row monotonic.
third="$(append three)"
[[ "$third" -eq 3 ]]
jq -e '.seq == 3 and .body == "three"' "$MAILBOX" >/dev/null

echo "test_mailbox_retention: PASS"
