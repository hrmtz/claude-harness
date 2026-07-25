#!/usr/bin/env bash
# Ensure tmux preserves OSC 8 links emitted by terminal agent UIs.
#
# tmux 3.4+ understands OSC 8, but only forwards stored hyperlinks to clients
# whose terminal feature set includes "hyperlinks". Apply the documented
# wildcard feature once per server.
set -uo pipefail

[ -n "${TMUX:-}" ] || exit 0
[ -n "${TMUX_PANE:-}" ] || exit 0
command -v tmux >/dev/null 2>&1 || exit 0

# A stale inherited TMUX_PANE must not mutate an unrelated tmux server.
tmux display-message -p -t "$TMUX_PANE" '#{pane_id}' >/dev/null 2>&1 || exit 0

features="$(tmux show-options -sv terminal-features 2>/dev/null || true)"
if ! printf '%s\n' "$features" | grep -Eq '^\*:([^:]+:)*hyperlinks(:|$)'; then
    tmux set-option -as terminal-features ',*:hyperlinks' >/dev/null 2>&1 || true
fi
