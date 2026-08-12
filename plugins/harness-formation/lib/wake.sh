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
    tmux send-keys -X -t "$pane_id" cancel 2>/dev/null || return
    sleep 0.1 || return
  fi
}

# Textarea submission contract for Claude Code and Codex. Both can consume the
# first Enter while committing a recent typed/pasted value, leaving the text
# visible but unsubmitted. Wait for the render tick, press Enter, then retry
# after a second delay. Keep this separate from shell-command launch Enter.
_submit_enter_twice() {
  local pane_id="$1"
  sleep "${FORMATION_SUBMIT_SETTLE_S:-0.4}" || return
  tmux send-keys -t "$pane_id" Enter || return
  sleep "${FORMATION_SUBMIT_RETRY_S:-0.5}" || return
  tmux send-keys -t "$pane_id" Enter || return
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
# Return codes let callers distinguish retry-safe failures before paste from a
# pasted-but-unconfirmed submit, where retrying could merge a second nudge into
# the recipient draft.
TMUX_SUBMIT_NOT_PASTED=10
TMUX_SUBMIT_PASTED_UNCONFIRMED=11
TMUX_SUBMIT_KIMI_UNCONFIRMED=12

tmux_send_submit() {
  local pane_id="$1" text="$2"
  local buf="njslyr-$$-$(date +%s%N)"
  # Leave copy-mode first, or the submit Enter below is eaten by it.
  _exit_copy_mode "$pane_id" || return "$TMUX_SUBMIT_NOT_PASTED"
  printf '%s' "$text" | tmux load-buffer -b "$buf" - ||
    return "$TMUX_SUBMIT_NOT_PASTED"
  if ! tmux paste-buffer -t "$pane_id" -b "$buf" -p -d; then
    tmux delete-buffer -b "$buf" 2>/dev/null || true
    return "$TMUX_SUBMIT_NOT_PASTED"
  fi
  _submit_enter_twice "$pane_id" ||
    return "$TMUX_SUBMIT_PASTED_UNCONFIRMED"
}

# Kimi renders an input box before its first-session state is durable. A seed
# submitted in that window can be accepted by tmux, then erased by Kimi's
# remaining startup redraws. Confirm the first turn created a Kimi session;
# when it did not, submit an existing draft again or re-paste a vanished seed.
tmux_send_kimi_bootstrap() {
  local pane_id="$1" text="$2"
  local attempts="${FORMATION_KIMI_SEED_ATTEMPTS:-3}"
  local confirm_checks="${FORMATION_KIMI_SEED_CONFIRM_CHECKS:-10}"
  local confirm_sleep="${FORMATION_KIMI_SEED_CONFIRM_SLEEP_S:-0.5}"
  local attempt check screen

  sleep "${FORMATION_KIMI_READY_SETTLE_S:-1}" ||
    return "$TMUX_SUBMIT_NOT_PASTED"
  for attempt in $(seq 1 "$attempts"); do
    screen="$(tmux capture-pane -p -t "$pane_id" 2>/dev/null || true)"
    if [[ "$attempt" -gt 1 && "$screen" == *"Formation bootstrap."* ]]; then
      # The seed may still be a visible draft. Retap submit without pasting a
      # duplicate; Enter on an already-cleared input box is harmless.
      _submit_enter_twice "$pane_id" ||
        return "$TMUX_SUBMIT_PASTED_UNCONFIRMED"
    else
      tmux_send_submit "$pane_id" "$text" || return
    fi

    for check in $(seq 1 "$confirm_checks"); do
      sleep "$confirm_sleep" || return "$TMUX_SUBMIT_KIMI_UNCONFIRMED"
      screen="$(tmux capture-pane -p -t "$pane_id" 2>/dev/null || true)"
      if [[ "$screen" =~ Session:[[:space:]]+session_[A-Za-z0-9_-]+ ]]; then
        return 0
      fi
    done
  done
  return "$TMUX_SUBMIT_KIMI_UNCONFIRMED"
}

# Send a machine-attributed pull nudge through the shared submit rail.
#
# Keep attribution outside tmux_send_submit itself: that lower-level primitive
# also submits the initial worker briefing, which is not a mailbox nudge. All
# prompt wakes after bootstrap must use this wrapper so a worker can never
# mistake parent/Formation control traffic for a user-authored turn.
#
# Args: <pane> <from> <mailbox-seq> <short-pull-instruction>
tmux_send_nudge() {
  local pane_id="$1" from="$2" seq="$3" text="$4"
  local safe_from
  safe_from="$(printf '%s' "$from" | tr -cd 'A-Za-z0-9._-')"
  [[ -n "$safe_from" ]] || safe_from="unknown"
  [[ "$seq" =~ ^[0-9]+$ ]] || seq="0"
  tmux_send_submit "$pane_id" \
    "[FORMATION-NUDGE from=${safe_from} seq=${seq}] ${text}"
}
