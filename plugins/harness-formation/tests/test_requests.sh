#!/usr/bin/env bash
# Durable semantic ASK/ACK state (#136). Transport receipt remains separate.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE="$(mktemp -d)"
RELAY_PID=""
cleanup() {
  [[ -n "$RELAY_PID" ]] && kill "$RELAY_PID" 2>/dev/null || true
  rm -rf "$FIXTURE"
}
trap cleanup EXIT

export FORMATION_HOME="$FIXTURE/formation-home"
export FORMATION_MAILBOX="$FORMATION_HOME/mailbox/log.jsonl"
export FORMATION_REQUEST_DIR="$FORMATION_HOME/requests"
export FORMATION_REQUEST_LOG="$FORMATION_REQUEST_DIR/events.jsonl"
export FORMATION_SELF=worker-a
export FORMATION_PARENT=parent-a
unset TMUX_PANE

TMUX_LOG="$FIXTURE/tmux.log"
: >"$TMUX_LOG"
tmux() {
  printf 'tmux %s\n' "$*" >>"$TMUX_LOG"
  case "${1:-}" in
    show-options) return 0 ;;
    set-option) [[ "${TMUX_FAIL_SET_OPTION:-0}" != "1" ]] ;;
    display-message|capture-pane|kill-pane) return 0 ;;
  esac
}

# shellcheck source=/dev/null
source "$HERE/../bin/formation"

[[ "$(grep -Fc 'def valid_request_event' "$HERE/../lib/requests.sh")" -eq 1 ]] || {
  echo "FAIL: request event schema gate is duplicated" >&2
  exit 1
}

# Readers must share the writer lock; otherwise a large JSONL append can be
# observed mid-write and the unresolved-ASK reap gate can fail open.
request_init
LOCK_HELD="$FIXTURE/request-lock-held"
(
  flock -x 202
  touch "$LOCK_HELD"
  sleep 0.25
) 202>"$FORMATION_REQUEST_LOCK" &
LOCK_HOLDER_PID=$!
for _ in $(seq 1 40); do
  [[ -e "$LOCK_HELD" ]] && break
  sleep 0.01
done
lock_wait_start="$(date +%s%N)"
request_snapshot >/dev/null
lock_wait_ms=$(( ($(date +%s%N) - lock_wait_start) / 1000000 ))
wait "$LOCK_HOLDER_PID"
[[ "$lock_wait_ms" -ge 150 ]] || {
  echo "FAIL: request snapshot did not wait for the writer lock (${lock_wait_ms}ms)" >&2
  exit 1
}

request_id="$(FORMATION_PARENT_PANE=%100 cmd_ask 'Choose rollout A or B')"
[[ "$request_id" == req-* ]]
[[ "$(request_unresolved worker-a parent-a | jq -r '.request_id')" == "$request_id" ]]
[[ "$(jq -Rsc '[splits("\n") | fromjson? | select(.body | startswith("[ASK "))] | length' "$MAILBOX_LOG")" -eq 1 ]]
grep -Fq 'tmux set-option -p -t %100 @formation_mail_pending' "$TMUX_LOG"
if grep -Eq 'paste-buffer|load-buffer|send-keys' "$TMUX_LOG"; then
  echo "FAIL: worker ASK signal touched the parent prompt" >&2
  exit 1
fi

# Ordinary report/done use the same parent route and never touch its prompt.
: >"$TMUX_LOG"
FORMATION_PARENT_PANE=%100 cmd_report 'parent signal report'
FORMATION_PARENT_PANE=%100 cmd_done 'parent signal done'
[[ "$(grep -Fc 'tmux set-option -p -t %100 @formation_mail_pending' "$TMUX_LOG")" -eq 2 ]]
jq -Rsc '
  ([splits("\n") | fromjson? | select(.body == "parent signal report")] | length) == 1
  and
  ([splits("\n") | fromjson? | select(.body == "[DONE] parent signal done")] | length) == 1
' "$MAILBOX_LOG" | grep -qx true
if grep -Eq 'paste-buffer|load-buffer|send-keys' "$TMUX_LOG"; then
  echo "FAIL: worker report/done signal touched the parent prompt" >&2
  exit 1
fi

# A live argv-verified parent relay remains the single signal owner for
# lifecycle rows; the sender must defer instead of writing a duplicate badge.
mkdir -p "$FORMATION_DIR"
printf '%s\n' '#!/usr/bin/env bash' 'while :; do sleep 1; done' \
  >"$FIXTURE/mailbox_relay.sh"
chmod +x "$FIXTURE/mailbox_relay.sh"
bash "$FIXTURE/mailbox_relay.sh" parent-a %100 &
RELAY_PID=$!
printf '%s\n' "$RELAY_PID" >"$FORMATION_DIR/parent-a.relay_pid"
: >"$TMUX_LOG"
FORMATION_PARENT_PANE=%100 cmd_report 'relay owns lifecycle signal' \
  2>"$FIXTURE/relay-owned-report.err"
grep -Fq 'signal=relay-owned relay_pid=' "$FIXTURE/relay-owned-report.err"
if grep -Fq '@formation_mail_pending' "$TMUX_LOG"; then
  echo "FAIL: lifecycle sender duplicated a live relay's badge write" >&2
  exit 1
fi
kill "$RELAY_PID" 2>/dev/null || true
wait "$RELAY_PID" 2>/dev/null || true
RELAY_PID=""
rm -f "$FORMATION_DIR/parent-a.relay_pid"

# Pre-upgrade workers without FORMATION_PARENT_PANE remain pull-only and
# successful; the row is durable and no badge success is claimed.
FORMATION_PARENT_PANE="" cmd_report 'legacy pull-only report' \
  2>"$FIXTURE/pull-only-report.err"
grep -Fq 'signal=unavailable' "$FIXTURE/pull-only-report.err"
jq -Rsc \
  '[splits("\n") | fromjson? | select(.body == "legacy pull-only report")] | length == 1' \
  "$MAILBOX_LOG" | grep -qx true

# Exact unresolved retries are idempotent in both semantic state and transport.
retry_id="$(FORMATION_PARENT_PANE=%100 cmd_ask 'Choose rollout A or B')"
[[ "$retry_id" == "$request_id" ]]
[[ "$(wc -l < "$FORMATION_REQUEST_LOG")" -eq 1 ]]
[[ "$(jq -Rsc '[splits("\n") | fromjson? | select(.body | startswith("[ASK "))] | length' "$MAILBOX_LOG")" -eq 1 ]]

# Concurrent retries converge to one request event and one mailbox ASK.
for _ in $(seq 1 12); do
  (cmd_ask 'Concurrent decision') >"$FIXTURE/concurrent.$_" &
done
wait
[[ "$(cat "$FIXTURE"/concurrent.* | sort -u | wc -l)" -eq 1 ]]
concurrent_id="$(head -n1 "$FIXTURE/concurrent.1")"
[[ "$(jq -Rsc --arg id "$concurrent_id" \
  '[splits("\n") | fromjson? | select(.request_id == $id)] | length' \
  "$FORMATION_REQUEST_LOG")" -eq 1 ]]
[[ "$(jq -Rsc --arg id "[ASK request_id=$concurrent_id]" \
  '[splits("\n") | fromjson? | select((.body // "") | startswith($id))] | length' \
  "$MAILBOX_LOG")" -eq 1 ]]

# Later ordinary reports cannot replace the sticky WAITING_PARENT status.
registry_add worker-a %999 formation-worker-a "$FIXTURE/brief.md" sid codex task goal 0
cmd_report 'later progress report'
status="$(cmd_status)"
grep -Fq "WAITING_PARENT worker=worker-a request=$request_id" <<<"$status"
grep -Fq "request=$request_id parent=parent-a" <<<"$status"
grep -Fq 'Choose rollout A or B' <<<"$status"
mail_before_status="$(wc -l < "$MAILBOX_LOG")"
cmd_status >/dev/null
cmd_status >/dev/null
[[ "$(wc -l < "$MAILBOX_LOG")" -eq "$mail_before_status" ]]

# Parent inbox renders unresolved semantic state before mailbox transport.
FORMATION_SELF=parent-a
inbox="$(cmd_inbox)"
grep -Fq '== UNRESOLVED ASK' <<<"$inbox"
grep -Fq "[ASK $request_id] worker=worker-a" <<<"$inbox"
grep -Fq "worker=worker-a parent=parent-a" <<<"$inbox"
grep -Fq 'UNTRUSTED ASK DATA' <<<"$inbox"

# A non-parent cannot close the request.
FORMATION_SELF=intruder
set +e
cmd_ack "$request_id" nope >"$FIXTURE/intruder.out" 2>&1
rc=$?
set -e
[[ "$rc" -eq 5 ]]
request_current_one "$request_id" | jq -e \
  '.closed == false and .state == "WAITING_PARENT"' >/dev/null

# The legacy literal parent id "lead" is still an identity, not a wildcard.
FORMATION_SELF=worker-lead-child
FORMATION_PARENT=lead
lead_id="$(cmd_ask 'Only literal lead may close this')"
FORMATION_SELF=intruder
set +e
cmd_ack "$lead_id" stolen >"$FIXTURE/intruder-lead.out" 2>&1
rc=$?
set -e
[[ "$rc" -eq 5 ]]
request_current_one "$lead_id" | jq -e '.closed == false' >/dev/null
FORMATION_SELF=lead
cmd_ack "$lead_id" acknowledged | grep -Fq 'notified=true'
FORMATION_PARENT=parent-a

# ACK closes semantic state, transitions to RUNNING, and notifies once.
FORMATION_SELF=parent-a
ack_out="$(cmd_ack "$request_id" 'review started')"
grep -Fq "request=$request_id event=ack state=RUNNING notified=true" <<<"$ack_out"
grep -Fq 'tmux set-option -p -t %999 @formation_mail_pending' "$TMUX_LOG"
if grep -Eq 'paste-buffer|load-buffer|send-keys' "$TMUX_LOG"; then
  echo "FAIL: ACK signal touched the worker prompt" >&2
  exit 1
fi
request_current_one "$request_id" | jq -e \
  '.closed == true and .state == "RUNNING" and .event == "ack"' >/dev/null
ack_rows="$(jq -Rsc --arg id "[ACK request_id=$request_id]" \
  '[splits("\n") | fromjson? | select((.body // "") | startswith($id))] | length' "$MAILBOX_LOG")"
[[ "$ack_rows" -eq 1 ]]

# Duplicate ACK is a no-op and produces no duplicate worker notification.
cmd_ack "$request_id" 'review started' | grep -Fq 'notified=false'
[[ "$(jq -Rsc --arg id "[ACK request_id=$request_id]" \
  '[splits("\n") | fromjson? | select((.body // "") | startswith($id))] | length' "$MAILBOX_LOG")" -eq 1 ]]

# Every lifecycle caller preserves the durable row/state and returns the shared
# honest exit 4 when a known pane cannot be badged.
set +e
TMUX_FAIL_SET_OPTION=1 FORMATION_PARENT_PANE=%100 \
  cmd_report 'report survives signal failure' \
  >"$FIXTURE/report-signal-fail.out" 2>&1
report_signal_rc=$?
TMUX_FAIL_SET_OPTION=1 FORMATION_PARENT_PANE=%100 \
  cmd_done 'done survives signal failure' \
  >"$FIXTURE/done-signal-fail.out" 2>&1
done_signal_rc=$?
FORMATION_SELF=worker-a TMUX_FAIL_SET_OPTION=1 FORMATION_PARENT_PANE=%100 \
  cmd_ask 'ASK survives signal failure' \
  >"$FIXTURE/ask-signal-fail.out" 2>"$FIXTURE/ask-signal-fail.err"
ask_signal_rc=$?
set -e
[[ "$report_signal_rc" -eq 4 && "$done_signal_rc" -eq 4 && "$ask_signal_rc" -eq 4 ]]
grep -Fq 'FAILED (exit 4)' "$FIXTURE/report-signal-fail.out"
grep -Fq 'FAILED (exit 4)' "$FIXTURE/done-signal-fail.out"
grep -Fq 'FAILED (exit 4)' "$FIXTURE/ask-signal-fail.err"
failed_ask_id="$(cat "$FIXTURE/ask-signal-fail.out")"
[[ "$failed_ask_id" == req-* ]]
request_current_one "$failed_ask_id" | jq -e '.state == "WAITING_PARENT"' >/dev/null
jq -Rsc '
  ([splits("\n") | fromjson? | select(.body == "report survives signal failure")] | length) == 1
  and
  ([splits("\n") | fromjson? | select(.body == "[DONE] done survives signal failure")] | length) == 1
' "$MAILBOX_LOG" | grep -qx true

FORMATION_SELF=worker-a
ack_signal_create="$(request_create worker-a parent-a 'ACK survives signal failure' RUNNING)"
ack_signal_id="$(jq -r '.request_id' <<<"$ack_signal_create")"
ensure_request_mailbox_message worker-a parent-a \
  "[ASK request_id=$ack_signal_id]" "[ASK request_id=$ack_signal_id] ACK survives signal failure" >/dev/null
set +e
FORMATION_SELF=parent-a TMUX_FAIL_SET_OPTION=1 \
  cmd_ack "$ack_signal_id" durable \
  >"$FIXTURE/ack-signal-fail.out" 2>"$FIXTURE/ack-signal-fail.err"
ack_signal_rc=$?
set -e
[[ "$ack_signal_rc" -eq 4 ]]
grep -Fq 'notified=true' "$FIXTURE/ack-signal-fail.out"
grep -Fq 'FAILED (exit 4)' "$FIXTURE/ack-signal-fail.err"
request_current_one "$ack_signal_id" | jq -e '.closed == true and .state == "RUNNING"' >/dev/null
FORMATION_SELF=parent-a

# No verified route is an explicit pull-only success, not a false tmux signal.
no_route_out="$(mailbox_signal_durable_row parent-a "" 1 worker-a "$FORMATION_DIR")"
grep -Fq 'route=absent-or-invalid' <<<"$no_route_out"

# A malformed legacy registry row without pane_id must not become tmux target
# "null" or a false successful badge.
jq -cn '{id:"worker-null", session_name:"legacy"}' >>"$REGISTRY"
null_route_create="$(request_create worker-null parent-a 'Missing pane route' RUNNING)"
null_route_id="$(jq -r '.request_id' <<<"$null_route_create")"
ensure_request_mailbox_message worker-null parent-a \
  "[ASK request_id=$null_route_id]" "[ASK request_id=$null_route_id] Missing pane route" >/dev/null
: >"$TMUX_LOG"
FORMATION_SELF=parent-a cmd_ack "$null_route_id" durable \
  >"$FIXTURE/null-route-ack.out" 2>"$FIXTURE/null-route-ack.err"
grep -Fq 'notified=true' "$FIXTURE/null-route-ack.out"
grep -Fq 'route=absent-or-invalid' "$FIXTURE/null-route-ack.err"
if grep -Eq '@formation_status|@formation_mail_pending|-t null ' "$TMUX_LOG"; then
  echo "FAIL: missing pane_id mutated a tmux pane through an empty/null target" >&2
  exit 1
fi
registry_remove worker-null

# Concurrent ACK retries append one transition event and one notification.
FORMATION_SELF=worker-a
ack_race_id="$(cmd_ask 'Concurrent ACK decision')"
FORMATION_SELF=parent-a
for _ in $(seq 1 12); do
  (cmd_ack "$ack_race_id" 'concurrent ack') >"$FIXTURE/ack-race.$_" &
done
wait
[[ "$(jq -Rsc --arg id "$ack_race_id" \
  '[splits("\n") | fromjson? | select(.request_id == $id and .event == "ack")] | length' \
  "$FORMATION_REQUEST_LOG")" -eq 1 ]]
[[ "$(jq -Rsc --arg id "[ACK request_id=$ack_race_id]" \
  '[splits("\n") | fromjson? | select((.body // "") | startswith($id))] | length' \
  "$MAILBOX_LOG")" -eq 1 ]]

# If the process closed state but died before mailbox append, retry heals the
# semantic-state/transport gap without reopening the request.
FORMATION_SELF=worker-a
heal_create="$(request_create worker-a parent-a 'Heal missing ACK transport' RUNNING)"
heal_id="$(jq -r '.request_id' <<<"$heal_create")"
request_transition "$heal_id" ack parent-a healed >/dev/null
FORMATION_SELF=parent-a
heal_out="$(cmd_ack "$heal_id" healed)"
grep -Fq "request=$heal_id event=ack state=RUNNING notified=true" <<<"$heal_out"
[[ "$(jq -Rsc --arg id "[ACK request_id=$heal_id]" \
  '[splits("\n") | fromjson? | select((.body // "") | startswith($id))] | length' "$MAILBOX_LOG")" -eq 1 ]]

# A resolved request transitions to the worker-declared next state.
FORMATION_SELF=worker-a
resolve_id="$(cmd_ask --next-state READY_TO_MERGE 'Approve final merge')"
FORMATION_SELF=parent-a
resolve_out="$(cmd_resolve "$resolve_id" 'approved')"
grep -Fq "request=$resolve_id event=resolve state=READY_TO_MERGE notified=true" <<<"$resolve_out"

# Credential-shaped ACK summaries are refused before semantic transition.
FORMATION_SELF=worker-a
cred_ack_id="$(cmd_ask 'Review safe summary')"
before_requests="$(wc -l < "$FORMATION_REQUEST_LOG")"
FORMATION_SELF=parent-a
set +e
cmd_ack "$cred_ack_id" 'password=literal-secret' >"$FIXTURE/refused-ack.out" 2>&1
rc=$?
set -e
[[ "$rc" -eq 3 ]]
[[ "$(wc -l < "$FORMATION_REQUEST_LOG")" -eq "$before_requests" ]]
request_current_one "$cred_ack_id" | jq -e '.closed == false' >/dev/null

# Reap refuses unresolved ASK unless the operator explicitly forces it.
FORMATION_SELF=worker-a
reap_id="$(cmd_ask 'May this pane be reaped?')"
FORMATION_SELF=parent-a
set +e
cmd_reap worker-a >"$FIXTURE/reap.out" 2>&1
rc=$?
set -e
[[ "$rc" -eq 6 ]]
grep -Fq "$reap_id" "$FIXTURE/reap.out"
registry_get worker-a | grep -q .

# Credential-shaped questions fail before either durable store is mutated.
before_requests="$(wc -l < "$FORMATION_REQUEST_LOG")"
before_mail="$(wc -l < "$MAILBOX_LOG")"
FORMATION_SELF=worker-a
set +e
cmd_ask 'password = literal-secret' >"$FIXTURE/refused.out" 2>&1
rc=$?
set -e
[[ "$rc" -eq 3 ]]
[[ "$(wc -l < "$FORMATION_REQUEST_LOG")" -eq "$before_requests" ]]
[[ "$(wc -l < "$MAILBOX_LOG")" -eq "$before_mail" ]]

# Ordinary report/done append transport only and never create request events.
before_requests="$(wc -l < "$FORMATION_REQUEST_LOG")"
cmd_report 'ordinary report'
cmd_done 'ordinary done'
[[ "$(wc -l < "$FORMATION_REQUEST_LOG")" -eq "$before_requests" ]]

# Malformed state rows do not hide valid requests after them.
printf '%s\n' '{not-json' >>"$FORMATION_REQUEST_LOG"
malformed_id="$(cmd_ask 'Valid after malformed state')"
request_current_one "$malformed_id" | jq -e '.state == "WAITING_PARENT"' >/dev/null
# Valid JSON with the same id but an incomplete schema is also fail-safe:
# ignore it rather than treating a missing state as implicitly resolved.
jq -cn --arg id "$malformed_id" '{request_id:$id,event:"broken"}' \
  >>"$FORMATION_REQUEST_LOG"
request_current_one "$malformed_id" | jq -e \
  '.state == "WAITING_PARENT" and .closed == false' >/dev/null

# A fresh shell can reconstruct unresolved state without in-memory registry
# state, and status rendering strips terminal control bytes from questions.
FORMATION_SELF=worker-a
control_id="$(cmd_ask $'control \033[31m text')"
registry_add control-worker %998 formation-control "$FIXTURE/control.md" sid codex task goal 0
request_create control-worker $'parent\033[31m' 'parent control' RUNNING >/dev/null
FORMATION_SELF=parent-a
FORMATION_HOME="$FORMATION_HOME" FORMATION_REQUEST_LOG="$FORMATION_REQUEST_LOG" \
  bash -c 'source "$1"; request_current_one "$2"' \
    _ "$HERE/../lib/requests.sh" "$control_id" | jq -e '.state == "WAITING_PARENT"' >/dev/null
status="$(cmd_status)"
[[ "$status" != *$'\033'* ]]

# Explicit history is read-only with respect to the cursor.
FORMATION_SELF=parent-a
cursor_before="$(cat "$MAILBOX_CURSOR_DIR/parent-a.txt")"
history="$(cmd_inbox --history)"
grep -Fq '== MAILBOX HISTORY (last 50 addressed rows) ==' <<<"$history"
[[ "$(cat "$MAILBOX_CURSOR_DIR/parent-a.txt")" == "$cursor_before" ]]

# A stale inherited TMUX_PANE must not split spawn's parent identity from the
# lead's later inbox/ACK identity or clear a sibling's badge (#59).
(
  export FORMATION_HOME="$FIXTURE/stale-pane-home"
  export FORMATION_MAILBOX="$FORMATION_HOME/mailbox/log.jsonl"
  export FORMATION_REQUEST_DIR="$FORMATION_HOME/requests"
  export FORMATION_REQUEST_LOG="$FORMATION_REQUEST_DIR/events.jsonl"
  export TMUX_PANE=%171
  unset FORMATION_SELF FORMATION_PARENT FORMATION_PARENT_PANE
  STALE_TMUX_LOG="$FIXTURE/stale-pane-tmux.log"
  : >"$STALE_TMUX_LOG"
  tmux() {
    printf 'tmux %s\n' "$*" >>"$STALE_TMUX_LOG"
    case "${1:-}" in
      list-panes) printf '%%209|%s\n' "$$" ;;
      display-message)
        if [[ " $* " == *" -p "* && " $* " == *formation_identity_locked* ]]; then
          if [[ " $* " == *" -t %171 "* ]]; then printf 'stale-worker\n'; else printf '\n'; fi
        elif [[ " $* " == *" -p "* ]]; then
          printf '0\n'
        fi
        ;;
      show-options) printf '1\n' ;;
      set-option|capture-pane|kill-pane) return 0 ;;
    esac
  }

  actual_parent="$(self_id)"
  [[ "$actual_parent" == "pane-209" ]]
  registry_add stale-child %300 formation-stale-child \
    "$FIXTURE/brief.md" stale-sid codex stale-task stale-goal 0
  export FORMATION_SELF=stale-child FORMATION_PARENT="$actual_parent"
  export FORMATION_PARENT_PANE=%209
  stale_request_id="$(cmd_ask 'Stale pane routing decision')"
  [[ "$stale_request_id" == req-* ]]

  unset FORMATION_SELF FORMATION_PARENT FORMATION_PARENT_PANE
  stale_inbox="$(cmd_inbox)"
  grep -Fq "$stale_request_id" <<<"$stale_inbox"
  cmd_ack "$stale_request_id" accepted >/dev/null
  request_current_one "$stale_request_id" |
    jq -e '.closed == true and .state == "RUNNING"' >/dev/null
  grep -Fq 'tmux set-option -p -u -t %209 @formation_mail_pending' "$STALE_TMUX_LOG"
  if grep -Fq 'tmux set-option -p -u -t %171 @formation_mail_pending' "$STALE_TMUX_LOG"; then
    echo "FAIL: stale sibling pane badge was cleared by lead inbox" >&2
    exit 1
  fi
)

echo "test_requests: PASS"
