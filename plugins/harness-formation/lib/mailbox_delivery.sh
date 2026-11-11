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

# The caller's proven pane, or failure. Kept for the existing call sites; the
# proof itself (and the reason there is no controlling-TTY fallback any more)
# lives in mailbox_prove_caller_pane.
mailbox_resolve_caller_pane() {
  mailbox_prove_caller_pane || return 1
  [[ -n "$MAILBOX_CALLER_PANE" ]] || return 1
  printf '%s\n' "$MAILBOX_CALLER_PANE"
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

# --- caller identity (#335) ---------------------------------------------------
#
# FORMATION_SELF / FORMATION_PARENT / FORMATION_PARENT_PANE /
# FORMATION_SESSION_ID / TMUX_PANE are claims: a Codex tool shell attached to
# the shared app-server daemon inherits all of them from another worker. The
# functions below decide who the caller is from things the caller's
# environment does not get to choose.

# The Formation home that the identity gate reads its registry from. It comes
# from the account database, not from $HOME or $FORMATION_HOME.
# ponytail: getent only; hosts without it fall back to $HOME.
mailbox_canonical_home() {
  local home=""
  home="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"
  printf '%s/.formation\n' "${home:-$HOME}"
}

# Test fixtures. A test cannot use the canonical home, so it declares
# FORMATION_TEST_FIXTURE=1 and points FORMATION_HOME (and, if it sets them,
# the mailbox / registry / request / review paths) somewhere else. The
# declaration is honoured only when NONE of those paths is inside the
# canonical home: whatever a command then reads or writes is fixture state.
# That is why a worker shell gains nothing from it: it can forge mail in a
# scratch directory nobody reads, never in the live mailbox, registry or
# request log. (A shell that can write those files directly needs no gate to
# forge them; this gate covers what the CLI does on a caller's behalf.)
mailbox_test_fixture_active() {
  local canonical path
  [[ "${FORMATION_TEST_FIXTURE:-}" == "1" && -n "${FORMATION_HOME:-}" ]] || return 1
  canonical="$(realpath -m "$(mailbox_canonical_home)")"
  for path in "$FORMATION_HOME" "${FORMATION_MAILBOX:-}" "${FORMATION_REGISTRY:-}" \
              "${FORMATION_REQUEST_DIR:-}" "${FORMATION_REQUEST_LOG:-}" \
              "${FORMATION_REVIEW_DIR:-}" "${FORMATION_REVIEW_LOG:-}"; do
    [[ -n "$path" ]] || continue
    path="$(realpath -m "$path")"
    [[ "$path" != "$canonical" && "$path" != "$canonical"/* ]] || return 1
  done
  return 0
}

# Prove which pane the caller belongs to. Sets, in the current shell:
#   MAILBOX_CALLER_PANE      the pane whose own process is the caller's nearest
#                            pane ancestor, "" when the caller is in no pane
#   MAILBOX_CALLER_SESSIONS  distinct process sessions on the path between the
#                            caller and that pane's own session
# Returns 0 when the answer is established (including "in no pane"), 1 when it
# could not be established; MAILBOX_CALLER_PROOF_ERROR then says why.
#
# What is checked, and from where:
#   - The process chain comes from the process table. If it cannot be read,
#     nothing is established.
#   - Whether the caller is inside tmux does not depend on $TMUX. Every tmux
#     server with a socket in the user's socket directory (/tmp/tmux-<uid>),
#     plus the one $TMUX names, is asked for its panes, and a pane counts when
#     its own process is an ancestor of the caller. So a shell in a pane
#     cannot become "outside tmux" by unsetting TMUX: the pane is still found,
#     and a caller that is in a pane while $TMUX is unset or names another
#     server is not established. Limit: a server on a socket outside that
#     directory is only seen through $TMUX.
#   - There is no controlling-terminal fallback and terminals are not looked
#     at on the path: a pane is proven by ancestry or not at all.
#   - A caller nested inside panes of two servers is not established.
#
# What the session count does and does not show: a CLI that owns the pane runs
# in the pane's session and gives a tool call at most one session of its own.
# Codex's shared app-server daemon puts its own sessions in between, and every
# session attached to it descends from the one pane that started it. Refusing
# two or more foreign sessions therefore rejects that daemon as it exists in
# codex-cli 0.167. It is a property of the path, not an attribution proof: a
# shared server that served several panes while adding only one session would
# pass. Formation's own Codex workers are started with --no-daemon, which
# removes the shared server instead of relying on this count.
mailbox_prove_caller_pane() {
  local pid="$$" guard=0 next sid line pane pane_pid dead index root_sid socket
  local tmux_socket="" socket_dir out matched_socket=""
  local -a chain_pid=() chain_sid=() sockets=()
  local -A pane_by_pid=() socket_by_pid=() seen_sid=() matched=()
  MAILBOX_CALLER_PANE=""
  MAILBOX_CALLER_SESSIONS=0
  MAILBOX_CALLER_PROOF_ERROR=""

  while [[ "$guard" -lt 64 && "$pid" =~ ^[0-9]+$ && "$pid" -gt 1 ]]; do
    read -r next sid < <(ps -o ppid=,sid= -p "$pid" 2>/dev/null) || true
    if [[ ! "${next:-}" =~ ^[0-9]+$ || ! "${sid:-}" =~ ^[0-9]+$ ]]; then
      MAILBOX_CALLER_PROOF_ERROR="the process table could not be read for pid $pid"
      return 1
    fi
    chain_pid+=("$pid")
    chain_sid+=("$sid")
    pid="$next"
    next="" sid=""
    guard=$((guard + 1))
  done

  [[ -n "${TMUX:-}" ]] && tmux_socket="$(realpath -m "${TMUX%%,*}")"
  if mailbox_test_fixture_active; then
    socket_dir="$FORMATION_HOME/tmux"
  else
    socket_dir="/tmp/tmux-$(id -u)"
  fi
  [[ -n "$tmux_socket" ]] && sockets+=("$tmux_socket")
  # Only sockets with a listening server behind them are worth asking; test
  # runs leave many dead socket files behind. Linux lists bound sockets in
  # /proc/net/unix; elsewhere every socket file is tried.
  local listening=""
  [[ -r /proc/net/unix ]] && listening="$(grep -F " $socket_dir/" /proc/net/unix 2>/dev/null || true)"
  for socket in "$socket_dir"/*; do
    [[ -S "$socket" ]] || continue
    [[ ! -r /proc/net/unix || "$listening" == *" $socket"* ]] || continue
    socket="$(realpath -m "$socket")"
    [[ "$socket" == "$tmux_socket" ]] || sockets+=("$socket")
  done

  for socket in "${sockets[@]}"; do
    if [[ "$socket" == "$tmux_socket" ]]; then
      # The server the rest of the command will talk to must answer.
      if ! out="$(tmux list-panes -a -F '#{pane_id}|#{pane_pid}|#{pane_tty}|#{pane_dead}' 2>/dev/null)"; then
        MAILBOX_CALLER_PROOF_ERROR="the tmux server named by \$TMUX did not answer"
        return 1
      fi
    else
      # A socket file without a server behind it is common and harmless.
      out="$(tmux -S "$socket" list-panes -a -F '#{pane_id}|#{pane_pid}|#{pane_tty}|#{pane_dead}' 2>/dev/null)" || continue
    fi
    while IFS= read -r line; do
      IFS='|' read -r pane pane_pid _ dead <<<"$line"
      [[ "$pane" =~ ^%[0-9]+$ && "$pane_pid" =~ ^[0-9]+$ && "$dead" != "1" ]] || continue
      [[ -n "${pane_by_pid[$pane_pid]+x}" ]] && continue
      pane_by_pid["$pane_pid"]="$pane"
      socket_by_pid["$pane_pid"]="$socket"
    done <<<"$out"
  done

  for index in "${!chain_pid[@]}"; do
    pid="${chain_pid[$index]}"
    [[ -n "${pane_by_pid[$pid]+x}" ]] || continue
    matched["${socket_by_pid[$pid]}"]=1
    [[ -z "$MAILBOX_CALLER_PANE" ]] || continue
    MAILBOX_CALLER_PANE="${pane_by_pid[$pid]}"
    matched_socket="${socket_by_pid[$pid]}"
    root_sid="${chain_sid[$index]}"
    for sid in "${chain_sid[@]:0:$index}"; do
      [[ "$sid" != "$root_sid" ]] && seen_sid["$sid"]=1
    done
    MAILBOX_CALLER_SESSIONS="${#seen_sid[@]}"
  done

  if [[ -z "$MAILBOX_CALLER_PANE" ]]; then
    [[ -z "$tmux_socket" ]] && return 0
    MAILBOX_CALLER_PROOF_ERROR="\$TMUX is set but no pane of that server is an ancestor of this shell"
    return 1
  fi
  if [[ "${#matched[@]}" -gt 1 ]]; then
    MAILBOX_CALLER_PANE=""
    MAILBOX_CALLER_PROOF_ERROR="this shell is nested inside panes of more than one tmux server"
    return 1
  fi
  if [[ "$matched_socket" != "$tmux_socket" ]]; then
    line="$MAILBOX_CALLER_PANE"
    MAILBOX_CALLER_PANE=""
    MAILBOX_CALLER_PROOF_ERROR="this shell runs in pane $line of the tmux server at $matched_socket, but \$TMUX ${tmux_socket:+names another server}${tmux_socket:-is unset}"
    return 1
  fi
  return 0
}

_mailbox_refuse_identity() {
  echo "formation: REFUSED (exit 8): $1. Caller identity (FORMATION_SELF=${FORMATION_SELF:-<unset>}) cannot be verified; nothing was sent or read." >&2
  return 8
}

# Identity gate. Run it in the calling shell before anything consumes the
# caller's identity; mailbox_require_identity does that for you.
#
# Outcomes (MAILBOX_CALLER_CONTEXT):
#   worker    the proven pane is a registered worker's pane. Self, parent,
#             parent pane and session id are all set from that registry row;
#             the environment's values are discarded together. A contradicting
#             FORMATION_SELF is named on stderr and tagged on rows sent.
#   lead      the pane is proven, its lock and the registry were read
#             successfully, and the pane has no worker row and no worker
#             marker. Only here, and for "operator", do FORMATION_SELF and
#             --from apply. Lead status is a finding, never a default.
#   operator  the caller is in no tmux pane at all (see
#             mailbox_prove_caller_pane for why unsetting TMUX does not get a
#             pane shell here).
# Everything else is refused with exit 8, for every CLI type: a pane that
# cannot be established, a detached server on the path, a lock or registry
# that cannot be read, a worker pane without its row, and a caller in a pane
# that points FORMATION_HOME / FORMATION_REGISTRY away from the canonical home
# (the caller does not choose the registry that judges it).
#
# The registry is always the canonical one (or the fixture's, see
# mailbox_test_fixture_active). No arguments.
mailbox_guard_identity() {
  local claimed="${FORMATION_SELF:-}" pane locked parent_marker registry canonical
  local rows row key value fixture=0
  MAILBOX_CALLER_WORKER=""
  MAILBOX_CALLER_CONTEXT=""
  _MAILBOX_GATE_PASSED=""
  unset MAILBOX_IDENTITY_NOTE

  mailbox_test_fixture_active && fixture=1
  if ! mailbox_prove_caller_pane; then
    # Test-only: a fixture's driver process that talks to the fixture's tmux
    # server from outside its panes. Inert without an active fixture.
    if [[ "$fixture" == "1" && "${FORMATION_TEST_OPERATOR:-}" == "1" ]]; then
      MAILBOX_CALLER_CONTEXT="operator"
      _MAILBOX_GATE_PASSED="$$"
      return 0
    fi
    _mailbox_refuse_identity "$MAILBOX_CALLER_PROOF_ERROR"
    return 8
  fi
  pane="$MAILBOX_CALLER_PANE"
  if [[ -z "$pane" ]]; then
    MAILBOX_CALLER_CONTEXT="operator"
    _MAILBOX_GATE_PASSED="$$"
    return 0
  fi
  if [[ "$MAILBOX_CALLER_SESSIONS" -ge 2 ]]; then
    _mailbox_refuse_identity "this shell reaches pane $pane only through a detached server ($MAILBOX_CALLER_SESSIONS process sessions in between, e.g. the shared Codex daemon), so it may belong to another worker. It cannot be repaired in place: respawn the worker ('formation spawn' starts Codex with --no-daemon)"
    return 8
  fi

  canonical="$(mailbox_canonical_home)"
  if [[ "$fixture" == "1" ]]; then
    registry="${FORMATION_REGISTRY:-$FORMATION_HOME/formation/registry.jsonl}"
  else
    registry="$canonical/formation/registry.jsonl"
    for value in "${FORMATION_HOME:-}" "${FORMATION_REGISTRY:-}"; do
      [[ -n "$value" ]] || continue
      value="$(realpath -m "$value")"
      if [[ "$value" != "$(realpath -m "$canonical")" && "$value" != "$(realpath -m "$registry")" ]]; then
        _mailbox_refuse_identity "FORMATION_HOME / FORMATION_REGISTRY point away from $canonical while this shell runs in pane $pane; a caller does not choose the registry that identifies it"
        return 8
      fi
    done
  fi

  # One read for the lock and the marker that formation spawn leaves on a
  # worker's pane.
  if ! locked="$(tmux display-message -p -t "$pane" \
         '#{@formation_identity_locked}|#{@formation_parent_id}' 2>/dev/null)"; then
    _mailbox_refuse_identity "the identity lock of pane $pane could not be read"
    return 8
  fi
  locked="$(printf '%s' "$locked" | tr -d '\000-\037\177')"
  parent_marker=""
  [[ "$locked" == *"|"* ]] && parent_marker="${locked#*|}"
  locked="${locked%%|*}"
  rows="[]"
  if [[ -e "$registry" ]] && ! rows="$(jq -sc . "$registry" 2>/dev/null)"; then
    _mailbox_refuse_identity "the registry $registry could not be read"
    return 8
  fi

  row="$(jq -c --arg id "$locked" --arg pane "$pane" \
    'map(select(.id == $id and .pane_id == $pane)) | last // empty' <<<"$rows")"
  if [[ -z "$row" ]]; then
    if [[ -n "$parent_marker" ]] || jq -e --arg id "$locked" --arg pane "$pane" \
         'any(.[]; .pane_id == $pane or ($id != "" and .id == $id))' <<<"$rows" >/dev/null; then
      _mailbox_refuse_identity "pane $pane is marked as a worker pane (lock '${locked:-<none>}') but the registry has no matching row for it"
      return 8
    fi
    MAILBOX_CALLER_CONTEXT="lead"
    _MAILBOX_GATE_PASSED="$$"
    return 0
  fi

  [[ "$locked" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
    _mailbox_refuse_identity "pane $pane carries an invalid identity lock"
    return 8
  }
  MAILBOX_CALLER_WORKER="$locked"
  MAILBOX_CALLER_CONTEXT="worker"
  if [[ -n "$claimed" && "$claimed" != "$locked" ]]; then
    echo "formation: warning: environment claims FORMATION_SELF=$claimed but this shell runs in pane $pane, locked to worker $locked; acting as $locked." >&2
    export MAILBOX_IDENTITY_NOTE="env-claimed:$claimed"
  fi
  export FORMATION_SELF="$locked"
  for key in parent_id:FORMATION_PARENT parent_pane:FORMATION_PARENT_PANE \
             session_id:FORMATION_SESSION_ID; do
    value="$(jq -r --arg key "${key%%:*}" '.[$key] // empty' <<<"$row")"
    if [[ -n "$value" ]]; then
      export "${key##*:}=$value"
    else
      unset "${key##*:}"
    fi
  done
  _MAILBOX_GATE_PASSED="$$"
  return 0
}

# Every identity-consuming command starts with mailbox_require_identity, in
# its own shell, before it reads the caller's identity. The pass marker is a
# plain shell variable holding this process's pid: the gate clears it first
# and sets it only after it succeeded, it is never exported, and a value
# inherited through the environment (another pid) is not accepted.
mailbox_identity_gated() {
  [[ "${_MAILBOX_GATE_PASSED:-}" == "$$" ]]
}
mailbox_require_identity() {
  mailbox_guard_identity
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
