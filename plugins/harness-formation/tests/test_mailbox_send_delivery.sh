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
TMUX_PAYLOAD_LOG="$FIXTURE/tmux.payload"
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
  '  display-message|display)' \
  '    if [[ " $* " == *" -p "* && " $* " == *formation_identity_locked* ]]; then printf "%s\n" "${TMUX_IDENTITY:-}";' \
  '    elif [[ " $* " == *" -p "* && " $* " == *window_name* ]]; then printf "%s\n" "${TMUX_WINDOW:-}";' \
  '    elif [[ " $* " == *" -p "* ]]; then printf "0\n"; fi ;;' \
  '  show-options)' \
  '    if [[ " $* " == *" @formation_exclusive_input "* ]]; then printf "%s\n" "${TMUX_EXCLUSIVE:-0}";' \
  '    elif [[ -s "${TMUX_STATE:-}" ]]; then cat "$TMUX_STATE"; fi ;;' \
  '  set-option)' \
  '    [[ "${TMUX_FAIL_SET_OPTION:-0}" != "1" ]] || exit 1' \
  '    if [[ " $* " == *" @formation_mail_pending "* && " $* " != *" -u "* ]]; then printf "%s\n" "${*: -1}" >"$TMUX_STATE"; fi ;;' \
  '  load-buffer)' \
  '    [[ "${TMUX_FAIL_LOAD:-0}" != "1" ]] || exit 1' \
  '    dd of="${TMUX_PAYLOAD_LOG:-/dev/null}" status=none ;;' \
  '  send-keys) [[ "${TMUX_FAIL_ENTER:-0}" != "1" ]] ;;' \
  'esac' \
  >"$FAKE_BIN/tmux"
chmod +x "$FAKE_BIN/tmux"

run_send() {
  local out="$1"; shift
  : >"$TMUX_LOG"
  : >"$TMUX_PAYLOAD_LOG"
  TMUX_LOG="$TMUX_LOG" \
  TMUX_PAYLOAD_LOG="$TMUX_PAYLOAD_LOG" \
  FORMATION_HOME="$FORMATION_HOME_FIXTURE" \
  FORMATION_MAILBOX="$MAILBOX" \
  MAILBOX_SUBMIT_SETTLE_S=0 \
  MAILBOX_SUBMIT_RETRY_S=0 \
	  TMUX_FAIL_SET_OPTION="${TMUX_FAIL_SET_OPTION:-0}" \
	  TMUX_FAIL_LOAD="${TMUX_FAIL_LOAD:-0}" \
	  TMUX_FAIL_ENTER="${TMUX_FAIL_ENTER:-0}" \
	  TMUX_EXCLUSIVE="${TMUX_EXCLUSIVE:-0}" \
	  TMUX_STATE="$TMUX_STATE" \
  PATH="$FAKE_BIN:/usr/bin:/bin" \
    "$FAKE_BIN/mailbox-send" "$@" --from fixture-sender >"$out" 2>&1
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
grep -Fq 'signal=sent-directly pane=%42' "$FIXTURE/default.stdout"
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

# Every public sender uses the locked Formation identity before a mutable
# window-name fallback.
resolved_sender="$(FORMATION_SELF="" MAILBOX_FROM="" \
  TMUX_PANE=%42 TMUX_IDENTITY=steady-heron \
  TMUX_LOG="$TMUX_LOG" \
  PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash -c 'source "$1"; mailbox_resolve_sender "" unknown' \
    _ "$HERE/../lib/mailbox_delivery.sh")"
[[ "$resolved_sender" == "steady-heron" ]]
resolved_sender="$(FORMATION_SELF=locked-worker MAILBOX_FROM=spoofed-window \
  bash -c 'source "$1"; mailbox_resolve_sender "" unknown' \
    _ "$HERE/../lib/mailbox_delivery.sh")"
[[ "$resolved_sender" == "locked-worker" ]]
resolved_sender="$(FORMATION_SELF="" MAILBOX_FROM=spoofed-window \
  TMUX_PANE=%42 TMUX_IDENTITY=steady-heron TMUX_LOG="$TMUX_LOG" \
  PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash -c 'source "$1"; mailbox_resolve_sender "" unknown' \
    _ "$HERE/../lib/mailbox_delivery.sh")"
[[ "$resolved_sender" == "steady-heron" ]]
resolved_sender="$(FORMATION_SELF="" MAILBOX_FROM=tooling-alias TMUX_PANE="" \
  bash -c 'source "$1"; mailbox_resolve_sender "" unknown' \
    _ "$HERE/../lib/mailbox_delivery.sh")"
[[ "$resolved_sender" == "tooling-alias" ]]
for mutable_window in claude-lead-alpha bash; do
  resolved_sender="$(FORMATION_SELF="" MAILBOX_FROM="" \
    TMUX_PANE=%42 TMUX_IDENTITY="" TMUX_WINDOW="$mutable_window" \
    TMUX_LOG="$TMUX_LOG" PATH="$FAKE_BIN:/usr/bin:/bin" \
    bash -c 'source "$1"; mailbox_resolve_sender "" shell 0 0' \
      _ "$HERE/../lib/mailbox_delivery.sh")"
  [[ "$resolved_sender" == "pane-42" ]]
done

# A live-looking TMUX_PANE is accepted as a parent route only when this process
# actually descends from that pane's root pid (#59). No process-name inference.
bash -c '
  tmux() { printf "%%42|%s\n" "$$"; }
  source "$1"
  [[ "$(mailbox_resolve_caller_pane)" == "%42" ]]
' _ "$HERE/../lib/mailbox_delivery.sh"
if bash -c '
  tmux() { printf "%%42|1\n"; }
  source "$1"
  mailbox_resolve_caller_pane
' _ "$HERE/../lib/mailbox_delivery.sh"; then
  echo "FAIL: inherited sibling TMUX_PANE passed the ancestry proof" >&2
  exit 1
fi

# Spawn refuses before creating a worker when neither a real parent pane nor a
# stable explicit Formation identity can receive replies.
printf '# review fixture\n' >"$FIXTURE/spawn-briefing.md"
: >"$TMUX_LOG"
if TMUX_PANE=%42 FORMATION_SELF="" TMUX_LOG="$TMUX_LOG" \
    FORMATION_HOME="$FORMATION_HOME_FIXTURE" FORMATION_MAILBOX="$MAILBOX" \
    PATH="$FAKE_BIN:/usr/bin:/bin" \
    bash "$HERE/../bin/formation" spawn --cli codex \
      "$FIXTURE/spawn-briefing.md" unaddressable-worker \
      >"$FIXTURE/spawn-unaddressable.out" 2>&1; then
  spawn_unaddressable_rc=0
else
  spawn_unaddressable_rc=$?
fi
[[ "$spawn_unaddressable_rc" -eq 2 ]]
grep -Fq 'replies would be unaddressable' "$FIXTURE/spawn-unaddressable.out"
if grep -Fq 'tmux new-window' "$TMUX_LOG"; then
  echo "FAIL: unaddressable spawn created a worker pane" >&2
  exit 1
fi

# Caller-provided submit timing remains authoritative over the standalone
# mailbox-send defaults after policy unification.
timing_log="$(FORMATION_SUBMIT_SETTLE_S=7 FORMATION_SUBMIT_RETRY_S=8 \
  MAILBOX_SUBMIT_SETTLE_S=9 MAILBOX_SUBMIT_RETRY_S=9 \
  bash -c '
    tmux() {
      case "$1" in
        show-options) printf "1\n" ;;
        display-message) printf "0\n" ;;
        load-buffer) cat >/dev/null ;;
      esac
    }
    sleep() { printf "sleep=%s\n" "$1"; }
    source "$1"
    mailbox_inject_nudge %42 1 sender 1
  ' _ "$HERE/../lib/mailbox_delivery.sh")"
grep -Fq 'sleep=7' <<<"$timing_log"
grep -Fq 'sleep=8' <<<"$timing_log"
if grep -Fq 'sleep=9' <<<"$timing_log"; then
  echo "FAIL: mailbox defaults overrode caller submit timing" >&2
  exit 1
fi

# Direct mailbox-send refusal uses the same metadata-only audit logger as
# formation msg/ask and never appends the credential-shaped body.
if run_send "$FIXTURE/credential-refused.stdout" %42 \
  "token=fixture-not-a-real-secret"; then
  credential_refused_rc=0
else
  credential_refused_rc=$?
fi
[[ "$credential_refused_rc" -eq 3 ]]
grep -Fq $'\tmailbox\tfrom=fixture-sender\tto=pane-42' \
  "$FORMATION_HOME_FIXTURE/mailbox/refuse.log"
if grep -Fq 'fixture-not-a-real-secret' \
  "$FORMATION_HOME_FIXTURE/mailbox/refuse.log" "$MAILBOX"; then
  echo "FAIL: credential-shaped body leaked into mailbox or refusal log" >&2
  exit 1
fi

# Every public mailbox-producing command must delegate signaling policy to the
# same library rather than re-growing local relay/injection implementations.
# Formation legitimately uses wake primitives for spawn and reap, so scope the
# direct-primitive check to the mailbox command block.
grep -Fq 'mailbox_delivery.sh' "$HERE/../bin/mailbox-send"
grep -Fq 'mailbox_delivery.sh' "$HERE/../bin/formation"
if grep -Eq '^[[:space:]]*(mailbox_relay_alive|mailbox_signal_pane|tmux_send_submit)' \
    "$HERE/../bin/mailbox-send" ||
   {
     sed -n '/^cmd_msg()/,/^clear_mailbox_badge_through()/p' \
       "$HERE/../bin/formation"
     sed -n '/^cmd_report()/,/^cmd_resolve()/p' \
       "$HERE/../bin/formation"
   } | grep -Eq '^[[:space:]]*(mailbox_relay_alive|mailbox_signal_pane|tmux_send_submit)'; then
  echo "FAIL: a public send path bypasses mailbox_delivery.sh" >&2
  exit 1
fi
for lifecycle_fn in cmd_report cmd_done cmd_ask cmd_request_transition; do
  sed -n "/^${lifecycle_fn}()/,/^}/p" "$HERE/../bin/formation" |
    grep -Fq 'mailbox_signal_durable_row ' || {
      echo "FAIL: $lifecycle_fn bypasses durable-row signaling policy" >&2
      exit 1
    }
done
[[ "$(grep -Fc -- '-e "FORMATION_PARENT_PANE=$parent_pane"' "$HERE/../bin/formation")" -eq 2 ]] || {
  echo "FAIL: spawn does not preserve parent pane routing for both placements" >&2
  exit 1
}

# A malformed/legacy route is pull-only; never pass a literal "null" target to
# tmux or claim a successful direct signal.
: >"$TMUX_LOG"
invalid_route_out="$(TMUX_LOG="$TMUX_LOG" PATH="$FAKE_BIN:/usr/bin:/bin" \
  bash -c 'source "$1"; mailbox_signal_durable_row worker-null null 9 sender "$2"' \
    _ "$HERE/../lib/mailbox_delivery.sh" "$FORMATION_HOME_FIXTURE/formation")"
grep -Fq 'route=absent-or-invalid' <<<"$invalid_route_out"
[[ ! -s "$TMUX_LOG" ]]

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
grep -Fq 'row is durable and its signal path was accepted' "$FIXTURE/inject-refused.stdout"
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
grep -Eq '^\[FORMATION-NUDGE from=fixture-sender seq=[0-9]+\] ' \
  "$TMUX_PAYLOAD_LOG"
grep -Fq 'pull with formation inbox' "$TMUX_PAYLOAD_LOG"
if grep -Fq 'send-keys -t %42 -l' "$TMUX_LOG"; then
  echo "FAIL: mailbox-send regressed to raw send-keys text injection" >&2
  exit 1
fi
# Inject nudge must NOT contain the full body (body stays in mailbox).
if grep -Fq 'inject fixture' "$TMUX_LOG" "$TMUX_PAYLOAD_LOG"; then
  echo "FAIL: --inject must not paste full body into the prompt" >&2
  cat "$TMUX_LOG" >&2
  exit 1
fi

# A failed exceptional nudge is never reported as attempted/successful. The
# mailbox row and non-destructive signal remain durable.
if TMUX_EXCLUSIVE=1 TMUX_FAIL_LOAD=1 \
  run_send "$FIXTURE/inject-failed.stdout" %42 "failed inject fixture" --inject; then
  inject_failed_rc=0
else
  inject_failed_rc=$?
fi
[[ "$inject_failed_rc" -eq 4 ]]
grep -Fq 'prompt nudge was not pasted' "$FIXTURE/inject-failed.stdout"
if grep -Fq 'inject=attempted' "$FIXTURE/inject-failed.stdout"; then
  echo "FAIL: failed prompt nudge was reported as attempted success" >&2
  exit 1
fi
jq -e 'select(.body == "failed inject fixture")' "$MAILBOX" >/dev/null

# Once paste succeeded, a submit failure is a distinct non-retryable state:
# retrying could merge a second nudge into the recipient draft.
if TMUX_EXCLUSIVE=1 TMUX_FAIL_ENTER=1 \
  run_send "$FIXTURE/submit-unconfirmed.stdout" %42 \
    "submit unconfirmed fixture" --inject; then
  submit_unconfirmed_rc=0
else
  submit_unconfirmed_rc=$?
fi
[[ "$submit_unconfirmed_rc" -eq 4 ]]
grep -Fq 'inject=pasted' "$FIXTURE/submit-unconfirmed.stdout"
grep -Fq 'DO NOT RETRY automatically' "$FIXTURE/submit-unconfirmed.stdout"
grep -Fq 'tmux paste-buffer -t %42 ' "$TMUX_LOG"
grep -Fq 'tmux send-keys -t %42 Enter' "$TMUX_LOG"
if grep -Fq 'inject=attempted' "$FIXTURE/submit-unconfirmed.stdout"; then
  echo "FAIL: pasted-but-unconfirmed nudge was reported as successful" >&2
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
grep -Fq 'signal=relay-owned relay_pid=' "$FIXTURE/relay-owned.stdout"
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
# real relay, claims only signal=relay-owned, and the relay actually sets the badge.
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
grep -Fq 'signal=relay-owned relay_pid=' "$FIXTURE/formation-msg.stdout"
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
  FORMATION_MAILBOX="$MAILBOX" \
    PATH="$FAKE_BIN:/usr/bin:/bin" \
    "$FAKE_BIN/mailbox-send" %42 "cli concurrent $i" \
      --no-nudge --from "cli-$i" >/dev/null &
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
