#!/bin/bash
# wake.sh - wake a target pane (local tmux; ssh fallback deferred to v2)
# Sourced by bin/formation.

wake_pane() {
  local pane_id="$1"
  local note="${2:-inbox}"
  if ! tmux has-session 2>/dev/null; then
    echo "wake: no tmux server" >&2
    return 1
  fi
  if ! tmux list-panes -a -F '#{pane_id}' | grep -qx "$pane_id"; then
    echo "wake: pane not found: $pane_id" >&2
    return 1
  fi
  tmux send-keys -t "$pane_id" "$note" Enter
}

wake_paste() {
  local pane_id="$1" file="$2"
  local buf="njslyr-$$-$(date +%s)"
  tmux load-buffer -b "$buf" "$file"
  tmux paste-buffer -t "$pane_id" -b "$buf" -d
  tmux send-keys -t "$pane_id" Enter
}

# Send text to a pane and force-submit. Claude Code's text area can swallow
# a single Enter and leave the message un-submitted; double-tapping with a
# delay reliably submits. Shared by `formation msg` and the mailbox relay.
tmux_send_submit() {
  local pane_id="$1" text="$2"
  tmux send-keys -t "$pane_id" "$text" Enter
  sleep 0.5
  tmux send-keys -t "$pane_id" Enter
}
