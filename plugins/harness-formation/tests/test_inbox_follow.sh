#!/usr/bin/env bash
# Regression tests for `formation inbox --follow` (gh #282 idle-wake rail).

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/../bin/formation"
PASS=0
FAIL=0
ok()  { PASS=$((PASS+1)); printf 'ok - %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf 'bad - %s\n' "$1"; }

TEST_TMP="$(mktemp -d)"
FOLLOW_PID=""
cleanup() {
  [[ -n "$FOLLOW_PID" ]] && kill "$FOLLOW_PID" 2>/dev/null
  rm -rf "$TEST_TMP"
}
trap cleanup EXIT

export HOME="$TEST_TMP/home"
export FORMATION_HOME="$TEST_TMP/formation-home"
export FORMATION_MAILBOX="$TEST_TMP/mailbox/log.jsonl"
export FORMATION_SELF="tester"
mkdir -p "$HOME" "$(dirname "$FORMATION_MAILBOX")"

# Standalone identity path: never inherit the invoking test pane.
unset TMUX TMUX_PANE

cat >"$FORMATION_MAILBOX" <<'EOF'
{"seq":7,"ts":"2026-08-12T09:00:00Z","from":"worker-a","to":"tester","body":"old already-read row"}
{"seq":8,"ts":"2026-08-12T09:01:00Z","from":"worker-b","to":"tester","body":"old unread row"}
EOF
mkdir -p "$(dirname "$FORMATION_MAILBOX")/cursor"
CURSOR="$(dirname "$FORMATION_MAILBOX")/cursor/tester.txt"
printf '7\n' >"$CURSOR"
CURSOR_BEFORE="$(cksum "$CURSOR")"

OUT="$TEST_TMP/follow.out"
ERR="$TEST_TMP/follow.err"
FORMATION_INBOX_FOLLOW_INTERVAL=1 bash "$BIN" inbox --follow \
  >"$OUT" 2>"$ERR" &
FOLLOW_PID=$!

# Bounded wait helper: retry until the output file matches or 10s elapse.
wait_grep() {
  local pattern="$1" i
  for i in $(seq 1 100); do
    grep -q "$pattern" "$OUT" 2>/dev/null && return 0
    sleep 0.1
  done
  return 1
}

if wait_grep 'pending=1 at follow start'; then
  ok "pre-existing unread collapses into one startup pending event"
else
  bad "no startup pending event [$(cat "$OUT")]"
fi

printf '%s\n' \
  '{"seq":9,"ts":"2026-08-12T09:02:00Z","from":"worker-c","to":"tester","subject":"done","body":"SECRET-BODY-MUST-NOT-LEAK"}' \
  '{"seq":10,"ts":"2026-08-12T09:03:00Z","from":"worker-d","to":"someone-else","body":"not ours"}' \
  >>"$FORMATION_MAILBOX"

if wait_grep 'mailbox seq=9 from=worker-c'; then
  ok "new addressed row emits one metadata event"
else
  bad "no event for seq 9 [$(cat "$OUT")]"
fi
if grep -q 'subject:done' "$OUT"; then
  ok "subject is included in the event line"
else
  bad "subject missing [$(cat "$OUT")]"
fi
if ! grep -q 'SECRET-BODY-MUST-NOT-LEAK' "$OUT"; then
  ok "body is never emitted"
else
  bad "body leaked into event stream"
fi
if ! grep -q 'seq=10' "$OUT"; then
  ok "row addressed to another worker is ignored"
else
  bad "foreign row leaked [$(cat "$OUT")]"
fi

# Control characters in attacker-controllable fields must not survive. A raw
# ESC byte would make the row invalid JSON (fromjson? drops it wholesale), so
# the realistic attack shape is a valid JSON escape that decodes to ESC.
printf '%s\n' \
  '{"seq":11,"ts":"2026-08-12T09:04:00Z","from":"worker-\u001b[31mevil","to":"tester","body":"x"}' \
  >>"$FORMATION_MAILBOX"
if wait_grep 'mailbox seq=11'; then
  if grep -q $'\x1b' "$OUT"; then
    bad "escape byte survived into event stream"
  else
    ok "control characters are stripped from event fields"
  fi
else
  bad "no event for seq 11 [$(cat "$OUT")]"
fi

# Same seq is never re-emitted by later appends (baseline drain discipline).
printf '%s\n' \
  '{"seq":12,"ts":"2026-08-12T09:05:00Z","from":"worker-e","to":"tester","body":"y"}' \
  >>"$FORMATION_MAILBOX"
wait_grep 'mailbox seq=12' >/dev/null
SEQ9_COUNT="$(grep -c 'mailbox seq=9 ' "$OUT")"
if [[ "$SEQ9_COUNT" == "1" ]]; then
  ok "each seq is emitted exactly once"
else
  bad "seq 9 emitted $SEQ9_COUNT times"
fi

CURSOR_AFTER="$(cksum "$CURSOR")"
if [[ "$CURSOR_BEFORE" == "$CURSOR_AFTER" ]]; then
  ok "follow leaves cursor byte-identical"
else
  bad "follow changed cursor"
fi

kill "$FOLLOW_PID" 2>/dev/null
wait "$FOLLOW_PID" 2>/dev/null
FOLLOW_PID=""

# Polling fallback must behave identically without inotify.
OUT2="$TEST_TMP/follow-poll.out"
FORMATION_INBOX_FOLLOW_INTERVAL=1 FORMATION_INBOX_FOLLOW_FORCE_POLL=1 \
  bash "$BIN" inbox --follow >"$OUT2" 2>/dev/null &
FOLLOW_PID=$!
# Wait for the baseline to be computed (the startup pending line proves it)
# before appending, or the new row is folded into the startup summary.
BASELINE_OK=1
for i in $(seq 1 100); do
  grep -q 'at follow start' "$OUT2" 2>/dev/null && BASELINE_OK=0 && break
  sleep 0.1
done
[[ "$BASELINE_OK" == "0" ]] || bad "poll-mode follow never reported startup"
printf '%s\n' \
  '{"seq":13,"ts":"2026-08-12T09:06:00Z","from":"worker-f","to":"tester","body":"z"}' \
  >>"$FORMATION_MAILBOX"
POLL_OK=1
for i in $(seq 1 100); do
  grep -q 'mailbox seq=13 from=worker-f' "$OUT2" 2>/dev/null && POLL_OK=0 && break
  sleep 0.1
done
if [[ "$POLL_OK" == "0" ]]; then
  ok "polling fallback emits the same event"
else
  bad "polling fallback missed seq 13 [$(cat "$OUT2")]"
fi
kill "$FOLLOW_PID" 2>/dev/null
wait "$FOLLOW_PID" 2>/dev/null
FOLLOW_PID=""

printf '%s ok, %s bad\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
