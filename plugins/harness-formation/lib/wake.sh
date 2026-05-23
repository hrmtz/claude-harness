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
  tmux send-keys -l -t "$pane_id" "$note"
  tmux send-keys -t "$pane_id" Enter
}

wake_paste() {
  local pane_id="$1" file="$2"
  local buf="njslyr-$$-$(date +%s)"
  tmux load-buffer -b "$buf" "$file"
  tmux paste-buffer -t "$pane_id" -b "$buf" -d
  tmux send-keys -t "$pane_id" Enter
}

# Send text to a pane and force-submit. Use -l for literal send so multi-byte
# (Japanese etc.) characters are not misinterpreted as tmux key names.
# Double-tap Enter: Claude Code's textarea can swallow a single Enter;
# harmless for Codex (second Enter just submits an empty turn).
tmux_send_submit() {
  local pane_id="$1" text="$2"
  tmux send-keys -l -t "$pane_id" "$text"
  tmux send-keys -t "$pane_id" Enter
  sleep 0.5
  tmux send-keys -t "$pane_id" Enter
}
