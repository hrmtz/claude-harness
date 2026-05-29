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
  local buf="njslyr-$$-$(date +%s%N)"
  tmux load-buffer -b "$buf" "$file"
  # -p: bracketed paste so a multi-line note lands atomically (no embedded
  # newline submits the turn early). See tmux_send_submit for the rationale.
  tmux paste-buffer -t "$pane_id" -b "$buf" -p -d
  sleep 0.4
  tmux send-keys -t "$pane_id" Enter
}

# Send text to a pane and force-submit, robustly.
#
# Injection uses bracketed paste (load-buffer + paste-buffer -p) rather than
# `send-keys -l`, which fixes two failure modes the old type-then-Enter path
# suffered from:
#   1. Premature submit / "delay x2 won't fire": typed text needs a render
#      tick before Claude Code's textarea commits it. An Enter sent in the
#      same batch races ahead and submits an empty turn, leaving the text
#      un-submitted. Bracketed paste lands the whole text as one atomic input
#      event, and we sleep before the Enter so it submits the committed text.
#   2. "search-mode": with send-keys -l, embedded newlines in a multi-line
#      briefing submit early, and a resulting line starting with / @ # ! is
#      interpreted as a slash-command / file-search / memory / bash trigger.
#      Pasted prefixes do NOT trigger those modes — only typed ones do.
# The trailing guarded Enter is belt-and-suspenders for the rare swallow;
# harmless on an already-submitted (empty) prompt and for Codex.
tmux_send_submit() {
  local pane_id="$1" text="$2"
  local buf="njslyr-$$-$(date +%s%N)"
  printf '%s' "$text" | tmux load-buffer -b "$buf" -
  tmux paste-buffer -t "$pane_id" -b "$buf" -p -d
  sleep 0.4
  tmux send-keys -t "$pane_id" Enter
  sleep 0.5
  tmux send-keys -t "$pane_id" Enter
}
