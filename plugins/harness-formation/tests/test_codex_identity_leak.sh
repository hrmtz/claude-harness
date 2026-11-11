#!/usr/bin/env bash
# claude-harness#335: a Codex tool shell can inherit another worker's
# FORMATION_SELF / FORMATION_PARENT / FORMATION_SESSION_ID / TMUX_PANE from the
# shared app-server daemon. Formation must act as the worker whose pane the
# caller provably runs in, and must refuse every caller it cannot prove.
#
# Fixture mode (FORMATION_TEST_FIXTURE=1, see mailbox_test_fixture_active)
# points the gate at this test's registry and at tmux sockets under
# $FORMATION_HOME/tmux. FORMATION_TEST_OPERATOR is deliberately NOT set
# globally here: commands run inside fixture panes get the strict rules.
set -euo pipefail
trap 'echo "FAIL: test_codex_identity_leak line $LINENO" >&2' ERR
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
CLI="$HERE/../bin/formation"
SEND="$HERE/../bin/mailbox-send"
HOOK="$HERE/../../harness-core/hooks/codex_tmux_self_name.sh"
FIXTURE="$(mktemp -d)"
export FORMATION_HOME="$FIXTURE/home"
SOCK="$FORMATION_HOME/tmux/sock"
cleanup() {
  # The spawn in section 8 starts a real relay for its worker.
  local relay_pid
  relay_pid="$(cat "$FIXTURE"/home/formation/spawned-codex.relay*pid 2>/dev/null | head -n1 || true)"
  if [[ "$relay_pid" =~ ^[0-9]+$ ]]; then
    pkill -P "$relay_pid" 2>/dev/null || true
    kill "$relay_pid" 2>/dev/null || true
  fi
  command tmux -S "$SOCK" kill-server >/dev/null 2>&1 || true
  rm -r "$FIXTURE"
}
trap cleanup EXIT
export FORMATION_TEST_FIXTURE=1
export FORMATION_MAILBOX="$FORMATION_HOME/mailbox/log.jsonl"
export FORMATION_REQUEST_DIR="$FORMATION_HOME/requests"
export FORMATION_REQUEST_LOG="$FORMATION_REQUEST_DIR/events.jsonl"
unset FORMATION_SELF FORMATION_PARENT FORMATION_PARENT_PANE FORMATION_SESSION_ID \
  FORMATION_REGISTRY FORMATION_TEST_OPERATOR CODEX_THREAD_ID TMUX TMUX_PANE \
  MAILBOX_IDENTITY_NOTE _MAILBOX_GATE_PASSED
REGISTRY="$FORMATION_HOME/formation/registry.jsonl"
mkdir -p "$(dirname "$REGISTRY")" "$(dirname "$FORMATION_MAILBOX")" "$(dirname "$SOCK")"
: >"$FORMATION_MAILBOX"

command tmux -S "$SOCK" -f /dev/null new-session -d -s fixture 'sleep 300'
export TMUX="$(command tmux -S "$SOCK" display-message -p '#{socket_path},#{pid},0')"
pane_a="$(tmux list-panes -t fixture -F '#{pane_id}')"
new_pane() { tmux new-window -d -P -F '#{pane_id}' -t fixture 'sleep 300'; }
worker_pane() { # pane id parent  -> the options formation spawn sets
  tmux set-option -p -t "$1" @formation_identity_locked "$2"
  tmux set-option -p -t "$1" @formation_parent_id "$3"
}
pane_b="$(new_pane)"
pane_lead="$(new_pane)"
worker_pane "$pane_a" worker-a lead-x
worker_pane "$pane_b" worker-b lead-y
# A lead pane carries a lock from its own session-start hook but is not a
# worker: no registry row, no parent marker.
tmux set-option -p -t "$pane_lead" @formation_identity_locked lead-now

row() { # id pane parent sid goal
  jq -cn --arg id "$1" --arg pane "$2" --arg parent "$3" --arg sid "$4" \
    --arg goal "$5" --arg lead "$pane_lead" \
    '{id: $id, pane_id: $pane, session_name: ("codex-" + $id), cli: "codex",
      session_id: $sid, parent_id: $parent, parent_pane: $lead, goal: $goal,
      exclusive_input: false, parent_route_state: "ROUTABLE"}'
}
row worker-a "$pane_a" lead-x sid-a goal-of-a >>"$REGISTRY"
row worker-b "$pane_b" lead-y sid-b goal-of-b >>"$REGISTRY"

# Run a script as the process of a fixture pane and wait for its exit status.
run_in_pane() { # pane label  (script at $FIXTURE/<label>.sh)
  local pane="$1" label="$2"
  rm -f "$FIXTURE/$label.rc"
  tmux respawn-pane -k -t "$pane" \
    -e "FORMATION_HOME=$FORMATION_HOME" -e "FORMATION_MAILBOX=$FORMATION_MAILBOX" \
    -e "FORMATION_REQUEST_DIR=$FORMATION_REQUEST_DIR" \
    -e "FORMATION_REQUEST_LOG=$FORMATION_REQUEST_LOG" -e "FORMATION_TEST_FIXTURE=1" \
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
# A command that must be refused (exit 8) without appending anything.
refused_in() { # pane label script-line...
  local pane="$1" label="$2" before; shift 2
  before="$(rows)"
  printf '%s\n' "$@" >"$FIXTURE/$label.sh"
  run_in_pane "$pane" "$label"
  [[ "$(rc_of "$label")" == 8 ]] || { echo "FAIL: $label rc=$(rc_of "$label")" >&2; cat "$FIXTURE/$label.err" >&2; exit 1; }
  [[ "$(rows)" == "$before" ]] || { echo "FAIL: $label appended a row" >&2; exit 1; }
}
# The test driver itself talks to the fixture server from outside its panes.
driver() { FORMATION_TEST_OPERATOR=1 "$@"; }

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

# A correct or missing FORMATION_SELF must not leave the other worker's parent
# and session id in place; the row supplies all of them.
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
grep -Fxq "worker-b pane=$pane_b context=worker" "$FIXTURE/whoami.out"

# Sourced use (review r2 #3): the gate runs in the caller's shell, so the
# corrected parent and session reach the append, not only the sender.
{ poison; echo "bash -c \"source '$CLI'; cmd_report 'sourced report'\""; } >"$FIXTURE/lib.sh"
run_in_pane "$pane_b" lib
last_row | jq -e '.body == "sourced report" and .from == "worker-b" and .to == "lead-y"
                  and .session_id == "sid-b"' >/dev/null
# self_id never resolves an identity when the gate has not run in this shell,
# and a pass marker that arrives through the environment is not one (r2 #4).
refused_in "$pane_b" lib_self "$(poison)" \
  "export _MAILBOX_GATE_PASSED=1 MAILBOX_IDENTITY_GATED=1" \
  "bash -c \"source '$CLI'; self_id\""
[[ ! -s "$FIXTURE/lib_self.out" ]]

# --- 2. inbox: read the proven worker's mail, leave the claimed one alone -----
driver env FORMATION_SELF=lead-x bash "$CLI" msg worker-a 'mail for a' >/dev/null 2>&1 || true
driver env FORMATION_SELF=lead-y bash "$CLI" msg worker-b 'mail for b' >/dev/null 2>&1 || true
[[ "$(grep -c 'mail for' "$FORMATION_MAILBOX")" == 2 ]]
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

# --- 4. lead status is a positive finding (rule A) ----------------------------
# Operator recovery from SKILL.md still works from a proven lead pane: lock
# read, registry read, no worker row, no worker marker.
cat >"$FIXTURE/recover.sh" <<SH
FORMATION_SELF=lead-y bash '$CLI' ack '$request_id' 'recovered'
bash '$SEND' '$pane_a' 'operator send' --from lead-x --no-nudge
bash '$CLI' whoami
SH
run_in_pane "$pane_lead" recover
grep -Fq "request=$request_id" "$FIXTURE/recover.out"
refute grep -Fq 'REFUSED' "$FIXTURE/recover.err"
grep -Fxq "lead-now pane=$pane_lead context=lead" "$FIXTURE/recover.out"
last_row | jq -e '.from == "lead-x" and .body == "operator send"' >/dev/null

# --from is not a way around the gate from a worker's pane.
forge="bash '$SEND' '$pane_lead' 'forged' --from worker-a --no-nudge"
refused_in "$pane_b" forge "$forge"
echo "bash '$SEND' '$pane_lead' 'own name' --from worker-b --no-nudge" >"$FIXTURE/own.sh"
run_in_pane "$pane_b" own
[[ "$(rc_of own)" == 0 ]]

# Review r2 #1: a worker pane must not fall through to lead status because its
# lock or its registry row cannot be found. Each case used to append as
# worker-a from worker-b's pane.
tmux set-option -p -u -t "$pane_b" @formation_identity_locked
refused_in "$pane_b" lock_gone "$forge"
tmux set-option -p -t "$pane_b" @formation_identity_locked somebody-else
refused_in "$pane_b" lock_changed "$forge"
tmux set-option -p -t "$pane_b" @formation_identity_locked worker-b
cp "$REGISTRY" "$FIXTURE/registry.good"
grep -v '"worker-b"' "$FIXTURE/registry.good" >"$REGISTRY"
refused_in "$pane_b" row_gone "$forge"
: >"$REGISTRY"
refused_in "$pane_b" registry_empty "$forge"
{ cat "$FIXTURE/registry.good"; printf '%s\n' '{"id": "half-written", "pane_id": '; } >"$REGISTRY"
refused_in "$pane_b" registry_corrupt "$(poison)" "bash '$CLI' report 'corrupt registry'"
refused_in "$pane_lead" registry_corrupt_lead "FORMATION_SELF=lead-x bash '$CLI' report 'corrupt registry'"
# A pane that the registry assigns to a worker is not a lead pane, whatever
# its lock says and even without the spawn marker.
{ cat "$FIXTURE/registry.good"; row hand-registered "$pane_lead" lead-x sid-h goal-h; } >"$REGISTRY"
refused_in "$pane_lead" row_other_id "FORMATION_SELF=lead-x bash '$CLI' report 'not a lead'"
cp "$FIXTURE/registry.good" "$REGISTRY"
# Rule B: outside fixture mode the registry is the canonical one, and a caller
# in a pane that points FORMATION_HOME / FORMATION_REGISTRY elsewhere is
# refused. (This is the reviewer's FORMATION_REGISTRY=/dev/null reproduction.)
refused_in "$pane_b" evidence "unset FORMATION_TEST_FIXTURE" \
  "FORMATION_REGISTRY=/dev/null $forge"
grep -Fq 'does not choose the registry' "$FIXTURE/evidence.err"

# Fixture mode is void as soon as one state path lies inside the canonical
# home (nothing is written by whoami; the path is never created).
canonical_home="$(getent passwd "$(id -u)" | cut -d: -f6)/.formation"
refused_in "$pane_lead" fixture_void \
  "FORMATION_REQUEST_DIR='$canonical_home/identity-leak-test-probe' bash '$CLI' whoami"
[[ ! -e "$canonical_home/identity-leak-test-probe" ]]
# The test-driver declaration is inert without an active fixture.
if env -u FORMATION_TEST_FIXTURE FORMATION_TEST_OPERATOR=1 FORMATION_SELF=lead-x \
     bash "$SEND" "$pane_a" 'driver without fixture' --no-nudge >/dev/null 2>&1; then
  echo "FAIL: FORMATION_TEST_OPERATOR worked without a fixture" >&2; exit 1
else
  [[ "$?" == 8 ]]
fi

# --- 5. no pane proof, no service - for every CLI type (rule A, r2 #2) --------
# None of these set CODEX_THREAD_ID: refusal must not depend on a marker the
# caller's environment supplies.
report="bash '$CLI' report 'unproven'"
refused_in "$pane_b" tmux_down "$(poison)" "TMUX='$FIXTURE/no-such-socket,1,0' $report"
mkdir -p "$FIXTURE/nops"
printf '%s\n' '#!/bin/bash' 'exit 1' >"$FIXTURE/nops/ps"
chmod +x "$FIXTURE/nops/ps"
refused_in "$pane_b" ps_down "$(poison)" "PATH='$FIXTURE/nops':\$PATH $report"
# ...also with TMUX unset: an unreadable process table cannot show that the
# caller is outside tmux, so it is not an operator.
refused_in "$pane_b" ps_down_no_tmux "$(poison)" "unset TMUX" "PATH='$FIXTURE/nops':\$PATH $report"
# Rule C: unsetting TMUX inside a pane is not "outside tmux". The pane is
# found through the socket directory and the mismatch is refused.
refused_in "$pane_b" tmux_unset "$(poison)" "env -u TMUX $report"
grep -Fq 'is unset' "$FIXTURE/tmux_unset.err"
# Rule D: holding the pane's terminal is not proof. An orphaned child keeps
# the controlling terminal but has no pane process among its ancestors.
cat >"$FIXTURE/orphan.sh" <<SH
$(poison)
(bash -c "sleep 0.4; bash '$CLI' report 'orphan report' 2>'$FIXTURE/orphan.inner.err'; echo \\\$? >'$FIXTURE/orphan.inner.rc'" &)
sleep 0.1
SH
before="$(rows)"
run_in_pane "$pane_b" orphan
for _ in {1..200}; do [[ -s "$FIXTURE/orphan.inner.rc" ]] && break; sleep 0.05; done
[[ "$(cat "$FIXTURE/orphan.inner.rc")" == 8 ]]
[[ "$(rows)" == "$before" ]]
# A real operator, in no pane at all and with TMUX unset, keeps the override.
env -u TMUX FORMATION_SELF=lead-x bash "$SEND" "$pane_a" 'operator outside tmux' --no-nudge >/dev/null
last_row | jq -e '.from == "lead-x" and .body == "operator outside tmux"' >/dev/null
# With TMUX set and no pane (and no test-driver declaration) it is refused.
if FORMATION_SELF=lead-x bash "$SEND" "$pane_a" 'tmux set, no pane' --no-nudge >/dev/null 2>&1; then
  echo "FAIL: caller with TMUX set and no pane was served" >&2; exit 1
else
  [[ "$?" == 8 ]]
fi

# --- 6. a detached server on the path -----------------------------------------
# Two foreign process sessions between the caller and the pane = a detached
# server sits in between (the shared Codex daemon). The daemon descends from
# the pane that started it, so here the env even agrees with that pane.
detached() { printf "setsid -w setsid -w %s\n" "$1"; }
a_env="export FORMATION_SELF=worker-a FORMATION_PARENT=lead-x FORMATION_SESSION_ID=sid-a TMUX_PANE=$pane_a"
refused() { # label command  -> runs from worker-a's pane through a daemon chain
  refused_in "$pane_a" "$1" "$a_env" "$(detached "$2")"
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
# A tool shell that was given its own PTY is still daemon-served.
refused d_pty "script -qec \"bash '$CLI' report 'pty report'\" /dev/null"
# Sourced use inside a condition (r2 #3): the refusal reaches the caller and
# nothing is appended with an empty sender.
refused_in "$pane_a" d_sourced "$a_env" \
  "$(detached "bash -c \"source '$CLI'; if cmd_report 'sourced daemon'; then exit 0; else exit \\\$?; fi\"")"
# A refused gate leaves no pass marker behind (r2 #4).
refused_in "$pane_a" d_marker "$a_env" \
  "$(detached "bash -c \"source '$CLI'; mailbox_guard_identity 2>/dev/null || :; self_id\"")"
# One live worker left is no proof either.
tmux kill-pane -t "$pane_b"
refused d_lone "bash '$CLI' report 'lone daemon report'"

# register never accepts such a shell.
pane_new="$(new_pane)"
refused_in "$pane_new" reg "export TMUX_PANE=$pane_new" \
  "$(detached "bash '$CLI' register --cli codex newbie")"
refute jq -e 'select(.id == "newbie")' "$REGISTRY"

# --- 7. other consumers of the inherited environment --------------------------
pane_b="$(new_pane)"
worker_pane "$pane_b" worker-b lead-y
grep -v '"worker-b"' "$FIXTURE/registry.good" >"$REGISTRY"
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

# Agent instructions must not teach the poisoned lookup.
for doc in "$ROOT/AGENTS.md" "$ROOT/plugins/harness-kimi/AGENTS.md.template"; do
  grep -Fq 'formation whoami' "$doc"
  refute grep -Fq 'display-message -p -t "$TMUX_PANE" '"'"'#{?#{@formation_identity_locked}' "$doc"
done

# --- 8. spawn isolates Codex workers ------------------------------------------
# Real spawn with stub launchers: the command typed into the new pane must
# carry --no-daemon.
cat >"$FIXTURE/guard" <<SH
#!/bin/bash
printf '%s\n' "\$@" >"$FIXTURE/launch.args"
sleep 300
SH
printf '%s\n' '#!/bin/bash' 'exit 0' >"$FIXTURE/cache-safe"
chmod +x "$FIXTURE/guard" "$FIXTURE/cache-safe"
printf '# probe\n\n## Mission\nnothing\n' >"$FIXTURE/brief.md"
stubs="export HARNESS_CROSS_CLI_GUARD='$FIXTURE/guard' CODEX_CACHE_SAFE_BIN='$FIXTURE/cache-safe' FORMATION_RELAY_LOG_DIR='$FIXTURE'"
# From a daemon chain spawn is refused before anything is created.
refused_in "$pane_a" d_spawn "$a_env" "$stubs" \
  "$(detached "bash '$CLI' spawn --cli codex '$FIXTURE/brief.md' ghost-codex")"
refute jq -e 'select(.id == "ghost-codex")' "$REGISTRY"
[[ ! -e "$FIXTURE/launch.args" ]]

printf '%s\n' "$stubs" "bash '$CLI' spawn --cli codex '$FIXTURE/brief.md' spawned-codex" >"$FIXTURE/spawn.sh"
run_in_pane "$pane_lead" spawn
for _ in {1..100}; do [[ -s "$FIXTURE/launch.args" ]] && break; sleep 0.05; done
grep -Fxq -- '--no-daemon' "$FIXTURE/launch.args"
grep -Fxq "$FIXTURE/cache-safe" "$FIXTURE/launch.args"

# --- 9. session-start hook assigns nothing from a daemon ----------------------
{ echo "$a_env"; detached "bash -c \"printf '{}' | bash '$HOOK'\""; } >"$FIXTURE/hook.sh"
run_in_pane "$pane_a" hook
grep -Fq 'cannot be verified' "$FIXTURE/hook.out"
refute grep -Fq 'worker-a' "$FIXTURE/hook.out"
{ echo "$a_env"; detached "script -qec \"printf '{}' | bash '$HOOK'\" /dev/null"; } >"$FIXTURE/hook_pty.sh"
run_in_pane "$pane_a" hook_pty
grep -Fq 'cannot be verified' "$FIXTURE/hook_pty.out"

echo "PASS: test_codex_identity_leak"
