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
  mailbox_prove_caller_pane || return 1   # cached: proven once per process
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
# Threat model: this gate stops ACCIDENTAL wrong identity - an environment
# inherited from Codex's shared daemon, a lost registry row, a stale variable.
# It is not a boundary against a worker that sets out to evade it: the mailbox
# is a plain file every worker can append to.
#
# FORMATION_SELF / FORMATION_PARENT / FORMATION_PARENT_PANE /
# FORMATION_SESSION_ID / TMUX_PANE are claims: a Codex tool shell attached to
# the shared app-server daemon inherits all of them from another worker.

# Nothing identity-related is trusted from the environment this library was
# loaded in; both are plain shell variables, set by the functions below.
unset _MAILBOX_GATE_PASSED _MAILBOX_PROOF_PID

# The registry the identity gate reads: the one under the home directory from
# the account database, whatever $HOME or $FORMATION_HOME say. A caller in a
# pane that points FORMATION_HOME / FORMATION_REGISTRY elsewhere (a stale
# variable) is refused rather than judged by a registry of its own choosing.
# ponytail: getent only; hosts without it fall back to $HOME.
#
# Tests cannot use the canonical home. FORMATION_TEST_FIXTURE=1 together with
# an explicit FORMATION_HOME lets the test's own registry through. It is a
# convenience for tests that run from a worker's pane, not isolation and not a
# security control.
# Prints the registry path; returns 1 when the environment points elsewhere.
mailbox_identity_registry() {
  local home="" canonical
  home="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"
  canonical="$(realpath -m "${home:-$HOME}/.formation")"
  if [[ "${FORMATION_TEST_FIXTURE:-}" == "1" && -n "${FORMATION_HOME:-}" ]]; then
    printf '%s\n' "${FORMATION_REGISTRY:-$FORMATION_HOME/formation/registry.jsonl}"
    return 0
  fi
  [[ -z "${FORMATION_HOME:-}" || "$(realpath -m "$FORMATION_HOME")" == "$canonical" ]] || return 1
  [[ -z "${FORMATION_REGISTRY:-}" ||
     "$(realpath -m "$FORMATION_REGISTRY")" == "$canonical/formation/registry.jsonl" ]] || return 1
  printf '%s\n' "$canonical/formation/registry.jsonl"
}

# Prove which pane the caller belongs to, once per process. Sets:
#   MAILBOX_CALLER_PANE      the nearest pane whose own process is an ancestor
#                            of the caller; "" when $TMUX is unset
#   MAILBOX_CALLER_SESSIONS  distinct process sessions on the path between the
#                            caller and that pane's own session
# Returns 1, with MAILBOX_CALLER_PROOF_ERROR, when $TMUX is set and no pane
# could be established (process table or tmux unreadable, no pane ancestor).
#
# $TMUX unset means "not in tmux": an operator's terminal or cron. A shell
# that lost TMUX inside a pane is not detected; no accidental way to lose it
# while keeping another worker's FORMATION_SELF is known.
# There is no controlling-terminal fallback (#216's was removed): a pane is
# proven by ancestry or not at all.
#
# What the session count shows: a CLI that owns the pane runs in the pane's
# session and gives a tool call at most one session of its own. Codex's
# shared app-server daemon adds its own sessions, and every session attached
# to it descends from the one pane that started it. Refusing two or more
# foreign sessions rejects that daemon as it exists in codex-cli 0.167. It is
# a property of the path, not an attribution proof: a shared server that added
# only one session would pass. Formation's own Codex workers are started with
# --no-daemon, which removes the shared server instead of relying on this.
mailbox_prove_caller_pane() {
  local pid="$$" guard=0 next sid line pane pane_pid dead index root_sid out
  local -a chain_pid=() chain_sid=()
  local -A pane_by_pid=() seen_sid=()
  if [[ -n "${_MAILBOX_PROOF_PID:-}" ]]; then
    [[ -z "$MAILBOX_CALLER_PROOF_ERROR" ]]
    return
  fi
  MAILBOX_CALLER_PANE=""
  MAILBOX_CALLER_SESSIONS=0
  MAILBOX_CALLER_PROOF_ERROR=""
  _MAILBOX_PROOF_PID=1
  [[ -n "${TMUX:-}" ]] || return 0

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
  if ! out="$(tmux list-panes -a -F '#{pane_id}|#{pane_pid}|#{pane_tty}|#{pane_dead}' 2>/dev/null)"; then
    MAILBOX_CALLER_PROOF_ERROR="the tmux server named by \$TMUX did not answer"
    return 1
  fi
  while IFS= read -r line; do
    IFS='|' read -r pane pane_pid _ dead <<<"$line"
    [[ "$pane" =~ ^%[0-9]+$ && "$pane_pid" =~ ^[0-9]+$ && "$dead" != "1" ]] || continue
    pane_by_pid["$pane_pid"]="$pane"
  done <<<"$out"

  for index in "${!chain_pid[@]}"; do
    [[ -n "${pane_by_pid[${chain_pid[$index]}]+x}" ]] || continue
    MAILBOX_CALLER_PANE="${pane_by_pid[${chain_pid[$index]}]}"
    root_sid="${chain_sid[$index]}"
    for sid in "${chain_sid[@]:0:$index}"; do
      [[ "$sid" != "$root_sid" ]] && seen_sid["$sid"]=1
    done
    MAILBOX_CALLER_SESSIONS="${#seen_sid[@]}"
    return 0
  done
  MAILBOX_CALLER_PROOF_ERROR="\$TMUX is set but no pane of that server is an ancestor of this shell (a wrapper that breaks the process chain, or an orphaned job)"
  return 1
}

_mailbox_refuse_identity() {
  echo "formation: REFUSED (exit 8): $1. Caller identity (FORMATION_SELF=${FORMATION_SELF:-<unset>}) cannot be verified; nothing was sent or read." >&2
  return 8
}

# Identity gate. mailbox_require_identity runs it as the first line of every
# identity-consuming command, in that command's own shell.
#
# Outcomes (MAILBOX_CALLER_CONTEXT):
#   operator  $TMUX is unset. The environment applies.
#   worker    the proven pane has a registry row with the pane's lock. Self,
#             parent, parent pane and session id are all set from that row;
#             the environment's values are discarded together. A
#             contradicting FORMATION_SELF is named on stderr and tagged on
#             rows sent.
#   pane      the proven pane has an identity lock and nothing says it is a
#             worker: the caller acts as that lock and nothing else.
#             FORMATION_SELF is overridden and --from is refused.
#   lead      the proven pane has no lock at all. Only here, and for
#             "operator", do FORMATION_SELF and --from apply.
# "pane" and "lead" are findings, never defaults: they need the registry to
# parse and to have the expected shape, no row for this pane and no spawn
# marker on the pane. In every context a lock that is registered to another
# pane is refused. Everything else is refused with exit 8, for every CLI type.
# A missing registry file is refused here; bin/formation creates an empty one
# at start-up (the bootstrap for the first register/spawn), so through the
# CLI "missing" behaves as "empty" and the lost-row protection rests on the
# spawn marker and the lock.
mailbox_guard_identity() {
  local claimed="${FORMATION_SELF:-}" pane locked parent_marker registry rows row key value
  local reregister="If the registry was reset on purpose, register this pane again: formation register --cli <cli> <id>"
  MAILBOX_CALLER_WORKER=""
  MAILBOX_CALLER_CONTEXT=""
  _MAILBOX_GATE_PASSED=""
  unset MAILBOX_IDENTITY_NOTE

  if ! mailbox_prove_caller_pane; then
    # Test-only: a fixture's driver process that talks to the fixture's tmux
    # server (or a stub) from outside its panes.
    if [[ "${FORMATION_TEST_FIXTURE:-}" == "1" && "${FORMATION_TEST_OPERATOR:-}" == "1" ]] &&
       mailbox_identity_registry >/dev/null; then
      MAILBOX_CALLER_CONTEXT="operator"
      _MAILBOX_GATE_PASSED=1
      return 0
    fi
    _mailbox_refuse_identity "$MAILBOX_CALLER_PROOF_ERROR"
    return 8
  fi
  pane="$MAILBOX_CALLER_PANE"
  if [[ -z "$pane" ]]; then
    MAILBOX_CALLER_CONTEXT="operator"
    _MAILBOX_GATE_PASSED=1
    return 0
  fi
  if [[ "$MAILBOX_CALLER_SESSIONS" -ge 2 ]]; then
    _mailbox_refuse_identity "this shell reaches pane $pane only through a detached server ($MAILBOX_CALLER_SESSIONS process sessions in between, e.g. the shared Codex daemon), so it may belong to another worker. It cannot be repaired in place: respawn the worker ('formation spawn' starts Codex with --no-daemon)"
    return 8
  fi
  if ! registry="$(mailbox_identity_registry)"; then
    _mailbox_refuse_identity "FORMATION_HOME / FORMATION_REGISTRY point away from the canonical Formation home while this shell runs in pane $pane (a stale variable?)"
    return 8
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
  if ! rows="$(jq -sce 'if all(.[]; type == "object" and (.id | type) == "string"
                               and (.pane_id | type) == "string")
                        then . else error("shape") end' "$registry" 2>/dev/null)"; then
    _mailbox_refuse_identity "the registry $registry is missing, unreadable or not a list of worker rows. $reregister"
    return 8
  fi

  # One id, one pane. Two concurrent spawns with the same name used to leave
  # two rows; both panes would then share a mailbox identity.
  if [[ -n "$locked" ]] && jq -e --arg id "$locked" --arg pane "$pane" \
       'any(.[]; .id == $id and .pane_id != $pane)' <<<"$rows" >/dev/null; then
    _mailbox_refuse_identity "identity '$locked' is registered to another pane as well as locked on pane $pane. $reregister"
    return 8
  fi
  row="$(jq -c --arg id "$locked" --arg pane "$pane" \
    'map(select(.id == $id and .pane_id == $pane)) | last // empty' <<<"$rows")"
  if [[ -z "$row" ]]; then
    if [[ -n "$parent_marker" ]] || jq -e --arg pane "$pane" \
         'any(.[]; .pane_id == $pane)' <<<"$rows" >/dev/null; then
      _mailbox_refuse_identity "pane $pane is a worker pane (lock '${locked:-<none>}') but the registry has no matching row for it. $reregister"
      return 8
    fi
    if [[ -z "$locked" ]]; then
      MAILBOX_CALLER_CONTEXT="lead"
      _MAILBOX_GATE_PASSED=1
      return 0
    fi
    MAILBOX_CALLER_CONTEXT="pane"
  else
    MAILBOX_CALLER_CONTEXT="worker"
  fi

  [[ "$locked" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
    _mailbox_refuse_identity "pane $pane carries an invalid identity lock"
    return 8
  }
  MAILBOX_CALLER_WORKER="$locked"
  if [[ -n "$claimed" && "$claimed" != "$locked" ]]; then
    echo "formation: warning: environment claims FORMATION_SELF=$claimed but this shell runs in pane $pane, locked to $locked; acting as $locked. To act as another id (operator recovery), run the command from a plain shell pane without an identity lock, or outside tmux." >&2
    export MAILBOX_IDENTITY_NOTE="env-claimed:$claimed"
  fi
  export FORMATION_SELF="$locked"
  if [[ -n "$row" ]]; then
    for key in parent_id:FORMATION_PARENT parent_pane:FORMATION_PARENT_PANE \
               session_id:FORMATION_SESSION_ID; do
      value="$(jq -r --arg key "${key%%:*}" '.[$key] // empty' <<<"$row")"
      if [[ -n "$value" ]]; then
        export "${key##*:}=$value"
      else
        unset "${key##*:}"
      fi
    done
  fi
  _MAILBOX_GATE_PASSED=1
  return 0
}

# The pass marker is a plain shell variable: the gate clears it first and
# sets it only after it succeeded, it is never exported, and loading this
# library drops any inherited value.
mailbox_identity_gated() {
  [[ -n "${_MAILBOX_GATE_PASSED:-}" ]]
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
