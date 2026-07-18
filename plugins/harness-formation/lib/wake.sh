#!/bin/bash
# wake.sh - wake a target pane (local tmux; ssh fallback deferred to v2)
# Sourced by bin/formation.

# If the target pane is in tmux copy-mode (the user scrolled up to read, or a
# prior interaction left it there), keystrokes sent with `send-keys` are
# consumed by copy-mode instead of reaching the application: Enter copies the
# selection and exits rather than submitting, and a literal `/` opens copy-mode
# *search* — the exact "Enter won't fire" and "drops into search-mode" symptoms
# seen when injecting into a Claude Code pane. paste-buffer reaches the app tty
# regardless, but the submit Enter does not, so we must leave copy-mode first.
_exit_copy_mode() {
  local pane_id="$1"
  if [[ "$(tmux display-message -p -t "$pane_id" '#{pane_in_mode}' 2>/dev/null)" == "1" ]]; then
    tmux send-keys -X -t "$pane_id" cancel 2>/dev/null
    sleep 0.1
  fi
}

# Textarea submission contract for Claude Code and Codex. Both can consume the
# first Enter while committing a recent typed/pasted value, leaving the text
# visible but unsubmitted. Wait for the render tick, press Enter, then retry
# after a second delay. Keep this separate from shell-command launch Enter.
_submit_enter_twice() {
  local pane_id="$1"
  sleep "${FORMATION_SUBMIT_SETTLE_S:-0.4}"
  tmux send-keys -t "$pane_id" Enter
  sleep "${FORMATION_SUBMIT_RETRY_S:-0.5}"
  tmux send-keys -t "$pane_id" Enter
}

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
  _exit_copy_mode "$pane_id"
  tmux send-keys -l -t "$pane_id" "$note"
  _submit_enter_twice "$pane_id"
}

wake_paste() {
  local pane_id="$1" file="$2"
  local buf="njslyr-$$-$(date +%s%N)"
  _exit_copy_mode "$pane_id"
  tmux load-buffer -b "$buf" "$file"
  # -p: bracketed paste so a multi-line note lands atomically (no embedded
  # newline submits the turn early). See tmux_send_submit for the rationale.
  tmux paste-buffer -t "$pane_id" -b "$buf" -p -d
  _submit_enter_twice "$pane_id"
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
  # Leave copy-mode first, or the submit Enter below is eaten by it.
  _exit_copy_mode "$pane_id"
  printf '%s' "$text" | tmux load-buffer -b "$buf" -
  tmux paste-buffer -t "$pane_id" -b "$buf" -p -d
  _submit_enter_twice "$pane_id"
}
