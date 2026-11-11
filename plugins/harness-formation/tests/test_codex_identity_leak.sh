#!/usr/bin/env bash
# claude-harness#335: a Codex tool shell can inherit another worker's
# FORMATION_SELF / FORMATION_PARENT / FORMATION_SESSION_ID / TMUX_PANE from the
# shared app-server daemon. Formation must act as the worker whose pane the
# caller provably runs in, and must refuse every caller it cannot prove.
set -euo pipefail
trap 'echo "FAIL: test_codex_identity_leak line $LINENO" >&2' ERR
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
CLI="$HERE/../bin/formation"
SEND="$HERE/../bin/mailbox-send"
HOOK="$HERE/../../harness-core/hooks/codex_tmux_self_name.sh"
FIXTURE="$(mktemp -d)"
SOCKET="formation-leak-$$"
cleanup() {
  # The spawn in section 7 starts a real relay for its worker.
  local relay_pid
  relay_pid="$(cat "$FIXTURE"/home/formation/spawned-codex.relay*pid 2>/dev/null | head -n1 || true)"
  if [[ "$relay_pid" =~ ^[0-9]+$ ]]; then
    pkill -P "$relay_pid" 2>/dev/null || true
    kill "$relay_pid" 2>/dev/null || true
  fi
  tmux -L "$SOCKET" kill-server >/dev/null 2>&1 || true
  rm -r "$FIXTURE"
}
trap cleanup EXIT
export FORMATION_HOME="$FIXTURE/home"
export FORMATION_MAILBOX="$FORMATION_HOME/mailbox/log.jsonl"
export FORMATION_REQUEST_DIR="$FORMATION_HOME/requests"
export FORMATION_REQUEST_LOG="$FORMATION_REQUEST_DIR/events.jsonl"
unset FORMATION_SELF FORMATION_PARENT FORMATION_PARENT_PANE FORMATION_SESSION_ID \
  FORMATION_REGISTRY CODEX_THREAD_ID MAILBOX_IDENTITY_GATED MAILBOX_IDENTITY_NOTE
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

row() { # id pane parent sid goal
  jq -cn --arg id "$1" --arg pane "$2" --arg parent "$3" --arg sid "$4" \
    --arg goal "$5" --arg lead "$pane_lead" \
    '{id: $id, pane_id: $pane, session_name: ("codex-" + $id), cli: "codex",
      session_id: $sid, parent_id: $parent, parent_pane: $lead, goal: $goal,
      exclusive_input: false, parent_route_state: "ROUTABLE"}'
}
# A half-written line must not make the worker lookup fail open (review P1-4).
printf '%s\n' '{"id": "worker-b", "pane_id": ' >>"$REGISTRY"
row worker-a "$pane_a" lead-x sid-a goal-of-a >>"$REGISTRY"
row worker-b "$pane_b" lead-y sid-b goal-of-b >>"$REGISTRY"

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
rc_of() { cat "$FIXTURE/$1.rc"; }
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

# --- 1. send: the proven pane's worker row outranks the inherited env ---------
{ poison; echo "bash '$CLI' report 'poisoned report'"; } >"$FIXTURE/send.sh"
run_in_pane "$pane_b" send
last_row | jq -e '.from == "worker-b" and .to == "lead-y" and .session_id == "sid-b"
                  and .identity_note == "env-claimed:worker-a"' >/dev/null
grep -Fq 'worker-a' "$FIXTURE/send.err"
grep -Fq 'worker-b' "$FIXTURE/send.err"

# mailbox-send resolves the sender the same way.
{ poison; echo "bash '$SEND' '$pane_lead' 'poisoned send' --no-nudge"; } >"$FIXTURE/send2.sh"
run_in_pane "$pane_b" send2
[[ "$(rc_of send2)" == 0 ]]
last_row | jq -e '.from == "worker-b" and .identity_note == "env-claimed:worker-a"' >/dev/null

# Review P2-5: a correct or missing FORMATION_SELF must not leave the other
# worker's parent and session id in place; the row supplies all of them.
cat >"$FIXTURE/half.sh" <<SH
export FORMATION_SELF=worker-b FORMATION_PARENT=lead-x FORMATION_SESSION_ID=sid-a
bash '$CLI' report 'half poisoned'
unset FORMATION_SELF
bash '$CLI' report 'self unset'
SH
run_in_pane "$pane_b" half
tail -n2 "$FORMATION_MAILBOX" | jq -se 'length == 2 and all(.from == "worker-b"
  and .to == "lead-y" and .session_id == "sid-b" and (has("identity_note") | not))' >/dev/null

# The supported "who am I" answers from the proven pane, not from the env.
{ poison; echo "bash '$CLI' whoami"; } >"$FIXTURE/whoami.sh"
run_in_pane "$pane_b" whoami
grep -q "^worker-b pane=$pane_b " "$FIXTURE/whoami.out"

# Library use (a script that sources formation and never reaches the
# dispatcher) is gated by self_id itself.
{ poison; echo "bash -c \"source '$CLI'; self_id\""; } >"$FIXTURE/lib.sh"
run_in_pane "$pane_b" lib
grep -Fxq worker-b "$FIXTURE/lib.out"

# --- 2. inbox: read the proven worker's mail, leave the claimed one alone -----
FORMATION_SELF=lead-x bash "$CLI" msg worker-a 'mail for a' >/dev/null 2>&1 || true
FORMATION_SELF=lead-y bash "$CLI" msg worker-b 'mail for b' >/dev/null 2>&1 || true
{ poison; echo "bash '$CLI' inbox"; } >"$FIXTURE/inbox.sh"
run_in_pane "$pane_b" inbox
grep -Fq 'mail for b' "$FIXTURE/inbox.out"
refute grep -Fq 'mail for a' "$FIXTURE/inbox.out"

# --- 3. ASK: the request belongs to the proven worker -------------------------
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
bash '$SEND' '$pane_a' 'operator send' --from lead-x --no-nudge
SH
run_in_pane "$pane_lead" recover
grep -Fq "request=$request_id" "$FIXTURE/recover.out"
refute grep -Fq 'REFUSED' "$FIXTURE/recover.err"
last_row | jq -e '.from == "lead-x" and .body == "operator send"' >/dev/null

# Review P1-3: --from is not a way around the gate from a worker's pane.
before="$(rows)"
echo "bash '$SEND' '$pane_lead' 'forged' --from worker-a --no-nudge" >"$FIXTURE/forge.sh"
run_in_pane "$pane_b" forge
[[ "$(rc_of forge)" == 8 ]]
[[ "$(rows)" == "$before" ]]
echo "bash '$SEND' '$pane_lead' 'own name' --from worker-b --no-nudge" >"$FIXTURE/own.sh"
run_in_pane "$pane_b" own
[[ "$(rc_of own)" == 0 ]]

# --- 5. a caller that cannot be proven is refused, whatever the registry says -
# Two foreign process sessions between the caller and the pane = a detached
# server sits in between (the shared Codex daemon). The daemon descends from
# the pane that started it, so here the env even agrees with that pane.
detached() { printf "setsid -w setsid -w %s\n" "$1"; }
a_env="export FORMATION_SELF=worker-a FORMATION_PARENT=lead-x FORMATION_SESSION_ID=sid-a TMUX_PANE=$pane_a"
refused() { # label command  -> runs from worker-a's pane through a daemon chain
  local before; before="$(rows)"
  { echo "$a_env"; detached "$2"; } >"$FIXTURE/$1.sh"
  run_in_pane "$pane_a" "$1"
  [[ "$(rc_of "$1")" == 8 ]] || { echo "FAIL: $1 rc=$(rc_of "$1")" >&2; exit 1; }
  [[ "$(rows)" == "$before" ]]
  cat "$FIXTURE/$1.err" "$FIXTURE/$1.out" | grep -Fq -- '--no-daemon'  # a PTY merges stderr into stdout
}
refused d_report "bash '$CLI' report 'daemon report'"
refused d_inbox "bash '$CLI' inbox --history"
refute grep -Fq 'mail for' "$FIXTURE/d_inbox.out"
refused d_ask "bash '$CLI' ask 'daemon question'"
refute jq -e 'select(.question == "daemon question")' "$FORMATION_REQUEST_LOG"
refused d_send "bash '$SEND' '$pane_lead' 'daemon send' --no-nudge"
refused d_from "bash '$SEND' '$pane_lead' 'daemon send' --from worker-a --no-nudge"
refused d_whoami "bash '$CLI' whoami"

# Review P1-1: a tool shell that was given its own PTY is still daemon-served.
refused d_pty "script -qec \"bash '$CLI' report 'pty report'\" /dev/null"

# Review P1-2: one live worker left is no proof either. worker-b's pane is
# gone; the daemon shell still carries worker-a's environment.
tmux kill-pane -t "$pane_b"
refused d_lone "bash '$CLI' report 'lone daemon report'"

# register never accepts such a shell.
pane_new="$(tmux new-window -d -P -F '#{pane_id}' -t fixture 'sleep 300')"
before="$(rows)"
{ echo "export TMUX_PANE=$pane_new"; detached "bash '$CLI' register --cli codex newbie"; } >"$FIXTURE/reg.sh"
run_in_pane "$pane_new" reg
[[ "$(rc_of reg)" == 8 ]]
refute jq -e 'select(.id == "newbie")' "$REGISTRY"

# Review P1-4: a Codex tool shell with no provable pane (daemon outlived its
# pane, tmux unreachable) is refused instead of trusted on its environment...
if CODEX_THREAD_ID=thread-x FORMATION_SELF=worker-a TMUX="$FIXTURE/no-such-socket,1,0" \
     bash "$CLI" report 'orphan daemon report' >"$FIXTURE/orphan.out" 2>"$FIXTURE/orphan.err"; then
  echo "FAIL: orphan Codex shell was not refused" >&2; exit 1
else
  [[ "$?" == 8 ]]
fi
[[ "$(rows)" == "$before" ]]
# ...while an operator's plain shell outside tmux keeps the explicit override.
FORMATION_SELF=lead-x TMUX="$FIXTURE/no-such-socket,1,0" \
  bash "$SEND" "$pane_a" 'operator outside tmux' --no-nudge >/dev/null
last_row | jq -e '.from == "lead-x" and .body == "operator outside tmux"' >/dev/null

# --- 6. other consumers of the inherited environment --------------------------
pane_b="$(tmux new-window -d -P -F '#{pane_id}' -t fixture 'sleep 300')"
tmux set-option -p -t "$pane_b" @formation_identity_locked worker-b
row worker-b "$pane_b" lead-y sid-b goal-of-b >>"$REGISTRY"
mkdir -p "$FIXTURE/fakebin"
cat >"$FIXTURE/fakebin/python3" <<SH
#!/bin/bash
printf '%s\n' "\${FORMATION_SESSION_ID:-<unset>}" >"$FIXTURE/receipt.sid"
SH
chmod +x "$FIXTURE/fakebin/python3"
{
  poison
  echo "bash '$HERE/../bin/formation-statusline' </dev/null"
  echo "PATH='$FIXTURE/fakebin':\$PATH bash '$HERE/../hooks/pr_receipt.sh' </dev/null"
  echo "python3 '$HERE/../bin/formation-window-status' apply --task 'task typed in b'"
} >"$FIXTURE/consumers.sh"
run_in_pane "$pane_b" consumers
grep -Fq 'goal-of-b' "$FIXTURE/consumers.out"
refute grep -Fq 'goal-of-a' "$FIXTURE/consumers.out"
[[ "$(cat "$FIXTURE/receipt.sid")" == sid-b ]]
[[ "$(tmux show-options -pqv -t "$pane_b" @harness_task)" == 'task typed in b' ]]
[[ -z "$(tmux show-options -pqv -t "$pane_a" @harness_task)" ]]
# From a daemon chain they stay silent rather than show another worker's data.
rm -f "$FIXTURE/receipt.sid"
{
  echo "$a_env"
  detached "bash '$HERE/../bin/formation-statusline' </dev/null"
  echo "export PATH='$FIXTURE/fakebin':\$PATH"
  detached "bash '$HERE/../hooks/pr_receipt.sh' </dev/null"
} >"$FIXTURE/d_consumers.sh"
run_in_pane "$pane_a" d_consumers
[[ ! -s "$FIXTURE/d_consumers.out" && ! -e "$FIXTURE/receipt.sid" ]]

# Agent instructions must not teach the poisoned lookup (review P2-7).
for doc in "$ROOT/AGENTS.md" "$ROOT/plugins/harness-kimi/AGENTS.md.template"; do
  grep -Fq 'formation whoami' "$doc"
  refute grep -Fq 'display-message -p -t "$TMUX_PANE" '"'"'#{?#{@formation_identity_locked}' "$doc"
done

# --- 7. spawn isolates Codex workers ------------------------------------------
# Real spawn from the lead's pane with stub launchers: the command typed into
# the new pane must carry --no-daemon.
cat >"$FIXTURE/guard" <<SH
#!/bin/bash
printf '%s\n' "\$@" >"$FIXTURE/launch.args"
sleep 300
SH
printf '%s\n' '#!/bin/bash' 'exit 0' >"$FIXTURE/cache-safe"
chmod +x "$FIXTURE/guard" "$FIXTURE/cache-safe"
printf '# probe\n\n## Mission\nnothing\n' >"$FIXTURE/brief.md"
cat >"$FIXTURE/spawn.sh" <<SH
export HARNESS_CROSS_CLI_GUARD='$FIXTURE/guard' CODEX_CACHE_SAFE_BIN='$FIXTURE/cache-safe'
export FORMATION_RELAY_LOG_DIR='$FIXTURE'
bash '$CLI' spawn --cli codex '$FIXTURE/brief.md' spawned-codex
SH
# From a daemon chain spawn is refused before anything is created.
{
  echo "$a_env"
  echo "export HARNESS_CROSS_CLI_GUARD='$FIXTURE/guard' CODEX_CACHE_SAFE_BIN='$FIXTURE/cache-safe'"
  detached "bash '$CLI' spawn --cli codex '$FIXTURE/brief.md' ghost-codex"
} >"$FIXTURE/d_spawn.sh"
run_in_pane "$pane_a" d_spawn
[[ "$(rc_of d_spawn)" == 8 ]]
refute jq -e 'select(.id == "ghost-codex")' "$REGISTRY"
[[ ! -e "$FIXTURE/launch.args" ]]

run_in_pane "$pane_lead" spawn
for _ in {1..100}; do [[ -s "$FIXTURE/launch.args" ]] && break; sleep 0.05; done
grep -Fxq -- '--no-daemon' "$FIXTURE/launch.args"
grep -Fxq "$FIXTURE/cache-safe" "$FIXTURE/launch.args"

# --- 8. session-start hook assigns nothing from a daemon ----------------------
{ echo "$a_env"; detached "bash -c \"printf '{}' | bash '$HOOK'\""; } >"$FIXTURE/hook.sh"
run_in_pane "$pane_a" hook
grep -Fq 'cannot be verified' "$FIXTURE/hook.out"
refute grep -Fq 'worker-a' "$FIXTURE/hook.out"
{ echo "$a_env"; detached "script -qec \"printf '{}' | bash '$HOOK'\" /dev/null"; } >"$FIXTURE/hook_pty.sh"
run_in_pane "$pane_a" hook_pty
grep -Fq 'cannot be verified' "$FIXTURE/hook_pty.out"

echo "PASS: test_codex_identity_leak"
