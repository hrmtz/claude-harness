#!/bin/bash
# Exercise pane_option_snapshot against tmux's real value quoting.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/../bin/formation"
TMP="$(mktemp -d)"
SOCKET="formation-parent-snapshot-${PPID}-$$"
trap 'command tmux -L "$SOCKET" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

export FORMATION_HOME="$TMP/formation-home"

# shellcheck source=/dev/null
source "$BIN"

command tmux -L "$SOCKET" -f /dev/null new-session -d -s snapshot-test \
  'sleep 30'
PANE="$(command tmux -L "$SOCKET" list-panes -t snapshot-test \
  -F '#{pane_id}')"

tmux() {
  command tmux -L "$SOCKET" "$@"
}

tmux set-option -p -u -t "$PANE" @snapshot >/dev/null 2>&1 || true
[[ "$(pane_option_snapshot "$PANE" @snapshot)" == "0|" ]]

# A pane id is the live #216 failure. Empty must remain distinguishable from
# unset; the remaining cases cover syntax tmux quotes or escapes in ordinary
# show-options output.
for value in '%209' '' 'a b' 'a"b' 'a\b' 'a;b'; do
  tmux set-option -p -t "$PANE" @snapshot "$value"
  [[ "$(pane_option_snapshot "$PANE" @snapshot)" == "1|$value" ]] || {
    printf 'FAIL: pane option snapshot changed value <%s>\n' "$value" >&2
    exit 1
  }
done

echo "test_parent_route_repair_real_tmux: passed"
