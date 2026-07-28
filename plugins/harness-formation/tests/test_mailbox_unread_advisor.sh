#!/usr/bin/env bash
# Regression tests for gh #232 mailbox unread context advisory.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/../bin/formation"
HOOK="$HERE/../hooks/mailbox_unread_advisor.sh"
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
REAL_PATH="$PATH"
export PATH="$FAKE_BIN:$REAL_PATH"

cat >"$FORMATION_MAILBOX" <<'EOF'
{"seq":7,"ts":"2026-07-27T09:12:03Z","from":"worker-a","to":"tester","body":"first"}
{"seq":8,"ts":"2026-07-27T09:13:03Z","from":"worker-b","to":"someone-else","body":"other"}
{"seq":9,"ts":"2026-07-27T09:14:03Z","from":"worker-c","to":"tester","body":"second"}
EOF
mkdir -p "$(dirname "$FORMATION_MAILBOX")/cursor"
CURSOR="$(dirname "$FORMATION_MAILBOX")/cursor/tester.txt"
printf '5\n' >"$CURSOR"
CURSOR_BEFORE="$(cksum "$CURSOR")"

COUNT_OUT="$(formation inbox --count)"
CURSOR_AFTER="$(cksum "$CURSOR")"
if [[ "$COUNT_OUT" == "2 9 2026-07-27T09:12:03Z" ]]; then
  ok "inbox --count reports unread count, max seq, and oldest timestamp"
else
  bad "inbox --count output [$COUNT_OUT]"
fi
if [[ "$CURSOR_BEFORE" == "$CURSOR_AFTER" ]]; then
  ok "inbox --count leaves cursor byte-identical"
else
  bad "inbox --count changed cursor"
fi

# No tmux badge mutation is attempted on the standalone path.
if ! find "$FORMATION_HOME" -type f -name '*mail_pending*' -print -quit |
     grep -q .; then
  ok "inbox --count leaves badge state untouched"
else
  bad "inbox --count created badge state"
fi

printf '9\n' >"$CURSOR"
ZERO_OUT="$(formation inbox --count)"
if [[ "$ZERO_OUT" == "0 0 -" ]]; then
  ok "inbox --count reports zero unread"
else
  bad "zero unread output [$ZERO_OUT]"
fi

# Re-open the two fixture rows for hook cooldown tests.
printf '5\n' >"$CURSOR"
PAYLOAD='{"hook_event_name":"UserPromptSubmit","session_id":"issue-232-test"}'
FIRST_HOOK="$(printf '%s' "$PAYLOAD" | bash "$HOOK")"
if printf '%s' "$FIRST_HOOK" |
   jq -e '.hookSpecificOutput.additionalContext | contains("件未読")' \
     >/dev/null 2>&1; then
  ok "hook injects unread additionalContext"
else
  bad "hook first injection missing [$FIRST_HOOK]"
fi

SECOND_HOOK="$(printf '%s' "$PAYLOAD" | bash "$HOOK")"
if [[ -z "$SECOND_HOOK" ]]; then
  ok "hook suppresses duplicate during cooldown"
else
  bad "hook repeated during cooldown [$SECOND_HOOK]"
fi

printf '%s\n' \
  '{"seq":10,"ts":"2026-07-27T09:15:03Z","from":"worker-d","to":"tester","body":"new"}' \
  >>"$FORMATION_MAILBOX"
THIRD_HOOK="$(printf '%s' "$PAYLOAD" | bash "$HOOK")"
if printf '%s' "$THIRD_HOOK" |
   jq -e '.hookSpecificOutput.additionalContext | contains("3 件未読")' \
     >/dev/null 2>&1; then
  ok "new max seq bypasses cooldown"
else
  bad "new max seq did not inject [$THIRD_HOOK]"
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
