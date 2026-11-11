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

# Resolve registered recipients first, then an unregistered live locked identity.
# Disable live lookup for append-only/offline callers.
#
# Args: <target> <registry.jsonl> [allow-direct-pane=0] [allow-live-identity=1]
# Sets:
#   MAILBOX_RECIPIENT_LABEL
#   MAILBOX_RECIPIENT_PANE
#   MAILBOX_RECIPIENT_EXCLUSIVE (0|1)
mailbox_resolve_recipient() {
  local target="$1" registry="$2" allow_direct="${3:-0}"
  local allow_live="${4:-1}" candidate identity dead matches=0 live_pane=""
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

  if [[ -z "$MAILBOX_RECIPIENT_LABEL" && -z "$row" && "$allow_live" == "1" &&
        "$target" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
    # Window names are mutable display text, not routing identities. Require
    # exactly one live pane with the locked identity assigned by the CLI hook.
    while IFS='|' read -r candidate identity dead; do
      [[ "$candidate" =~ ^%[0-9]+$ && "$identity" == "$target" && "$dead" == "0" ]] || continue
      matches=$((matches + 1))
      live_pane="$candidate"
    done < <(tmux list-panes -a -F '#{pane_id}|#{@formation_identity_locked}|#{pane_dead}' 2>/dev/null || true)
    if [[ "$matches" -gt 1 ]]; then
      echo "mailbox: ambiguous live identity '$target' ($matches panes)" >&2
      return 1
    fi
    if [[ "$matches" -eq 1 ]]; then
      MAILBOX_RECIPIENT_LABEL="$target"
      MAILBOX_RECIPIENT_PANE="$live_pane"
      # Discovery grants no exclusive-input authority and writes no registry.
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

# True when the caller is served by a detached server instead of a CLI that
# owns the pane's terminal (#335). Codex gives every tool call its own session,
# so a tool shell of `codex --no-daemon` crosses ONE session leader before it
# reaches the terminal-owning TUI. Under the shared app-server daemon it also
# crosses the daemon's own sessions, and the pane it finally reaches is the
# pane that happened to start the daemon, not the caller's. No process names
# are consulted. A headless caller (cron, CI) is not "detached" unless Codex
# marked the shell with CODEX_THREAD_ID: that is the daemon outliving its pane.
# ponytail: a job a worker itself detached with setsid also matches; the cost
# is a warning (or a refusal while two shared-daemon workers are alive). Use an
# explicit registry marker per Codex thread if that ever bites.
mailbox_caller_behind_detached_server() {
  local pid="$$" guard=0 leaders=0 found_tty=0 ppid sid tty
  while [[ "$guard" -lt 64 && "$pid" =~ ^[0-9]+$ && "$pid" -gt 1 ]]; do
    read -r ppid sid tty < <(ps -o ppid=,sid=,tty= -p "$pid" 2>/dev/null) || break
    case "$tty" in
      ""|"?"|"??") ;;
      *) found_tty=1; break ;;
    esac
    [[ "$sid" == "$pid" ]] && leaders=$((leaders + 1))
    pid="$ppid"
    guard=$((guard + 1))
  done
  [[ "$leaders" -ge 2 && ( "$found_tty" == "1" || -n "${CODEX_THREAD_ID:-}" ) ]]
}

# Count live Codex workers that the shared daemon could be serving: a registry
# row whose pane still carries the same lock and was not spawned isolated.
mailbox_count_shared_daemon_codex() {
  local registry="$1" count=0 pane identity isolated
  [[ -s "$registry" ]] || { echo 0; return; }
  while IFS='|' read -r pane identity isolated; do
    [[ "$pane" =~ ^%[0-9]+$ && -n "$identity" && "$isolated" != "1" ]] || continue
    if jq -c --arg id "$identity" --arg pane "$pane" \
         'select(.id == $id and .pane_id == $pane and .cli == "codex")' \
         "$registry" 2>/dev/null | grep -q .; then
      count=$((count + 1))
    fi
  done < <(tmux list-panes -a \
    -F '#{pane_id}|#{@formation_identity_locked}|#{@formation_codex_isolated}' \
    2>/dev/null || true)
  echo "$count"
}

# Identity gate for every command that sends, reads or changes state as the
# caller (#335). FORMATION_SELF / FORMATION_PARENT / FORMATION_SESSION_ID /
# TMUX_PANE are claims: a Codex tool shell can inherit them from another
# worker. Run this in the main shell before anything consumes them.
#
#   - Caller's ancestry-verified pane is a registered worker's pane and the
#     environment names someone else: rewrite the environment to the verified
#     worker (override, not refusal: the pane lock is the stronger evidence and
#     a Codex shell cannot repair its own environment), say so on stderr and
#     tag every row it sends.
#   - A pane without a worker lock (a lead, a plain shell) keeps the explicit
#     FORMATION_SELF override; that is the documented operator recovery.
#   - Caller is served by a detached server: its pane cannot be verified.
#     With at most one shared-daemon Codex worker alive it is unambiguous and
#     proceeds with a warning and tagged rows; with two or more it is refused.
#
# Args: <registry.jsonl>. Returns 0 to proceed, 8 to refuse.
mailbox_guard_identity() {
  local registry="$1" claimed="${FORMATION_SELF:-}" pane locked row shared value
  if mailbox_caller_behind_detached_server; then
    shared="$(mailbox_count_shared_daemon_codex "$registry")"
    if [[ "$shared" -ge 2 ]]; then
      echo "formation: REFUSED (exit 8): this shell is served by a shared Codex daemon and $shared live Codex workers share it, so FORMATION_SELF=${claimed:-<unset>} may be another worker's identity. Nothing was sent or read. Respawn this worker (formation spawn starts Codex with --no-daemon)." >&2
      return 8
    fi
    echo "formation: warning: this shell is served by a shared Codex daemon; identity ${claimed:-<unset>} comes from the inherited environment and cannot be verified. Rows are tagged unverified. Respawn with --no-daemon to fix." >&2
    export MAILBOX_IDENTITY_NOTE="unverified:codex-shared-daemon"
    return 0
  fi
  [[ -n "$claimed" && -s "$registry" ]] || return 0
  pane="$(mailbox_resolve_caller_pane || true)"
  locked="$(mailbox_resolve_locked_pane_identity "$pane" || true)"
  [[ -n "$locked" && "$locked" != "$claimed" ]] || return 0
  row="$(jq -c --arg id "$locked" --arg pane "$pane" \
    'select(.id == $id and .pane_id == $pane)' "$registry" 2>/dev/null | tail -n1)"
  [[ -n "$row" ]] || return 0
  echo "formation: warning: environment claims FORMATION_SELF=$claimed but this shell runs in pane $pane, locked to worker $locked; acting as $locked." >&2
  export FORMATION_SELF="$locked" MAILBOX_IDENTITY_NOTE="env-claimed:$claimed"
  unset FORMATION_PARENT FORMATION_PARENT_PANE FORMATION_SESSION_ID
  value="$(jq -r '.parent_id // empty' <<<"$row")"
  [[ -n "$value" ]] && export FORMATION_PARENT="$value"
  value="$(jq -r '.parent_pane // empty' <<<"$row")"
  [[ -n "$value" ]] && export FORMATION_PARENT_PANE="$value"
  value="$(jq -r '.session_id // empty' <<<"$row")"
  [[ -n "$value" ]] && export FORMATION_SESSION_ID="$value"
  return 0
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
    echo "signal=relay-owned relay_pid=$relay_pid pane=$pane receipt=unconfirmed recipient_activity=unknown (signal sent; the relay sets the badge with zero prompt keystrokes, but inbox read is not proven)"
    return 0
  fi
  if ! mailbox_signal_pane "$pane" "$seq" "$from"; then
    echo "FAILED (exit 4): receipt=unconfirmed recipient_activity=unknown; pane $pane could not be signaled. The row is durable, so nothing is lost, but $recipient sees no badge until it reads its inbox unprompted." >&2
    return 4
  fi
  echo "signal=sent-directly pane=$pane receipt=unconfirmed recipient_activity=unknown (signal sent; the sender set the badge with zero prompt keystrokes, but inbox read is not proven)"
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
    echo "signal=unavailable recipient=$recipient route=absent-or-invalid receipt=unconfirmed recipient_activity=unknown (row remains durable; pull required)"
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
     tmux_send_nudge "$pane" "$from" "$seq" "$notify"; then
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

# Wake a nonexclusive worker only for the answer to the exact ASK on which it
# is durably WAITING_PARENT. This is the sole exception to the exclusive-input
# gate. The caller supplies the pre-transition snapshot and invokes this only
# when request_transition reports that it performed the first close.
#
# Eligibility is deliberately rechecked at the injection boundary:
#   1. the request is open and WAITING_PARENT;
#   2. request_id and worker_id identify the target worker's own ASK;
#   3. the sender is the parent_id recorded on that ASK.
#
# Args: <pane> <seq> <from> <request-id> <worker-id> <pre-transition-json>
mailbox_inject_waiting_request_nudge() {
  local pane="$1" seq="$2" from="$3" request_id="$4" worker="$5" current="$6"
  if ! printf '%s\n' "$current" | jq -e \
      --arg request "$request_id" --arg worker "$worker" --arg parent "$from" '
        .closed == false
        and .state == "WAITING_PARENT"
        and .request_id == $request
        and .worker_id == $worker
        and .parent_id == $parent
      ' >/dev/null 2>&1; then
    echo "WARN (exit 4): ASK reply row is durable, but nonexclusive nudge authorization no longer matches request=$request_id worker=$worker parent=$from." >&2
    return 4
  fi
  if [[ ! "$pane" =~ ^%[0-9]+$ ]]; then
    echo "WARN (exit 4): ASK reply row is durable, but worker=$worker has no valid pane route for its pull nudge." >&2
    return 4
  fi

  local notify="ASK reply ready for request=${request_id}; pull with formation inbox (receipt unconfirmed)"
  local submit_rc=0
  if tmux_send_nudge "$pane" "$from" "$seq" "$notify"; then
    echo "ask-nudge=attempted pane=$pane request=$request_id (short pull nudge only; receipt unconfirmed)"
    return 0
  else
    submit_rc=$?
  fi
  if [[ "$submit_rc" -eq "${TMUX_SUBMIT_PASTED_UNCONFIRMED:-11}" ]]; then
    echo "WARN (exit 4): ASK reply nudge was pasted to pane=$pane but submit is unconfirmed; DO NOT RETRY automatically (row remains durable)." >&2
  else
    echo "WARN (exit 4): ASK reply row is durable, but its pull nudge was not pasted to pane=$pane." >&2
  fi
  return 4
}
