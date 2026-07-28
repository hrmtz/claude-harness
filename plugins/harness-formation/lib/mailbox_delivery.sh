#!/bin/bash
# mailbox_delivery.sh - recipient resolution and mailbox delivery policy.
#
# This is the policy layer shared by every public mailbox-producing command:
# `formation msg/report/done/ask/ack/resolve` and `mailbox-send`. Durability
# remains in mailbox.sh, pane signaling primitives remain in mailbox_notify.sh,
# and the exceptional keystroke contract remains in wake.sh. Keeping policy
# here prevents send entrypoints from drifting on canonical addressing, relay
# ownership, or the exclusive-input gate.

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

# Resolve the caller's actual pane by matching pane root pids against this
# process's ancestry. A resolving $TMUX_PANE is not sufficient: wrappers and
# tmux run-shell can inherit a live sibling pane id (#59). This check is
# read-only and uses no process-name inference.
mailbox_resolve_caller_pane() {
  local pid="$$" next guard=0 line pane pane_pid pane_tty rank
  local best_rank=999 best_pane="" caller_tty="" tty_pane="" tty_matches=0
  declare -A ancestor_rank=()

  while [[ "$guard" -lt 64 && "$pid" =~ ^[0-9]+$ && "$pid" -gt 1 ]]; do
    ancestor_rank["$pid"]="$guard"
    next="$(ps -o ppid= -p "$pid" 2>/dev/null | awk 'NR==1 { print $1 }')"
    [[ -n "$next" && "$next" != "$pid" ]] || break
    pid="$next"
    guard=$((guard + 1))
  done

  caller_tty="$(ps -o tty= -p "$$" 2>/dev/null |
    awk 'NR == 1 { gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print }' || true)"
  case "$caller_tty" in
    ""|"?"|"??") caller_tty="" ;;
    /dev/*) ;;
    *) caller_tty="/dev/$caller_tty" ;;
  esac

  while IFS= read -r line; do
    IFS='|' read -r pane pane_pid pane_tty <<<"$line"
    [[ "$pane" =~ ^%[0-9]+$ && "$pane_pid" =~ ^[0-9]+$ ]] || continue
    if [[ -n "${ancestor_rank[$pane_pid]+x}" ]]; then
      rank="${ancestor_rank[$pane_pid]}"
      if [[ "$rank" -lt "$best_rank" ]]; then
        best_rank="$rank"
        best_pane="$pane"
      fi
    fi
    if [[ -n "$caller_tty" && "$pane_tty" == "$caller_tty" ]]; then
      tty_pane="$pane"
      tty_matches=$((tty_matches + 1))
    fi
  done < <(tmux list-panes -a -F '#{pane_id}|#{pane_pid}|#{pane_tty}' 2>/dev/null || true)

  if [[ -n "$best_pane" ]]; then
    printf '%s\n' "$best_pane"
    return
  fi

  # Wrappers such as CLI launch guards can break the pane-root PID ancestry
  # chain while the caller still owns the pane's controlling TTY. Accept only
  # an exact, unique pane_tty match. This does not consult inherited
  # TMUX_PANE, mutable window names, or pane titles, so a live sibling cannot
  # become the reply route merely because its id is present in the environment.
  [[ "$tty_matches" -eq 1 && -n "$tty_pane" ]] || return 1
  printf '%s\n' "$tty_pane"
}

# Return only the immutable Formation identity locked on a verified pane.
# Callers must prove the pane with mailbox_resolve_caller_pane first.
mailbox_resolve_locked_pane_identity() {
  local pane="${1:-}" identity=""
  [[ "$pane" =~ ^%[0-9]+$ ]] || return 1
  identity="$(tmux display-message -p -t "$pane" \
    '#{@formation_identity_locked}' 2>/dev/null |
    tr -d '\000-\037\177' || true)"
  [[ "$identity" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || return 1
  printf '%s\n' "$identity"
}

# Resolve only the immutable/legacy Formation identity on a verified pane.
# Unlike mailbox_resolve_sender this deliberately has no pane-id, window-name,
# MAILBOX_FROM, or process-name fallback: a spawn reply route must be an
# existing semantic Formation identity.
mailbox_resolve_formation_pane_identity() {
  local pane="${1:-}" identity=""
  [[ "$pane" =~ ^%[0-9]+$ ]] || return 1
  identity="$(tmux display-message -p -t "$pane" \
    '#{?#{@formation_identity_locked},#{@formation_identity_locked},#{@formation_id}}' \
    2>/dev/null | tr -d '\000-\037\177' || true)"
  [[ "$identity" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || return 1
  printf '%s\n' "$identity"
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
  # Word these from the sender's point of view. The healthy branch used to say
  # "signal=pending", which workers read as "not delivered yet" and answered by
  # re-sending a whole verdict, while the failing branch opened with "row is
  # durable" and read like reassurance. Nothing about the delivery logic was
  # wrong — only which outcome sounded alarming (gh #214).
  if mailbox_relay_alive "$relay_pid" "$recipient"; then
    echo "signal=relay-owned relay_pid=$relay_pid pane=$pane (sent; the relay sets the badge, zero keystrokes into prompt)"
    return 0
  fi
  if ! mailbox_signal_pane "$pane" "$seq" "$from"; then
    echo "FAILED (exit 4): pane $pane could not be signaled. The row is durable, so nothing is lost, but $recipient sees no badge until it reads its inbox unprompted." >&2
    return 4
  fi
  echo "signal=sent-directly pane=$pane (sent; no relay, so the sender set the badge itself, zero keystrokes into prompt)"
}

# Signal an already-durable row when its pane route is known. Parent-directed
# worker reports carry the parent pane separately from the semantic parent id;
# ASK/ACK retries can reuse the original row's seq without appending spam.
#
# An absent route is not a transport failure: the immutable mailbox row remains
# available to the mandatory pull rail. A known route that cannot be signaled
# preserves mailbox_signal_or_defer's honest exit 4 contract.
#
# Args: <recipient-label> <pane-or-empty> <seq> <from> <formation-state-dir>
mailbox_signal_durable_row() {
  local recipient="$1" pane="$2" seq="$3" from="$4" formation_dir="$5"
  if [[ ! "$pane" =~ ^%[0-9]+$ ]]; then
    echo "signal=unavailable recipient=$recipient route=absent-or-invalid (row remains durable; pull required)"
    return 0
  fi
  mailbox_signal_or_defer "$recipient" "$pane" "$seq" "$from" "$formation_dir"
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
