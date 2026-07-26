#!/bin/bash
# mailbox_delivery.sh - recipient resolution and mailbox delivery policy.
#
# This is the policy layer shared by `formation msg` and `mailbox-send`.
# Durability remains in mailbox.sh, pane signaling primitives remain in
# mailbox_notify.sh, and the exceptional keystroke contract remains in wake.sh.
# Keeping policy here prevents the two public send entrypoints from drifting on
# canonical addressing, relay ownership, or the exclusive-input gate.

MAILBOX_DELIVERY_LIB_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=/dev/null
source "$MAILBOX_DELIVERY_LIB_DIR/mailbox_notify.sh"
# shellcheck source=/dev/null
source "$MAILBOX_DELIVERY_LIB_DIR/wake.sh"

# Resolve the sender identity consistently for every public send entrypoint.
# Priority: explicit flag > launch identity > immutable/legacy pane identity >
# MAILBOX_FROM > optional window-name fallback > stable pane/fallback.
#
# Args: [explicit] [fallback] [allow-MAILBOX_FROM=1] [allow-window-name=1]
mailbox_resolve_sender() {
  local explicit="${1:-}" fallback="${2:-unknown}"
  local allow_mailbox_from="${3:-1}" allow_window="${4:-1}"
  local identity="" window=""
  if [[ -n "$explicit" ]]; then
    printf '%s\n' "$explicit"
    return
  fi
  if [[ -n "${FORMATION_SELF:-}" ]]; then
    printf '%s\n' "$FORMATION_SELF"
    return
  fi
  if [[ -n "${TMUX_PANE:-}" ]]; then
    identity="$(tmux display-message -p -t "$TMUX_PANE" \
      '#{?#{@formation_identity_locked},#{@formation_identity_locked},#{@formation_id}}' \
      2>/dev/null | tr -d '\000-\037\177' || true)"
    if [[ -n "$identity" ]]; then
      printf '%s\n' "$identity"
      return
    fi
  fi
  if [[ "$allow_mailbox_from" == "1" && -n "${MAILBOX_FROM:-}" ]]; then
    printf '%s\n' "$MAILBOX_FROM"
    return
  fi
  if [[ -n "${TMUX_PANE:-}" && "$allow_window" == "1" ]]; then
    window="$(tmux display-message -p -t "$TMUX_PANE" \
      '#{window_name}' 2>/dev/null | tr -d '\000-\037\177' || true)"
    window="${window#claude-}"
    window="${window#codex-}"
    window="${window#kimi-}"
    window="${window#grok-}"
    window="${window#main-}"
    if [[ -n "$window" ]]; then
      printf '%s\n' "$window"
      return
    fi
  fi
  if [[ -n "${TMUX_PANE:-}" ]]; then
    printf 'pane-%s\n' "${TMUX_PANE#%}"
    return
  fi
  printf '%s\n' "$fallback"
}

# Resolve a recipient without touching tmux.
#
# Args: <target> <registry.jsonl> [allow-direct-pane=0]
# Sets:
#   MAILBOX_RECIPIENT_LABEL
#   MAILBOX_RECIPIENT_PANE
#   MAILBOX_RECIPIENT_EXCLUSIVE (0|1)
mailbox_resolve_recipient() {
  local target="$1" registry="$2" allow_direct="${3:-0}"
  local raw="${target#pane-}" pane="" row="" canonical=""
  local allow_direct_json=false
  [[ "$allow_direct" == "1" ]] && allow_direct_json=true

  raw="${raw#%}"
  MAILBOX_RECIPIENT_LABEL=""
  MAILBOX_RECIPIENT_PANE=""
  MAILBOX_RECIPIENT_EXCLUSIVE=0

  if [[ "$allow_direct" == "1" && "$raw" =~ ^[0-9]+$ ]]; then
    MAILBOX_RECIPIENT_LABEL="pane-$raw"
    MAILBOX_RECIPIENT_PANE="%$raw"
    pane="$MAILBOX_RECIPIENT_PANE"
  elif [[ "$raw" =~ ^[0-9]+$ ]]; then
    pane="%$raw"
  fi

  if [[ -s "$registry" ]]; then
    row="$(jq -c --arg target "$target" --arg raw "$raw" --arg pane "$pane" \
      --argjson allow_direct "$allow_direct_json" \
      'select(.id == $target
              or ($allow_direct
                  and (.id == $raw or ($pane != "" and .pane_id == $pane))))' \
      "$registry" 2>/dev/null | tail -n1)"
    canonical="$(printf '%s' "$row" | jq -r '.id // empty' 2>/dev/null || true)"
    if [[ -n "$canonical" ]]; then
      MAILBOX_RECIPIENT_LABEL="$canonical"
      MAILBOX_RECIPIENT_PANE="$(printf '%s' "$row" | jq -r '.pane_id // empty')"
      if [[ "$(printf '%s' "$row" | jq -r '.exclusive_input // false')" == "true" ]]; then
        MAILBOX_RECIPIENT_EXCLUSIVE=1
      fi
    fi
  fi

  [[ -n "$MAILBOX_RECIPIENT_LABEL" && -n "$MAILBOX_RECIPIENT_PANE" ]]
}

# Signal a durable row. A healthy relay remains the single signal owner;
# otherwise the sender performs the same zero-keystroke pane signal directly.
#
# Args: <recipient-label> <pane> <seq> <from> <formation-state-dir>
mailbox_signal_or_defer() {
  local recipient="$1" pane="$2" seq="$3" from="$4" formation_dir="$5"
  local relay_pid_file="$formation_dir/$recipient.relay_pid"
  local relay_pid=""

  [[ -f "$relay_pid_file" ]] &&
    relay_pid="$(cat "$relay_pid_file" 2>/dev/null || true)"
  if mailbox_relay_alive "$relay_pid" "$recipient"; then
    echo "signal=pending relay_pid=$relay_pid pane=$pane (zero keystrokes into prompt)"
    return 0
  fi
  if ! mailbox_signal_pane "$pane" "$seq" "$from"; then
    echo "WARN (exit 4): row is durable, but pane $pane could not be signaled." >&2
    return 4
  fi
  echo "signaled pane=$pane directly because relay is unavailable (zero keystrokes into prompt)"
}

# Attempt the exceptional short prompt nudge after a row is durable+signaled.
# Both the registry and live pane must independently declare exclusive input.
#
# Args: <pane> <seq> <from> <registry-exclusive:0|1>
mailbox_inject_nudge() {
  local pane="$1" seq="$2" from="$3" registry_exclusive="$4"
  local pane_exclusive

  pane_exclusive="$(tmux show-options -p -v -t "$pane" \
    @formation_exclusive_input 2>/dev/null || true)"
  if [[ "$registry_exclusive" != "1" || "$pane_exclusive" != "1" ]]; then
    echo "REFUSED (exit 5): row is durable and its signal path was accepted, but pane $pane lacks the registry+pane --exclusive-input contract; prompt injection skipped." >&2
    return 5
  fi

  local notify="[mailbox seq=${seq} from=${from}] pull with formation inbox (receipt unconfirmed)"
  local settle="${FORMATION_SUBMIT_SETTLE_S:-${MAILBOX_SUBMIT_SETTLE_S:-0.4}}"
  local retry="${FORMATION_SUBMIT_RETRY_S:-${MAILBOX_SUBMIT_RETRY_S:-0.5}}"
  local submit_rc=0
  if FORMATION_SUBMIT_SETTLE_S="$settle" \
     FORMATION_SUBMIT_RETRY_S="$retry" \
     tmux_send_submit "$pane" "$notify"; then
    echo "inject=attempted pane=$pane (short pull nudge only; receipt unconfirmed)"
    return 0
  else
    submit_rc=$?
  fi
  if [[ "$submit_rc" -eq "${TMUX_SUBMIT_PASTED_UNCONFIRMED:-11}" ]]; then
    echo "WARN (exit 4): inject=pasted pane=$pane but submit is unconfirmed; DO NOT RETRY automatically (row remains durable)." >&2
  else
    echo "WARN (exit 4): row is durable and its signal path was accepted, but prompt nudge was not pasted." >&2
  fi
  return 4
}
