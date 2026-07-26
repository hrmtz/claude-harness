#!/usr/bin/env bash
# Durable semantic ASK/ACK state (#136). Transport receipt remains separate.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE="$(mktemp -d)"
cleanup() { rm -rf "$FIXTURE"; }
trap cleanup EXIT

export FORMATION_HOME="$FIXTURE/formation-home"
export FORMATION_MAILBOX="$FORMATION_HOME/mailbox/log.jsonl"
export FORMATION_REQUEST_DIR="$FORMATION_HOME/requests"
export FORMATION_REQUEST_LOG="$FORMATION_REQUEST_DIR/events.jsonl"
export FORMATION_SELF=worker-a
export FORMATION_PARENT=parent-a
unset TMUX_PANE

# shellcheck source=/dev/null
source "$HERE/../bin/formation"

request_id="$(cmd_ask 'Choose rollout A or B')"
[[ "$request_id" == req-* ]]
[[ "$(request_unresolved worker-a parent-a | jq -r '.request_id')" == "$request_id" ]]
[[ "$(jq -Rsc '[splits("\n") | fromjson? | select(.body | startswith("[ASK "))] | length' "$MAILBOX_LOG")" -eq 1 ]]

# Exact unresolved retries are idempotent in both semantic state and transport.
retry_id="$(cmd_ask 'Choose rollout A or B')"
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
request_current_one "$request_id" | jq -e \
  '.closed == true and .state == "RUNNING" and .event == "ack"' >/dev/null
ack_rows="$(jq -Rsc --arg id "[ACK request_id=$request_id]" \
  '[splits("\n") | fromjson? | select((.body // "") | startswith($id))] | length' "$MAILBOX_LOG")"
[[ "$ack_rows" -eq 1 ]]

# Duplicate ACK is a no-op and produces no duplicate worker notification.
cmd_ack "$request_id" 'review started' | grep -Fq 'notified=false'
[[ "$(jq -Rsc --arg id "[ACK request_id=$request_id]" \
  '[splits("\n") | fromjson? | select((.body // "") | startswith($id))] | length' "$MAILBOX_LOG")" -eq 1 ]]

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

echo "test_requests: PASS"
