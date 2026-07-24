#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
GUARD="$ROOT/plugins/harness-core/bin/harness-cross-cli"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
STATE="$TMP/state"
LOG="$TMP/tmux.log"
printf '%%7\t@3\tcodex-parent\tcodex-parent\t1\n' > "$STATE"

cat > "$TMP/bin/tmux" <<'SH'
#!/usr/bin/env bash
set -u
state="$TEST_TMUX_STATE"
log="$TEST_TMUX_LOG"
IFS=$'\t' read -r pane window name title panes < "$state"
case "$1" in
  display-message)
    format="${!#}"
    case "$format" in
      '#{pane_id}') printf '%s\n' "$pane" ;;
      '#{window_id}') printf '%s\n' "$window" ;;
      '#{window_name}') printf '%s\n' "$name" ;;
      '#{pane_title}') printf '%s\n' "$title" ;;
      '#{window_panes}') printf '%s\n' "$panes" ;;
      *) exit 1 ;;
    esac
    ;;
  rename-window)
    printf '%s\n' "$*" >> "$log"
    name="$4"
    printf '%s\t%s\t%s\t%s\t%s\n' "$pane" "$window" "$name" "$title" "$panes" > "$state"
    ;;
  select-pane)
    printf '%s\n' "$*" >> "$log"
    title="$5"
    printf '%s\t%s\t%s\t%s\t%s\n' "$pane" "$window" "$name" "$title" "$panes" > "$state"
    ;;
esac
SH
chmod +x "$TMP/bin/tmux"

cat > "$TMP/bin/child" <<'SH'
#!/usr/bin/env bash
set -u
case "${1:-}" in
  rename)
    tmux rename-window -t %7 kimi-stolen
    exit "${2:-0}"
    ;;
  env)
    [ "${HARNESS_TMUX_SELF_NAME_DISABLE:-}" = 1 ]
    [ -z "${TMUX_PANE:-}" ]
    ;;
  self-name)
    [ -z "${HARNESS_TMUX_SELF_NAME_DISABLE:-}" ]
    [ -z "${HIPPOCAMPUS_TMUX_NAME_DISABLE:-}" ]
    [ -z "${KIMI_TMUX_NAME_DISABLE:-}" ]
    [ -z "${CODEX_TMUX_NAME_DISABLE:-}" ]
    [ -z "${CLAUDE_TMUX_NAME_DISABLE:-}" ]
    [ -z "${GROK_TMUX_NAME_DISABLE:-}" ]
    [ "${TMUX_PANE:-}" = "%7" ]
    ;;
  stdin)
    IFS= read -r value
    [ "$value" = "prompt-through-guard" ]
    ;;
  external-rename)
    printf '%%7\t@3\tuser-renamed\tuser-title\t1\n' > "$TEST_TMUX_STATE"
    ;;
  signal)
    tmux rename-window -t %7 kimi-stolen
    trap 'exit 143' TERM
    while :; do sleep 1; done
    ;;
  signal-int)
    exec sleep 10
    ;;
esac
SH
chmod +x "$TMP/bin/child"

run_guard() {
  PATH="$TMP/bin:$PATH" TEST_TMUX_STATE="$STATE" TEST_TMUX_LOG="$LOG" \
    TMUX="fake" TMUX_PANE="%7" "$GUARD" "$@"
}

pass=0 fail=0
ok() { echo "ok - $1"; pass=$((pass + 1)); }
bad() { echo "not ok - $1"; fail=$((fail + 1)); }

: > "$LOG"
run_guard -- child rename 0 >/dev/null 2>&1
if grep -Fq 'rename-window -t @3 codex-parent' "$LOG"; then
  ok "restores drift after exit 0 using the snapshot window id"
else
  bad "did not restore exit-0 drift"
fi

: > "$LOG"
set +e
run_guard -- child rename 23 >/dev/null 2>&1
rc=$?
set -e
if [ "$rc" -eq 23 ] && grep -Fq 'rename-window -t @3 codex-parent' "$LOG"; then
  ok "preserves nonzero status while restoring drift"
else
  bad "nonzero child status or restoration was lost (rc=$rc)"
fi

printf '%%7\t@3\tcodex-parent\tcodex-parent\t1\n' > "$STATE"
: > "$LOG"
PATH="$TMP/bin:$PATH" TEST_TMUX_STATE="$STATE" TEST_TMUX_LOG="$LOG" \
  TMUX="fake" TMUX_PANE="%7" "$GUARD" -- child signal >/dev/null 2>&1 &
guard_pid=$!
for _ in $(seq 1 20); do
  grep -Fq 'rename-window -t %7 kimi-stolen' "$LOG" && break
  sleep 0.05
done
kill -TERM "$guard_pid"
set +e
wait "$guard_pid"
rc=$?
set -e
if [ "$rc" -eq 143 ] && grep -Fq 'rename-window -t @3 codex-parent' "$LOG"; then
  ok "restores drift on TERM and returns signal status"
else
  bad "TERM cleanup or status propagation failed (rc=$rc)"
fi

set +e
PATH="$TMP/bin:$PATH" TEST_TMUX_STATE="$STATE" TEST_TMUX_LOG="$LOG" \
  TMUX="fake" TMUX_PANE="%7" GUARD="$GUARD" python3 - <<'PY'
import os, signal, subprocess, time
p = subprocess.Popen([os.environ["GUARD"], "--", "child", "signal-int"],
                     env=os.environ.copy(),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(0.1)
p.send_signal(signal.SIGINT)
try:
    rc = p.wait(timeout=2)
except subprocess.TimeoutExpired:
    p.terminate()
    p.wait()
    rc = 999
raise SystemExit(0 if rc == 130 else 1)
PY
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  ok "forwards SIGINT to a child with default signal handling"
else
  bad "SIGINT left the supervised child running"
fi

if run_guard --isolate-tmux -- child env >/dev/null 2>&1; then
  ok "passes the shared kill switch and removes tmux identity from headless child"
else
  bad "headless child environment was not isolated"
fi

if PATH="$TMP/bin:$PATH" TEST_TMUX_STATE="$STATE" TEST_TMUX_LOG="$LOG" \
   TMUX="stale" TMUX_PANE="" "$GUARD" --isolate-tmux -- child env >/dev/null 2>&1; then
  ok "isolated launch ignores incomplete inherited tmux context"
else
  bad "stale tmux context blocked an isolated headless child"
fi

if HARNESS_TMUX_SELF_NAME_DISABLE=1 HIPPOCAMPUS_TMUX_NAME_DISABLE=1 \
   KIMI_TMUX_NAME_DISABLE=1 CODEX_TMUX_NAME_DISABLE=1 \
   CLAUDE_TMUX_NAME_DISABLE=1 GROK_TMUX_NAME_DISABLE=1 \
   run_guard --allow-self-name -- child self-name >/dev/null 2>&1; then
  ok "Formation mode preserves the worker's own identity hooks"
else
  bad "Formation mode disabled the worker's identity hooks"
fi

if printf 'prompt-through-guard\n' | run_guard -- child stdin >/dev/null 2>&1; then
  ok "preserves caller stdin for supervised children"
else
  bad "supervised child stdin was disconnected"
fi

printf '%%7\t@3\tcodex|parent\tcodex|parent\t1\n' > "$STATE"
: > "$LOG"
if run_guard --expected-window 'codex|parent' -- child rename 0 >/dev/null 2>&1 \
   && grep -Fq 'rename-window -t @3 codex|parent' "$LOG"; then
  ok "handles delimiter characters in tmux identity fields"
else
  bad "tmux identity parsing corrupted a delimiter character"
fi

printf '%%7\t@3\tcodex-parent\tcodex-parent\t1\n' > "$STATE"
: > "$LOG"
if run_guard --isolate-tmux -- child external-rename >/dev/null 2>&1 \
   && grep -Fq 'user-renamed' "$STATE" \
   && ! grep -Fq 'rename-window -t @3 codex-parent' "$LOG"; then
  ok "isolated launch preserves unrelated concurrent window renames"
else
  bad "isolated launch reverted an unrelated window rename"
fi

printf '%%7\t@3\tshared-parent\tshared-title\t2\n' > "$STATE"
: > "$LOG"
if run_guard -- child rename 0 >/dev/null 2>&1 \
   && grep -Fq 'kimi-stolen' "$STATE" \
   && ! grep -Fq 'rename-window -t @3 shared-parent' "$LOG"; then
  ok "shared-window launch preserves sibling window renames"
else
  bad "shared-window launch reverted a sibling window rename"
fi

printf '%%7\t@3\twrong-name\twrong-title\t1\n' > "$STATE"
: > "$LOG"
if run_guard --expected-window codex-parent --isolate-tmux -- child env >/dev/null 2>&1 \
   && grep -Fq 'rename-window -t @3 codex-parent' "$LOG"; then
  ok "repairs pre-existing drift before launch"
else
  bad "preflight drift was not repaired"
fi

printf '%%7\t@3\twrong-name\twrong-title\t2\n' > "$STATE"
: > "$LOG"
set +e
run_guard --expected-window codex-parent -- child env >/dev/null 2>&1
rc=$?
set -e
if [ "$rc" -eq 70 ] && [ ! -s "$LOG" ]; then
  ok "fails closed instead of renaming a shared window"
else
  bad "shared-window mismatch was not fail-closed"
fi

unset TMUX TMUX_PANE
if PATH="$TMP/bin:$PATH" "$GUARD" -- child env >/dev/null 2>&1; then
  ok "tmux-external launch is a no-op wrapper"
else
  bad "tmux-external launch failed"
fi

printf 'RESULT: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
