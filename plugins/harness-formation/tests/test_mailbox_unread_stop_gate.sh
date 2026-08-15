#!/usr/bin/env bash
# Regression tests for the gh #273 unread-mail Stop gate.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/../bin/formation"
HOOK="$HERE/../hooks/mailbox_unread_stop_gate.sh"
PASS=0
FAIL=0
ok()  { PASS=$((PASS+1)); printf 'ok - %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf 'bad - %s\n' "$1"; }

TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT
export HOME="$TEST_TMP/home"
export FORMATION_HOME="$TEST_TMP/formation-home"
export FORMATION_MAILBOX="$TEST_TMP/mailbox/log.jsonl"
export FORMATION_SELF="tester"
export XDG_RUNTIME_DIR="$TEST_TMP/runtime"
mkdir -p "$HOME" "$(dirname "$FORMATION_MAILBOX")" "$XDG_RUNTIME_DIR"

# Standalone identity path: never inherit the invoking test pane.
unset TMUX TMUX_PANE

FAKE_BIN="$TEST_TMP/bin"
mkdir -p "$FAKE_BIN"
ln -s "$BIN" "$FAKE_BIN/formation"
export PATH="$FAKE_BIN:$PATH"

cat >"$FORMATION_MAILBOX" <<'EOF'
{"seq":7,"ts":"2026-07-30T09:15:52Z","from":"worker-a","to":"tester","body":"[DONE] finished"}
{"seq":8,"ts":"2026-07-30T09:16:52Z","from":"worker-b","to":"someone-else","body":"other"}
EOF
mkdir -p "$(dirname "$FORMATION_MAILBOX")/cursor"
CURSOR="$(dirname "$FORMATION_MAILBOX")/cursor/tester.txt"
printf '0\n' >"$CURSOR"

PAYLOAD='{"hook_event_name":"Stop","session_id":"issue-273-test","stop_hook_active":false}'

FIRST="$(printf '%s' "$PAYLOAD" | bash "$HOOK")"
if printf '%s' "$FIRST" |
   jq -e '(.decision == "block") and (.reason | contains("件未読"))' \
     >/dev/null 2>&1; then
  ok "unread mail blocks stop with a readable reason"
else
  bad "first stop did not block [$FIRST]"
fi

SECOND="$(printf '%s' "$PAYLOAD" | bash "$HOOK")"
if [[ -z "$SECOND" ]]; then
  ok "same max seq never blocks twice"
else
  bad "second stop blocked again [$SECOND]"
fi

printf '%s\n' \
  '{"seq":9,"ts":"2026-07-30T09:17:52Z","from":"worker-c","to":"tester","body":"[ASK] question"}' \
  >>"$FORMATION_MAILBOX"
THIRD="$(printf '%s' "$PAYLOAD" | bash "$HOOK")"
if printf '%s' "$THIRD" |
   jq -e '.decision == "block"' >/dev/null 2>&1; then
  ok "new max seq blocks again"
else
  bad "new seq did not block [$THIRD]"
fi

ACTIVE_PAYLOAD='{"hook_event_name":"Stop","session_id":"issue-273-active","stop_hook_active":true}'
ACTIVE="$(printf '%s' "$ACTIVE_PAYLOAD" | bash "$HOOK")"
if [[ -z "$ACTIVE" ]]; then
  ok "stop_hook_active allows stop unconditionally"
else
  bad "stop_hook_active blocked [$ACTIVE]"
fi

printf '9\n' >"$CURSOR"
READ_ALL="$(printf '%s' "${PAYLOAD/issue-273-test/issue-273-read}" | bash "$HOOK")"
if [[ -z "$READ_ALL" ]]; then
  ok "zero unread allows stop"
else
  bad "zero unread blocked [$READ_ALL]"
fi

printf '0\n' >"$CURSOR"
DISABLED="$(printf '%s' "${PAYLOAD/issue-273-test/issue-273-off}" |
  FORMATION_MAILBOX_STOP_GATE_DISABLE=1 bash "$HOOK")"
if [[ -z "$DISABLED" ]]; then
  ok "kill switch disables the gate"
else
  bad "kill switch ignored [$DISABLED]"
fi

MISSING_HOME="$TEST_TMP/missing-home"
mkdir -p "$MISSING_HOME"
ABSENT_OUT="$(
  printf '%s' "$PAYLOAD" |
    HOME="$MISSING_HOME" PATH="$TEST_TMP/no-formation" \
      /bin/bash "$HOOK" 2>/dev/null
)"
ABSENT_RC=$?
if [[ "$ABSENT_RC" -eq 0 && -z "$ABSENT_OUT" ]]; then
  ok "absent formation CLI fails open silently"
else
  bad "absent formation CLI rc=$ABSENT_RC output=[$ABSENT_OUT]"
fi

printf '%s ok, %s bad\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
