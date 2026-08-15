#!/usr/bin/env bash
# The mail-nudge watch loop re-applies the Formation window-status format after
# a tmux server restart (#283): tmux global options are server-memory only, and
# the watcher is the one process that outlives server restarts. Gates under
# test: missing journal -> apply once; journal present -> no apply; explicit
# revert marker -> no apply; FORMATION_WINDOW_STATUS_AUTO=0 -> no apply.
set -euo pipefail
trap 'echo "FAIL: test_mail_nudge_window_status_ensure line $LINENO" >&2' ERR
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/canbin"

# ensure_window_status resolves the helper next to the running script, so the
# watcher copy and the fake helper must share a directory.
cp "$HERE/../bin/formation-mail-nudge" "$TMP/canbin/formation-mail-nudge"
chmod +x "$TMP/canbin/formation-mail-nudge"

APPLY_LOG="$TMP/apply.log"
cat >"$TMP/canbin/formation-window-status" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$APPLY_LOG"
mkdir -p "$FORMATION_HOME/state/window-status"
touch "$FORMATION_HOME/state/window-status/4242.json"
EOF
chmod +x "$TMP/canbin/formation-window-status"

cat >"$TMP/bin/tmux" <<'EOF'
#!/usr/bin/env bash
case " $* " in
  *"display-message -p #{pid}"*) echo 4242 ;;
  *"list-panes"*) : ;;
  *) : ;;
esac
EOF
chmod +x "$TMP/bin/tmux"

export PATH="$TMP/bin:/usr/bin:/bin"
export APPLY_LOG

run_watch() {
  # One watch cycle: start, let the first loop iteration run, terminate. The
  # watch pid file is written right before the loop, so it is the readiness
  # sentinel; the short sleep afterwards covers the first ensure+sweep pass.
  : >"$APPLY_LOG"
  local pidfile="$FORMATION_HOME/state/mail-nudge/watch.pid.json"
  "$TMP/canbin/formation-mail-nudge" --watch --interval 60 >"$TMP/watch.out" 2>"$TMP/watch.err" &
  local pid=$!
  local deadline=$((SECONDS + 10))
  until [[ -e "$pidfile" || $SECONDS -ge $deadline ]]; do sleep 0.1; done
  sleep 0.7
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

# Case 1: fresh server (no journal, no marker) -> exactly one apply.
export FORMATION_HOME="$TMP/home1"
mkdir -p "$FORMATION_HOME"
run_watch
[[ "$(wc -l <"$APPLY_LOG")" -eq 1 ]]
grep -Fqx 'apply' "$APPLY_LOG"
grep -Fq 'window-status re-applied for tmux server 4242' "$TMP/watch.out"
[[ -e "$FORMATION_HOME/state/window-status/4242.json" ]]

# Case 2: journal already present -> no apply.
export FORMATION_HOME="$TMP/home2"
mkdir -p "$FORMATION_HOME/state/window-status"
touch "$FORMATION_HOME/state/window-status/4242.json"
run_watch
[[ ! -s "$APPLY_LOG" ]]

# Case 3: explicit revert marker wins over re-application.
export FORMATION_HOME="$TMP/home3"
mkdir -p "$FORMATION_HOME/state/window-status"
touch "$FORMATION_HOME/state/window-status/4242.reverted"
run_watch
[[ ! -s "$APPLY_LOG" ]]
[[ ! -e "$FORMATION_HOME/state/window-status/4242.json" ]]

# Case 4: opt-out env var disables the rail entirely.
export FORMATION_HOME="$TMP/home4"
mkdir -p "$FORMATION_HOME"
FORMATION_WINDOW_STATUS_AUTO=0 run_watch
[[ ! -s "$APPLY_LOG" ]]

echo "test_mail_nudge_window_status_ensure: passed"
