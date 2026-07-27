#!/usr/bin/env bash
# Deterministic regression for #211: slow readiness is not daemon death.
set -euo pipefail
trap 'echo "FAIL: test_relay_startup_lifecycle line $LINENO" >&2' ERR

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/../bin/formation"
REAL_LIB="$HERE/../lib"
FIXTURE="$(mktemp -d)"
ACTIVE_PID=""
cleanup() {
  if [[ -n "$ACTIVE_PID" ]]; then
    pkill -P "$ACTIVE_PID" 2>/dev/null || true
    kill "$ACTIVE_PID" 2>/dev/null || true
    wait "$ACTIVE_PID" 2>/dev/null || true
  fi
  rm -r "$FIXTURE"
}
trap cleanup EXIT

export FORMATION_HOME="$FIXTURE/home"
export FORMATION_SELF="relay-lifecycle-test"
unset TMUX_PANE
source "$BIN"
set -euo pipefail

mkdir -p "$FIXTURE/bin" "$FIXTURE/logs"
REAL_JQ="$(command -v jq)"
cat >"$FIXTURE/bin/jq" <<'EOF'
#!/usr/bin/env bash
delay=0
for arg in "$@"; do
  [[ "$arg" == "$RELAY_JQ_DELAY_TARGET" ]] && delay=1
done
if [[ "$delay" -eq 1 && ! -e "$RELAY_JQ_DELAY_MARKER" ]]; then
  : >"$RELAY_JQ_DELAY_MARKER"
  sleep "$RELAY_JQ_DELAY"
fi
exec "$RELAY_REAL_JQ" "$@"
EOF
chmod +x "$FIXTURE/bin/jq"
export PATH="$FIXTURE/bin:/usr/bin:/bin"
export RELAY_REAL_JQ="$REAL_JQ"
export RELAY_JQ_DELAY=0.35
export FORMATION_RELAY_FORCE_POLL=1
export FORMATION_RELAY_POLL_INTERVAL=0.05
export FORMATION_MAILBOX="$FIXTURE/home/mailbox/log.jsonl"
export RELAY_JQ_DELAY_TARGET="$FORMATION_MAILBOX"

stop_owned_relay() {
  local pid="$1"
  pkill -P "$pid" 2>/dev/null || true
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

# Positive reproduction: during the old 2-poll observation window the real
# relay is alive but has not anchored/published ready yet. The removed branch
# would have killed this exact healthy PID.
export RELAY_JQ_DELAY_MARKER="$FIXTURE/legacy-delay.marker"
legacy_ready="$FIXTURE/legacy.ready"
FORMATION_RELAY_READY_FILE="$legacy_ready" \
  bash "$REAL_LIB/mailbox_relay.sh" legacy-slow %41 \
  >"$FIXTURE/logs/legacy.log" 2>&1 &
legacy_pid=$!
ACTIVE_PID="$legacy_pid"
sleep 0.08
if kill -0 "$legacy_pid" 2>/dev/null && [[ ! -e "$legacy_ready" ]]; then
  echo "legacy_timeout_window=$legacy_pid"
else
  echo "slow-readiness fixture did not enter the legacy kill window" >&2
  exit 1
fi
# This is the removed timeout branch's destructive action, applied only to the
# isolated fixture PID. It proves the pre-fix policy kills a healthy slow relay.
stop_owned_relay "$legacy_pid"
! kill -0 "$legacy_pid" 2>/dev/null
echo "legacy_timeout_killed=$legacy_pid"
ACTIVE_PID=""

# Fixed path returns PENDING without publishing the sender-visible pidfile or
# killing the daemon; the relay later publishes ready+pid after anchoring.
export RELAY_JQ_DELAY_MARKER="$FIXTURE/fixed-delay.marker"
export FORMATION_RELAY_LOG_DIR="$FIXTURE/logs"
export FORMATION_RELAY_READY_ATTEMPTS=2
export FORMATION_RELAY_READY_SLEEP=0.02
registry_add fixed-slow %42 formation-fixed-slow "$FIXTURE/fixed-briefing.md" \
  test-fixed-session claude relay-fixed-test "relay fixed test" 0 parent %40
fixed_out="$(start_mailbox_relay fixed-slow %42 formation-fixed-slow claude)"
fixed_rc=$?
[[ "$fixed_rc" -eq 0 && "$fixed_out" == *"relay=PENDING"* ]]
fixed_row="$(registry_get fixed-slow)"
[[ "$(printf '%s\n' "$fixed_row" | jq -r '.relay_state')" == "STARTING" ]]
[[ "$(printf '%s\n' "$fixed_row" | jq -r '.relay_reason')" == "readiness_timeout" ]]
fixed_starting="$FORMATION_DIR/fixed-slow.relay_starting_pid"
fixed_public="$FORMATION_DIR/fixed-slow.relay_pid"
fixed_ready="$FORMATION_DIR/fixed-slow.relay_ready"
fixed_pid="$(cat "$fixed_starting")"
ACTIVE_PID="$fixed_pid"
[[ ! -e "$fixed_public" ]]
kill -0 "$fixed_pid"
for _ in $(seq 1 80); do
  [[ "$(cat "$fixed_ready" 2>/dev/null || true)" == "$fixed_pid" &&
     "$(cat "$fixed_public" 2>/dev/null || true)" == "$fixed_pid" ]] && break
  sleep 0.02
done
[[ "$(cat "$fixed_ready")" == "$fixed_pid" ]]
[[ "$(cat "$fixed_public")" == "$fixed_pid" ]]
[[ ! -e "$fixed_starting" ]]
mailbox_relay_alive "$fixed_pid" fixed-slow
[[ "$(relay_effective_state fixed-slow "$(registry_get fixed-slow)")" == "READY" ]]
fixed_status="$(cmd_status)"
[[ "$fixed_status" == *"fixed-slow"* && "$fixed_status" == *"relay=READY"* ]]
# Status is a read-only overlay: async promotion does not grant the daemon
# registry write ownership or erase the last synchronous timeout observation.
fixed_row_after_status="$(registry_get fixed-slow)"
[[ "$(printf '%s\n' "$fixed_row_after_status" | jq -r '.relay_state')" == "STARTING" ]]
[[ "$(printf '%s\n' "$fixed_row_after_status" | jq -r '.relay_reason')" == "readiness_timeout" ]]
stop_owned_relay "$fixed_pid"
registry_remove fixed-slow
ACTIVE_PID=""

# Reap also owns the private STARTING state. This closes the race where a slow
# relay has not yet promoted itself to the public pidfile when its pane exits.
export RELAY_JQ_DELAY_MARKER="$FIXTURE/reap-delay.marker"
registry_add reap-slow %44 formation-reap-slow "$FIXTURE/reap-briefing.md" \
  test-reap-session claude relay-reap-test "relay reap test" 0 parent %40
reap_out="$(start_mailbox_relay reap-slow %44 formation-reap-slow claude)"
[[ "$reap_out" == *"relay=PENDING"* ]]
reap_starting="$FORMATION_DIR/reap-slow.relay_starting_pid"
reap_pid="$(cat "$reap_starting")"
ACTIVE_PID="$reap_pid"
[[ ! -e "$FORMATION_DIR/reap-slow.relay_pid" ]]
cmd_reap reap-slow >/dev/null
for _ in $(seq 1 40); do
  kill -0 "$reap_pid" 2>/dev/null || break
  sleep 0.01
done
! kill -0 "$reap_pid" 2>/dev/null
[[ ! -e "$FORMATION_DIR/reap-slow.relay_starting_pid" ]]
[[ ! -e "$FORMATION_DIR/reap-slow.relay_pid" ]]
[[ ! -e "$FORMATION_DIR/reap-slow.relay_ready" ]]
ACTIVE_PID=""

# A daemon that genuinely exits during this slice remains DEAD. It is never
# mistaken for slow readiness and leaves no publishable lifecycle state.
broken_lib="$FIXTURE/broken-lib"
mkdir -p "$broken_lib"
cat >"$broken_lib/mailbox_relay.sh" <<'EOF'
#!/usr/bin/env bash
exit 23
EOF
chmod +x "$broken_lib/mailbox_relay.sh"
saved_lib="$LIB_DIR"
LIB_DIR="$broken_lib"
registry_add broken %43 formation-broken "$FIXTURE/broken-briefing.md" \
  test-broken-session claude relay-broken-test "relay broken test" 0 parent %40
if broken_out="$(start_mailbox_relay broken %43 formation-broken claude 2>&1)"; then
  broken_rc=0
else
  broken_rc=$?
fi
LIB_DIR="$saved_lib"
[[ "$broken_rc" -eq 1 && "$broken_out" == *"relay=DEAD"* ]]
broken_row="$(registry_get broken)"
[[ "$(printf '%s\n' "$broken_row" | jq -r '.relay_state')" == "DEAD" ]]
[[ "$(printf '%s\n' "$broken_row" | jq -r '.relay_reason')" == "daemon_exit" ]]
broken_status="$(cmd_status)"
[[ "$broken_status" == *"broken"* && "$broken_status" == *"relay=DEAD"* ]]
[[ ! -e "$FORMATION_DIR/broken.relay_pid" ]]
[[ ! -e "$FORMATION_DIR/broken.relay_starting_pid" ]]
[[ ! -e "$FORMATION_DIR/broken.relay_ready" ]]
registry_remove broken

echo "test_relay_startup_lifecycle: passed"
