#!/usr/bin/env bash
# claude-harness#335: a Codex tool shell can inherit another worker's
# FORMATION_SELF / FORMATION_PARENT / FORMATION_SESSION_ID / TMUX_PANE from the
# shared app-server daemon. Formation must send, read and ASK as the worker
# whose pane the caller really runs in, and must refuse when a daemon-served
# shell could belong to more than one worker.
set -euo pipefail
trap 'echo "FAIL: test_codex_identity_leak line $LINENO" >&2' ERR
HERE="$(cd "$(dirname "$0")" && pwd)"
CLI="$HERE/../bin/formation"
SEND="$HERE/../bin/mailbox-send"
HOOK="$HERE/../../harness-core/hooks/codex_tmux_self_name.sh"
FIXTURE="$(mktemp -d)"
SOCKET="formation-leak-$$"
cleanup() {
  tmux -L "$SOCKET" kill-server >/dev/null 2>&1 || true
  rm -r "$FIXTURE"
}
trap cleanup EXIT
export FORMATION_HOME="$FIXTURE/home"
export FORMATION_MAILBOX="$FORMATION_HOME/mailbox/log.jsonl"
export FORMATION_REQUEST_DIR="$FORMATION_HOME/requests"
export FORMATION_REQUEST_LOG="$FORMATION_REQUEST_DIR/events.jsonl"
unset FORMATION_SELF FORMATION_PARENT FORMATION_PARENT_PANE FORMATION_SESSION_ID \
  FORMATION_REGISTRY CODEX_THREAD_ID
REGISTRY="$FORMATION_HOME/formation/registry.jsonl"
mkdir -p "$(dirname "$REGISTRY")" "$(dirname "$FORMATION_MAILBOX")"
: >"$FORMATION_MAILBOX"

tmux -L "$SOCKET" -f /dev/null new-session -d -s fixture 'sleep 300'
export TMUX="$(tmux -L "$SOCKET" display-message -p '#{socket_path},#{pid},0')"
export TMUX_PANE=""
pane_a="$(tmux list-panes -t fixture -F '#{pane_id}')"
pane_b="$(tmux new-window -d -P -F '#{pane_id}' -t fixture 'sleep 300')"
pane_lead="$(tmux new-window -d -P -F '#{pane_id}' -t fixture 'sleep 300')"
tmux set-option -p -t "$pane_a" @formation_identity_locked worker-a
tmux set-option -p -t "$pane_b" @formation_identity_locked worker-b
# A lead pane carries a lock from its own session-start hook but is not a
# registered worker.
tmux set-option -p -t "$pane_lead" @formation_identity_locked lead-now

row() { # id pane parent sid
  jq -cn --arg id "$1" --arg pane "$2" --arg parent "$3" --arg sid "$4" \
    --arg lead "$pane_lead" \
    '{id: $id, pane_id: $pane, session_name: ("codex-" + $id), cli: "codex",
      session_id: $sid, parent_id: $parent, parent_pane: $lead,
      exclusive_input: false, parent_route_state: "ROUTABLE"}'
}
row worker-a "$pane_a" lead-x sid-a >>"$REGISTRY"
row worker-b "$pane_b" lead-y sid-b >>"$REGISTRY"

# Run a script as the process of a fixture pane and wait for its exit status.
run_in_pane() { # pane label  (script at $FIXTURE/<label>.sh)
  local pane="$1" label="$2"
  rm -f "$FIXTURE/$label.rc"
  tmux respawn-pane -k -t "$pane" \
    -e "FORMATION_HOME=$FORMATION_HOME" -e "FORMATION_MAILBOX=$FORMATION_MAILBOX" \
    -e "FORMATION_REQUEST_DIR=$FORMATION_REQUEST_DIR" \
    -e "FORMATION_REQUEST_LOG=$FORMATION_REQUEST_LOG" \
    "bash '$FIXTURE/$label.sh' >'$FIXTURE/$label.out' 2>'$FIXTURE/$label.err'; echo \$? >'$FIXTURE/$label.rc'; sleep 300"
  for _ in {1..400}; do
    [[ -s "$FIXTURE/$label.rc" ]] && return 0
    sleep 0.05
  done
  echo "timeout waiting for $label" >&2
  return 1
}
# The environment a second Codex session inherits from the shared daemon:
# everything names worker-a although the shell runs in worker-b's pane.
poison() {
  cat <<SH
export FORMATION_SELF=worker-a FORMATION_PARENT=lead-x FORMATION_SESSION_ID=sid-a
export FORMATION_PARENT_PANE=$pane_lead TMUX_PANE=$pane_a
SH
}
# `! cmd` never trips errexit, so negative assertions go through this.
refute() { if "$@" >/dev/null 2>&1; then echo "FAIL: expected failure: $*" >&2; exit 1; fi; }
last_row() { tail -n1 "$FORMATION_MAILBOX"; }
rows() { wc -l <"$FORMATION_MAILBOX"; }

# --- 1. send: the verified pane's worker lock outranks the inherited env ------
{ poison; echo "bash '$CLI' report 'poisoned report'"; } >"$FIXTURE/send.sh"
run_in_pane "$pane_b" send
last_row | jq -e '.from == "worker-b" and .to == "lead-y" and .session_id == "sid-b"
                  and .identity_note == "env-claimed:worker-a"' >/dev/null
grep -Fq 'worker-a' "$FIXTURE/send.err"
grep -Fq 'worker-b' "$FIXTURE/send.err"

# mailbox-send resolves the sender the same way.
{ poison; echo "bash '$SEND' '$pane_lead' 'poisoned send' --no-nudge"; } >"$FIXTURE/send2.sh"
run_in_pane "$pane_b" send2
[[ "$(cat "$FIXTURE/send2.rc")" == 0 ]]
last_row | jq -e '.from == "worker-b" and .identity_note == "env-claimed:worker-a"' >/dev/null

# --- 2. inbox: read the verified worker's mail, leave the claimed one alone ---
FORMATION_SELF=lead-x bash "$CLI" msg worker-a 'mail for a' >/dev/null 2>&1 || true
FORMATION_SELF=lead-y bash "$CLI" msg worker-b 'mail for b' >/dev/null 2>&1 || true
{ poison; echo "bash '$CLI' inbox"; } >"$FIXTURE/inbox.sh"
run_in_pane "$pane_b" inbox
grep -Fq 'mail for b' "$FIXTURE/inbox.out"
refute grep -Fq 'mail for a' "$FIXTURE/inbox.out"
# worker-a's own inbox is still unread.
echo "bash '$CLI' inbox" >"$FIXTURE/inbox_a.sh"
run_in_pane "$pane_a" inbox_a
grep -Fq 'mail for a' "$FIXTURE/inbox_a.out"

# --- 3. ASK: the request belongs to the verified worker ----------------------
{ poison; echo "bash '$CLI' ask 'poisoned question'"; } >"$FIXTURE/ask.sh"
run_in_pane "$pane_b" ask
request_id="$(grep -Eo 'req-[a-f0-9]+' "$FIXTURE/ask.out" | tail -n1)"
[[ -n "$request_id" ]]
jq -se --arg id "$request_id" \
  'map(select(.request_id == $id)) | length > 0
   and all(.worker_id == "worker-b" and .parent_id == "lead-y")' \
  "$FORMATION_REQUEST_LOG" >/dev/null
refute jq -e 'select(.worker_id == "worker-a")' "$FORMATION_REQUEST_LOG"

# --- 4. operator recovery: env still wins in a pane without a worker lock -----
# SKILL.md: FORMATION_SELF=<original-parent-id> formation ack <request-id>.
cat >"$FIXTURE/recover.sh" <<SH
FORMATION_SELF=lead-y bash '$CLI' ack '$request_id' 'recovered'
SH
run_in_pane "$pane_lead" recover
grep -Fq "request=$request_id" "$FIXTURE/recover.out"
grep -Fq 'state=' "$FIXTURE/recover.out"
refute grep -Fq 'REFUSED' "$FIXTURE/recover.err"

# --- 5. daemon-served shell ---------------------------------------------------
# Two session leaders between the caller and the terminal = the caller is
# served by a detached server, so the pane it descends from proves nothing.
detached() { printf "setsid -w setsid -w bash %s\n" "$1"; }
before="$(rows)"
{ poison; detached "'$CLI' report 'ambiguous report'"; } >"$FIXTURE/amb.sh"
run_in_pane "$pane_b" amb
[[ "$(cat "$FIXTURE/amb.rc")" == 8 ]]
[[ "$(rows)" == "$before" ]]
grep -Fq -- '--no-daemon' "$FIXTURE/amb.err"
{ poison; detached "'$CLI' inbox"; } >"$FIXTURE/amb_inbox.sh"
run_in_pane "$pane_b" amb_inbox
[[ "$(cat "$FIXTURE/amb_inbox.rc")" == 8 ]]
refute grep -Fq 'mail for' "$FIXTURE/amb_inbox.out"
{ poison; detached "'$CLI' ask 'ambiguous question'"; } >"$FIXTURE/amb_ask.sh"
run_in_pane "$pane_b" amb_ask
[[ "$(cat "$FIXTURE/amb_ask.rc")" == 8 ]]
refute jq -e 'select(.question == "ambiguous question")' "$FORMATION_REQUEST_LOG"

# One daemon-era Codex worker left (the other was spawned isolated): the
# caller is unambiguous, so it keeps working, loudly.
tmux set-option -p -t "$pane_b" @formation_codex_isolated 1
{ poison; detached "'$CLI' report 'lone daemon report'"; } >"$FIXTURE/lone.sh"
run_in_pane "$pane_a" lone
[[ "$(cat "$FIXTURE/lone.rc")" != 8 ]]
last_row | jq -e '.from == "worker-a" and .body == "lone daemon report"
                  and .identity_note == "unverified:codex-shared-daemon"' >/dev/null
grep -Fq 'shared' "$FIXTURE/lone.err"

# register never accepts a daemon-served shell.
pane_new="$(tmux new-window -d -P -F '#{pane_id}' -t fixture 'sleep 300')"
{ echo "export TMUX_PANE=$pane_new"; detached "'$CLI' register --cli codex newbie"; } >"$FIXTURE/reg.sh"
run_in_pane "$pane_new" reg
[[ "$(cat "$FIXTURE/reg.rc")" == 8 ]]
grep -Fq -- '--no-daemon' "$FIXTURE/reg.err"
refute jq -e 'select(.id == "newbie")' "$REGISTRY"

# --- 6. spawn isolates Codex workers ------------------------------------------
[[ "$(bash -c "source '$CLI'; codex_isolation_flags")" == "--no-daemon" ]]
grep -Fq '$(codex_isolation_flags)' "$CLI"
grep -Fq '@formation_codex_isolated 1' "$CLI"

# --- 7. session-start hook does not assert an anchor from a daemon ------------
{ poison; echo "printf '{}' | setsid -w setsid -w bash '$HOOK'"; } >"$FIXTURE/hook.sh"
run_in_pane "$pane_b" hook
grep -Fq 'cannot be verified' "$FIXTURE/hook.out"
refute grep -Fq 'worker-a' "$FIXTURE/hook.out"
[[ "$(tmux show-options -pqv -t "$pane_a" @formation_identity_locked)" == worker-a ]]

echo "PASS: test_codex_identity_leak"
