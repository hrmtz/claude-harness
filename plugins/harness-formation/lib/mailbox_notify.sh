#!/bin/bash
# mailbox_notify.sh - non-destructive mailbox notification primitives.
#
# Sourced by mailbox-send and mailbox_relay.sh. A successful signal means the
# durable mailbox row has been reflected into a pane option; it does NOT mean
# the recipient received or read the message.

mailbox_signal_pane() {
  local pane="$1" seq="$2" from="$3"
  local lock_path current
  lock_path="$(mailbox_notify_lock_path "$pane")"
  (
    flock -x 201
    current="$(tmux show-options -p -v -t "$pane" @formation_mail_pending 2>/dev/null || true)"
    # Never move the badge backwards when two senders signal concurrently.
    if [[ "$current" =~ ^[0-9]+$ && "$current" -ge "$seq" ]]; then
      return 0
    fi
    # This pane option is the durable, inspectable signal and therefore the
    # required success point. display-message is only a short human-facing flash.
    tmux set-option -p -t "$pane" @formation_mail_pending "$seq" 2>/dev/null || return 1
    tmux display-message -t "$pane" \
      "✉ mail seq=${seq} from=${from} — formation inbox / mailbox pull" \
      2>/dev/null || true
  ) 201>"$lock_path"
}

mailbox_notify_lock_path() {
  local pane="$1"
  local lock_dir="${MAILBOX_DIR:-${FORMATION_HOME:-$HOME/.formation}/mailbox}/notify-locks"
  local safe_pane="${pane//[^A-Za-z0-9_.-]/_}"
  mkdir -p "$lock_dir"
  printf '%s/%s.lock\n' "$lock_dir" "$safe_pane"
}

mailbox_relay_alive() {
  local pid="$1" agent="$2"
  local -a argv=()
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  # A live PID is insufficient: PID reuse once made reap capable of killing an
  # unrelated process. Require the exact daemon argv shape from procfs.
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  mapfile -d '' -t argv < "/proc/$pid/cmdline"
  # Supported real launch shapes:
  #   /path/mailbox_relay.sh AGENT PANE
  #   bash /path/mailbox_relay.sh AGENT PANE
  local exe0="${argv[0]:-}" exe1="${argv[1]:-}"
  if [[ "${exe0##*/}" == "mailbox_relay.sh" ]]; then
    [[ "${argv[1]:-}" == "$agent" ]]
    return
  fi
  if [[ "${exe0##*/}" =~ ^(ba|z|da)?sh$ &&
        "${exe1##*/}" == "mailbox_relay.sh" ]]; then
    [[ "${argv[2]:-}" == "$agent" ]]
    return
  fi
  return 1
}
