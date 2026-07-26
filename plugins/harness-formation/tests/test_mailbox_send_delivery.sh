#!/usr/bin/env bash
# Regression: mailbox-send M0 delivery contract (#166).
#
# Default path: append + non-destructive signal only (pane option / display-
# message). Must NOT paste or type into the recipient prompt.
# Opt-in --inject: shared bracketed-paste helper (not raw send-keys -l).

set -euo pipefail
trap 'rc=$?; echo "FAIL: test_mailbox_send_delivery line $LINENO rc=$rc" >&2; exit "$rc"' ERR

HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE="$(mktemp -d)"
RELAY_PIDS=()
cleanup() {
  local pid
  for pid in "${RELAY_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  rm -rf "$FIXTURE"
}
trap cleanup EXIT

FAKE_BIN="$FIXTURE/bin"
TMUX_LOG="$FIXTURE/tmux.log"
TMUX_STATE="$FIXTURE/tmux.pending"
MAILBOX="$FIXTURE/mailbox/log.jsonl"
FORMATION_HOME_FIXTURE="$FIXTURE/formation-home"
REGISTRY="$FORMATION_HOME_FIXTURE/formation/registry.jsonl"
mkdir -p "$FAKE_BIN" "$(dirname "$MAILBOX")" "$(dirname "$REGISTRY")"
ln -s "$HERE/../bin/mailbox-send" "$FAKE_BIN/mailbox-send"
ln -s "$HERE/../lib/mailbox_relay.sh" "$FAKE_BIN/mailbox-relay"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'printf "tmux %s\n" "$*" >>"$TMUX_LOG"' \
  'case "${1:-}" in' \
  '  list-panes) printf "%%42\n" ;;' \
  '  display-message|display) [[ " $* " == *" -p "* ]] && printf "0\n" || true ;;' \
  '  show-options)' \
  '    if [[ " $* " == *" @formation_exclusive_input "* ]]; then printf "%s\n" "${TMUX_EXCLUSIVE:-0}";' \
  '    elif [[ -s "${TMUX_STATE:-}" ]]; then cat "$TMUX_STATE"; fi ;;' \
  '  set-option)' \
  '    [[ "${TMUX_FAIL_SET_OPTION:-0}" != "1" ]] || exit 1' \
  '    if [[ " $* " == *" @formation_mail_pending "* && " $* " != *" -u "* ]]; then printf "%s\n" "${*: -1}" >"$TMUX_STATE"; fi ;;' \
  '  load-buffer) dd of=/dev/null status=none ;;' \
  'esac' \
  >"$FAKE_BIN/tmux"
chmod +x "$FAKE_BIN/tmux"

run_send() {
  local out="$1"; shift
  : >"$TMUX_LOG"
  TMUX_LOG="$TMUX_LOG" \
  FORMATION_HOME="$FORMATION_HOME_FIXTURE" \
  FORMATION_MAILBOX="$MAILBOX" \
  MAILBOX_FROM="fixture-sender" \
  MAILBOX_SUBMIT_SETTLE_S=0 \
  MAILBOX_SUBMIT_RETRY_S=0 \
	  TMUX_FAIL_SET_OPTION="${TMUX_FAIL_SET_OPTION:-0}" \
	  TMUX_EXCLUSIVE="${TMUX_EXCLUSIVE:-0}" \
	  TMUX_STATE="$TMUX_STATE" \
  PATH="$FAKE_BIN:/usr/bin:/bin" \
    "$FAKE_BIN/mailbox-send" "$@" >"$out" 2>&1
}

# Help is sourced from the comment header only; executable setup must not leak
# into the rendered usage text.
PATH="$FAKE_BIN:/usr/bin:/bin" "$FAKE_BIN/mailbox-send" --help >"$FIXTURE/help.out"
grep -Fq '5 injection refused' "$FIXTURE/help.out"
if grep -Eq '^set -|^SCRIPT_DIR=|^source ' "$FIXTURE/help.out"; then
  echo "FAIL: mailbox-send --help spilled executable code" >&2
  exit 1
fi

# --- default path: signal only, zero prompt keystrokes ---
run_send "$FIXTURE/default.stdout" %42 "delivery fixture"

grep -Fq 'appended seq=' "$FIXTURE/default.stdout"
grep -Fq 'signaled %42' "$FIXTURE/default.stdout"
grep -Fq 'inject=skipped' "$FIXTURE/default.stdout"
grep -Fq 'tmux set-option -p -t %42 @formation_mail_pending' "$TMUX_LOG"
grep -Fq 'tmux display-message -t %42' "$TMUX_LOG"
if grep -Eq 'paste-buffer|load-buffer|send-keys' "$TMUX_LOG"; then
  echo "FAIL: default path must not paste/type into the prompt (#166)" >&2
  cat "$TMUX_LOG" >&2
  exit 1
fi
jq -e '
  .from == "fixture-sender"
  and .to == "pane-42"
  and .body == "delivery fixture"
' "$MAILBOX" >/dev/null

# Signal ownership is serialized per pane and can never move the badge
# backwards when an older sender arrives after a newer one.
: > "$TMUX_STATE"
TMUX_LOG="$TMUX_LOG" TMUX_STATE="$TMUX_STATE" FORMATION_HOME="$FORMATION_HOME_FIXTURE" \
  PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash -c 'source "$1"; mailbox_signal_pane %42 20 newer; mailbox_signal_pane %42 19 older' \
    _ "$HERE/../lib/mailbox_notify.sh"
[[ "$(cat "$TMUX_STATE")" == "20" ]]

# --- --inject refuses a normal/shared pane after durable append + signal ---
if TMUX_EXCLUSIVE=0 run_send "$FIXTURE/inject-refused.stdout" %42 "refused inject fixture" --inject; then
  inject_refused_rc=0
else
  inject_refused_rc=$?
fi
[[ "$inject_refused_rc" -eq 5 ]]
grep -Fq 'row is durable and signaled' "$FIXTURE/inject-refused.stdout"
if grep -Eq 'paste-buffer|load-buffer|send-keys' "$TMUX_LOG"; then
  echo "FAIL: refused injection touched the prompt" >&2
  exit 1
fi

# --- exclusive --inject: bracketed paste contract, no raw -l body injection ---
jq -cn '{id:"worker-42", pane_id:"%42", exclusive_input:true}' > "$REGISTRY"
TMUX_EXCLUSIVE=1 run_send "$FIXTURE/inject.stdout" %42 "inject fixture" --inject

grep -Fq 'inject=attempted' "$FIXTURE/inject.stdout"
grep -Fq 'receipt unconfirmed' "$FIXTURE/inject.stdout"
grep -Fq 'tmux load-buffer -b ' "$TMUX_LOG"
grep -Fq 'tmux paste-buffer -t %42 ' "$TMUX_LOG"
grep -Fq -- '-p -d' "$TMUX_LOG"
[[ "$(grep -Fc 'tmux send-keys -t %42 Enter' "$TMUX_LOG")" -eq 2 ]]
if grep -Fq 'send-keys -t %42 -l' "$TMUX_LOG"; then
  echo "FAIL: mailbox-send regressed to raw send-keys text injection" >&2
  exit 1
fi
# Inject nudge must NOT contain the full body (body stays in mailbox).
if grep -Fq 'inject fixture' "$TMUX_LOG"; then
  echo "FAIL: --inject must not paste full body into the prompt" >&2
  cat "$TMUX_LOG" >&2
  exit 1
fi

# --- --no-nudge: append only ---
run_send "$FIXTURE/nonudge.stdout" %42 "silent fixture" --no-nudge
grep -Fq 'appended seq=' "$FIXTURE/nonudge.stdout"
if grep -Eq 'signaled|inject=' "$FIXTURE/nonudge.stdout"; then
  echo "FAIL: --no-nudge should skip signal and inject" >&2
  cat "$FIXTURE/nonudge.stdout" >&2
  exit 1
fi
if [[ -s "$TMUX_LOG" ]]; then
  echo "FAIL: --no-nudge must not call tmux" >&2
  cat "$TMUX_LOG" >&2
  exit 1
fi

# --- signal failure: append remains durable, but success is not claimed ---
: > "$TMUX_STATE"
if TMUX_FAIL_SET_OPTION=1 run_send "$FIXTURE/signal-failure.stdout" %42 "durable despite signal failure"; then
  signal_failure_rc=0
else
  signal_failure_rc=$?
fi
[[ "$signal_failure_rc" -eq 4 ]]
grep -Fq 'appended seq=' "$FIXTURE/signal-failure.stdout"
grep -Fq 'could not be signaled' "$FIXTURE/signal-failure.stdout"
if grep -Fq 'signaled %42' "$FIXTURE/signal-failure.stdout"; then
  echo "FAIL: signal failure was falsely reported as success" >&2
  cat "$FIXTURE/signal-failure.stdout" >&2
  exit 1
fi

# --- canonical addressing + single signal owner ---
jq -cn '{id:"worker-42", pane_id:"%42"}' > "$REGISTRY"
mkdir -p "$FORMATION_HOME_FIXTURE/formation"
: > "$FIXTURE/relay-owner-mailbox.jsonl"
FORMATION_MAILBOX="$FIXTURE/relay-owner-mailbox.jsonl" PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash "$HERE/../lib/mailbox_relay.sh" worker-42 %42 >"$FIXTURE/relay-owner.log" 2>&1 &
relay_pid=$!
RELAY_PIDS+=("$relay_pid")
printf '%s\n' "$relay_pid" > "$FORMATION_HOME_FIXTURE/formation/worker-42.relay_pid"
run_send "$FIXTURE/relay-owned.stdout" %42 "canonical worker fixture"
grep -Fq 'to=worker-42' "$FIXTURE/relay-owned.stdout"
grep -Fq 'signal=pending relay_pid=' "$FIXTURE/relay-owned.stdout"
if [[ -s "$TMUX_LOG" ]]; then
  echo "FAIL: mailbox-send duplicated signaling owned by the live relay" >&2
  cat "$TMUX_LOG" >&2
  exit 1
fi
jq -e 'select(.to == "worker-42" and .body == "canonical worker fixture")' "$MAILBOX" >/dev/null
run_send "$FIXTURE/worker-id.stdout" worker-42 "worker id addressing fixture" --no-nudge
grep -Fq 'to=worker-42' "$FIXTURE/worker-id.stdout"
jq -e 'select(.to == "worker-42" and .body == "worker id addressing fixture")' "$MAILBOX" >/dev/null
pkill -P "$relay_pid" 2>/dev/null || true
kill "$relay_pid" 2>/dev/null || true

# End-to-end formation msg: the CLI appends to the same mailbox watched by a
# real relay, claims only signal=pending, and the relay actually sets the badge.
: > "$TMUX_LOG"
: > "$TMUX_STATE"
FORMATION_MAILBOX="$MAILBOX" TMUX_LOG="$TMUX_LOG" TMUX_STATE="$TMUX_STATE" \
  PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash "$HERE/../lib/mailbox_relay.sh" worker-42 %42 >"$FIXTURE/formation-msg-relay.log" 2>&1 &
msg_relay_pid=$!
RELAY_PIDS+=("$msg_relay_pid")
printf '%s\n' "$msg_relay_pid" > "$FORMATION_HOME_FIXTURE/formation/worker-42.relay_pid"
sleep 0.15
TMUX_LOG="$TMUX_LOG" TMUX_STATE="$TMUX_STATE" FORMATION_HOME="$FORMATION_HOME_FIXTURE" \
  FORMATION_MAILBOX="$MAILBOX" FORMATION_SELF=integration-sender \
  PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash "$HERE/../bin/formation" msg worker-42 "formation msg relay integration" \
    >"$FIXTURE/formation-msg.stdout"
grep -Fq 'signal=pending relay_pid=' "$FIXTURE/formation-msg.stdout"
for _ in $(seq 1 40); do
  grep -Fq 'tmux set-option -p -t %42 @formation_mail_pending' "$TMUX_LOG" && break
  sleep 0.05
done
grep -Fq 'tmux set-option -p -t %42 @formation_mail_pending' "$TMUX_LOG"
jq -e 'select(.from == "integration-sender" and .to == "worker-42" and .body == "formation msg relay integration")' "$MAILBOX" >/dev/null
pkill -P "$msg_relay_pid" 2>/dev/null || true
kill "$msg_relay_pid" 2>/dev/null || true
rm -f "$REGISTRY" "$FORMATION_HOME_FIXTURE/formation/worker-42.relay_pid"

# --- all writers share one lock and one seq allocator ---
for i in $(seq 1 12); do
  FORMATION_MAILBOX="$MAILBOX" MAILBOX_FROM="cli-$i" \
    PATH="$FAKE_BIN:/usr/bin:/bin" \
    "$FAKE_BIN/mailbox-send" %42 "cli concurrent $i" --no-nudge >/dev/null &
  FORMATION_MAILBOX="$MAILBOX" PATH="$FAKE_BIN:/usr/bin:/bin" \
    bash -c 'source "$1"; mailbox_append "$2" pane-42 "$3" >/dev/null' \
      _ "$HERE/../lib/mailbox.sh" "lib-$i" "lib concurrent $i" &
done
wait
if jq -s '([.[].seq] | length) == ([.[].seq] | unique | length)' "$MAILBOX" | grep -qx true; then
  :
else
  echo "FAIL: concurrent mailbox writers produced duplicate seq values" >&2
  exit 1
fi
[[ "$(jq -s '[.[] | select(.body | startswith("cli concurrent ") or startswith("lib concurrent "))] | length' "$MAILBOX")" -eq 24 ]]

# The relay is normally launched by formation through its real path, but keep
# its sibling mailbox helpers resolvable if it is ever published as a symlink.
if relay_error="$("$FAKE_BIN/mailbox-relay" 2>&1)"; then
  relay_rc=0
else
  relay_rc=$?
fi
[[ "$relay_rc" -ne 0 ]]
grep -Fq 'agent name required' <<<"$relay_error"
if grep -Eq 'mailbox_(notify|relay)?\\.sh.*No such file|mailbox\\.sh.*No such file' <<<"$relay_error"; then
  echo "FAIL: mailbox relay could not resolve sibling helpers through a symlink" >&2
  exit 1
fi

# --- relay honors FORMATION_MAILBOX instead of silently watching the default ---
ALT_MAILBOX="$FIXTURE/alternate/mailbox.jsonl"
ALT_RELAY_LOG="$FIXTURE/alternate-relay.log"
mkdir -p "$(dirname "$ALT_MAILBOX")"
: > "$ALT_MAILBOX"
: > "$TMUX_STATE"
printf '%s\n' '#!/usr/bin/env bash' 'sleep 0.05' > "$FAKE_BIN/inotifywait"
chmod +x "$FAKE_BIN/inotifywait"
: > "$TMUX_LOG"
TMUX_LOG="$TMUX_LOG" FORMATION_HOME="$FORMATION_HOME_FIXTURE" \
  FORMATION_MAILBOX="$ALT_MAILBOX" PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash "$HERE/../lib/mailbox_relay.sh" alt-worker %42 >"$ALT_RELAY_LOG" 2>&1 &
alt_relay_pid=$!
RELAY_PIDS+=("$alt_relay_pid")
sleep 0.15
FORMATION_MAILBOX="$ALT_MAILBOX" PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash -c 'source "$1"; mailbox_append sender alt-worker alternate-body >/dev/null' \
    _ "$HERE/../lib/mailbox.sh"
for _ in $(seq 1 40); do
  grep -Fq '@formation_mail_pending' "$TMUX_LOG" && break
  sleep 0.05
done
grep -Fq "mailbox=$ALT_MAILBOX" "$ALT_RELAY_LOG"
grep -Fq 'tmux set-option -p -t %42 @formation_mail_pending' "$TMUX_LOG"

# A live relay must re-anchor after truncate/replace instead of remaining alive
# while silently ignoring every later row.
first_signal_count="$(grep -Fc 'tmux set-option -p -t %42 @formation_mail_pending' "$TMUX_LOG")"
: > "$ALT_MAILBOX"
sleep 0.1
FORMATION_MAILBOX="$ALT_MAILBOX" PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash -c 'source "$1"; mailbox_append sender alt-worker after-truncate >/dev/null' \
    _ "$HERE/../lib/mailbox.sh"
for _ in $(seq 1 40); do
  signal_count="$(grep -Fc 'tmux set-option -p -t %42 @formation_mail_pending' "$TMUX_LOG")"
  [[ "$signal_count" -gt "$first_signal_count" ]] && break
  sleep 0.05
done
[[ "$signal_count" -gt "$first_signal_count" ]]
grep -Fq 'last_seq=0' "$ALT_RELAY_LOG"

# Disaster recovery may restore a mailbox without its seq sidecar. If the new
# generation restarts below the in-memory high-water, fail safe by re-anchoring
# and signaling it instead of remaining permanently deaf.
second_signal_count="$signal_count"
: > "$ALT_MAILBOX"
mv "$ALT_MAILBOX.seq" "$ALT_MAILBOX.seq.previous-generation"
: > "$TMUX_STATE"
FORMATION_MAILBOX="$ALT_MAILBOX" PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash -c 'source "$1"; mailbox_append sender alt-worker reset-generation >/dev/null' \
    _ "$HERE/../lib/mailbox.sh"
for _ in $(seq 1 40); do
  signal_count="$(grep -Fc 'tmux set-option -p -t %42 @formation_mail_pending' "$TMUX_LOG")"
  [[ "$signal_count" -gt "$second_signal_count" ]] && break
  sleep 0.05
done
[[ "$signal_count" -gt "$second_signal_count" ]]
grep -Fq 'sequence generation moved backwards; re-anchor' "$ALT_RELAY_LOG"
kill "$alt_relay_pid" 2>/dev/null || true

# --- one-shot inotify gap: append during debounce must be drained ---
GAP_MAILBOX="$FIXTURE/gap/mailbox.jsonl"
GAP_RELAY_LOG="$FIXTURE/gap-relay.log"
GAP_NOTIFY_STATE="$FIXTURE/gap-inotify.state"
mkdir -p "$(dirname "$GAP_MAILBOX")"
: > "$GAP_MAILBOX"
: > "$TMUX_LOG"
: > "$TMUX_STATE"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if mkdir "$GAP_NOTIFY_STATE.once" 2>/dev/null; then sleep 0.10; exit 0; fi' \
  'sleep 30' \
  > "$FAKE_BIN/inotifywait"
chmod +x "$FAKE_BIN/inotifywait"
TMUX_LOG="$TMUX_LOG" TMUX_STATE="$TMUX_STATE" GAP_NOTIFY_STATE="$GAP_NOTIFY_STATE" \
  FORMATION_HOME="$FORMATION_HOME_FIXTURE" FORMATION_MAILBOX="$GAP_MAILBOX" \
  PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash "$HERE/../lib/mailbox_relay.sh" gap-worker %42 >"$GAP_RELAY_LOG" 2>&1 &
gap_relay_pid=$!
RELAY_PIDS+=("$gap_relay_pid")
sleep 0.05
FORMATION_MAILBOX="$GAP_MAILBOX" PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash -c 'source "$1"; mailbox_append sender gap-worker first-gap-body >/dev/null' \
    _ "$HERE/../lib/mailbox.sh"
# Relay debounces for 1s after the first signal; no watcher is armed then.
sleep 0.25
FORMATION_MAILBOX="$GAP_MAILBOX" PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash -c 'source "$1"; mailbox_append sender gap-worker second-gap-body >/dev/null' \
    _ "$HERE/../lib/mailbox.sh"
for _ in $(seq 1 60); do
  gap_signal_count="$(grep -Fc 'tmux set-option -p -t %42 @formation_mail_pending' "$TMUX_LOG" || true)"
  [[ "$gap_signal_count" -ge 2 ]] && break
  sleep 0.05
done
[[ "$gap_signal_count" -ge 2 ]]
grep -Fq 'new msg seq=1' "$GAP_RELAY_LOG"
grep -Fq 'new msg seq=2' "$GAP_RELAY_LOG"
pkill -P "$gap_relay_pid" 2>/dev/null || true
kill "$gap_relay_pid" 2>/dev/null || true

echo "test_mailbox_send_delivery: PASS"
