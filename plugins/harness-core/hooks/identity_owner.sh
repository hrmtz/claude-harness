#!/usr/bin/env bash
# identity_owner.sh — the single claim/rename authority for tmux chassis identity.
#
# Background (#95). Four adapters — the claude SessionStart hook, the codex
# SessionStart hook, the kimi wrapper and harness-cross-cli — each answered
# "does this pane belong to me?" with a different proxy: a `ps comm` name list, a
# window-label prefix, a sentinel file. Every proxy had a blind spot that only
# appeared once a new wrapper existed, so each fix held until the next surface
# (kimi --help stealing a window, a kimi child stamping a codex parent, a
# `claude -p` under `claude-harness-codex-real` slipping past the name list).
#
# The cure is to stop inferring ownership and start recording it. A claim writes
# the owning CLI's process identity onto the pane; a later claimant asks whether
# that process is still alive and whether it is one of its own ancestors. That
# question has no string in it, so a wrapper's name and the 15-character `comm`
# truncation stop mattering.
#
# Pane options written by a claim:
#   @harness_chassis            claude | codex | kimi | grok
#   @harness_owner_key          <pid>:<start-token> of the owning CLI process
#   @formation_identity_locked  routing id (unchanged semantics)
#   @formation_id               compatibility alias (unchanged)
#
# Usage from an adapter:
#   . "$(dirname "$0")/../lib/identity_owner.sh"
#   harness_identity_resolve --pane "$TMUX_PANE" --chassis codex --mode session-start
#   case "$HARNESS_IDENTITY_DECISION" in
#       CLAIM) harness_identity_apply ;;
#       *)     exit 0 ;;
#   esac
#
# Adapters declare a chassis and a mode. They do not call tmux rename-window,
# tmux set-option, or read a sentinel themselves.

# Deliberately no `set -e`: this file is sourced into hooks and wrappers whose
# own failure semantics differ, and an identity decision must never abort a CLI
# launch.

HARNESS_IDENTITY_SENTINEL_DIR="${HARNESS_IDENTITY_SENTINEL_DIR:-$HOME/.local/state/tmux_self_name}"
HARNESS_IDENTITY_DECISION_LOG_MAX="${HARNESS_IDENTITY_DECISION_LOG_MAX:-400}"

# ── kill switch ──────────────────────────────────────────────────────────────
# One canonical name plus every legacy per-family name, honoured for all four
# chassis. Before this, GROK_TMUX_NAME_DISABLE was exported by harness-cross-cli
# and read by nobody.
harness_identity_disabled() {
    [ "${HARNESS_TMUX_SELF_NAME_DISABLE:-0}" = "1" ] && return 0
    [ "${CLAUDE_TMUX_NAME_DISABLE:-0}" = "1" ] && return 0
    [ "${CODEX_TMUX_NAME_DISABLE:-0}" = "1" ] && return 0
    [ "${KIMI_TMUX_NAME_DISABLE:-0}" = "1" ] && return 0
    [ "${GROK_TMUX_NAME_DISABLE:-0}" = "1" ] && return 0
    [ "${HIPPOCAMPUS_TMUX_NAME_DISABLE:-0}" = "1" ] && return 0
    return 1
}

# ── process identity ─────────────────────────────────────────────────────────
# A bare pid is not an identity: pids are recycled, and a stale pane option can
# name a pid that some unrelated process now holds. `respawn-pane` was measured
# to carry both ownership options across a respawn while the recorded process
# was long gone, so a pane can hold a stale pid for days. Pair the pid with the
# process start time, and the start time with the boot id — start time is
# jiffies-since-boot, so it only identifies a process within one boot, and a
# session-restore plugin replaying pane options across a reboot would otherwise
# resurrect an ownership claim that nothing holds.
_hi_boot_id() {
    if [ -r /proc/sys/kernel/random/boot_id ]; then
        head -c 8 /proc/sys/kernel/random/boot_id 2>/dev/null
        return 0
    fi
    # macOS: kern.boottime is stable for the life of the boot.
    sysctl -n kern.boottime 2>/dev/null | tr -cd '0-9' | cut -c1-10
}

_hi_proc_start_token() {
    local pid="$1" stat
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    if [ -r "/proc/$pid/stat" ]; then
        # Field 22 (starttime, jiffies since boot). The comm field can contain
        # spaces and parentheses, so split on the last ')' rather than on wsp.
        stat=$(cat "/proc/$pid/stat" 2>/dev/null) || return 1
        stat="${stat#*) }"
        printf '%s\n' "$stat" | awk '{ print $20 }'
        return 0
    fi
    # macOS and anything else without procfs.
    ps -o lstart= -p "$pid" 2>/dev/null | tr -s ' ' '_' | sed 's/^_//;s/_$//'
}

_hi_proc_key() {
    local pid="$1" token
    token=$(_hi_proc_start_token "$pid" 2>/dev/null) || return 1
    [ -n "$token" ] || return 1
    printf '%s:%s:%s\n' "$pid" "$token" "$(_hi_boot_id)"
}

_hi_key_alive() {
    local key="$1" pid current
    [ -n "$key" ] || return 1
    pid="${key%%:*}"
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    kill -0 "$pid" 2>/dev/null || return 1
    current=$(_hi_proc_key "$pid" 2>/dev/null) || return 1
    [ "$current" = "$key" ]
}

# Ancestor pids of the current process, nearest first. `$$` is excluded: the
# caller is asking about processes that started it.
#
# The walk stops at the pane's own process when one is known. Nothing above a
# pane's shell can own that pane's identity — it is tmux, a login shell, init —
# and walking past it lets whatever happens to be running the whole terminal
# (a Claude Code session running a codex test, say) read as a foreign owner.
_hi_ancestors() {
    local pid="${PPID:-}" stop="${HARNESS_IDENTITY_PANE_PID:-}" next guard=0
    while [ "$guard" -lt 64 ]; do
        case "$pid" in ''|*[!0-9]*) return 0 ;; esac
        [ "$pid" -gt 1 ] || return 0
        printf '%s\n' "$pid"
        [ -n "$stop" ] && [ "$pid" = "$stop" ] && return 0
        next=$(ps -o ppid= -p "$pid" 2>/dev/null | awk 'NR==1 { print $1 }')
        [ "$next" = "$pid" ] && return 0
        pid="$next"
        guard=$((guard + 1))
    done
}

# The pid of the process this pane's shell launched — the CLI itself, whatever
# it is called. Resolved positionally (the ancestor directly below #{pane_pid})
# so no name list is involved.
#
# Absence is meaningful: if the pane's own process is nowhere in our ancestry we
# are not running in that pane, however confidently $TMUX_PANE says otherwise.
# That is the stale/inherited TMUX_PANE case, and the caller refuses on it.
# `exec claude` leaves the CLI *as* the pane process, so a chain that starts at
# pane_pid resolves to pane_pid rather than to nothing.
_hi_pane_root_pid() {
    local pane_pid="$1" pid last=""
    case "$pane_pid" in ''|*[!0-9]*) _hi_ancestors | tail -n1; return 0 ;; esac
    while read -r pid; do
        [ -n "$pid" ] || continue
        if [ "$pid" = "$pane_pid" ]; then
            printf '%s\n' "${last:-$pane_pid}"
            return 0
        fi
        last="$pid"
    done <<EOF
$(_hi_ancestors)
EOF
    return 1
}

_hi_is_ancestor() {
    local target="$1" pid
    [ -n "$target" ] || return 1
    while read -r pid; do
        [ "$pid" = "$target" ] && return 0
    done <<EOF
$(_hi_ancestors)
EOF
    return 1
}

# ── tmux reads ───────────────────────────────────────────────────────────────
_hi_tmux_get() {
    tmux display-message -p -t "$HARNESS_IDENTITY_PANE" "$1" 2>/dev/null || true
}

# ── codename generation ──────────────────────────────────────────────────────
# One pool for every chassis. The chassis lives in the prefix, so a shared pool
# only widens the space each family draws from.
_HI_POOL_ADJ="amber ashen cobalt cinder crimson dusk ember frost hazel indigo
iron jade midnight moss muted onyx pewter russet rust sable silent slate steady
storm swift umber verdant woven"
_HI_POOL_NOUN="cairn cedar crane falcon fox glyph harrow heron koan koto lantern
lattice otter petal quill raven reed rook sparrow tanuki thistle vireo willow wren"

_hi_rand_mod() {
    local n="$1" raw
    raw=$(od -An -N2 -tu2 /dev/urandom 2>/dev/null | tr -d ' \n')
    case "$raw" in ''|*[!0-9]*) raw=$$ ;; esac
    echo $((raw % n))
}

_hi_pick() {
    # shellcheck disable=SC2086
    set -- $1
    eval "echo \"\${$(( $(_hi_rand_mod $#) + 1 ))}\""
}

_hi_generate_codename() {
    printf '%s-%s\n' "$(_hi_pick "$_HI_POOL_ADJ")" "$(_hi_pick "$_HI_POOL_NOUN")"
}

# Two different questions, deliberately not merged.
#
# A live pane holding the id is a real conflict: two panes answering to one
# mailbox address. A sentinel file holding it is only a memory of a past
# session, and the same pane's own earlier sessions leave those behind — so
# treating a sentinel as a conflict made a pane refuse its own locked identity.
_hi_routing_id_on_other_pane() {
    local ident="$1"
    [ -n "$ident" ] || return 1
    tmux list-panes -a -F '#{pane_id}|#{?#{@formation_identity_locked},#{@formation_identity_locked},#{@formation_id}}' 2>/dev/null \
        | awk -F '|' -v target="$HARNESS_IDENTITY_PANE" -v ident="$ident" \
            '$1 != target && $2 == ident { found = 1 } END { exit !found }'
}

# For a candidate this process is about to invent or resume from a sentinel,
# a past session's sentinel does count: two sessions must not converge on one
# codename just because neither is live yet.
#
# The sentinel side of that question used to be answered per candidate with one
# `head` process per file. At 729 accumulated files that is >1s per attempt,
# and with a third of the name pool burned more than one attempt is the
# expected case — inside a hook timeout that the SessionStart fork storm
# already crowds, the fresh-claim path blew it and the hook died unlogged
# (#236). Read every sentinel's first line in a single pass per resolve;
# candidates then test membership in shell state without spawning anything.
#
# The pane's own sentinel is excluded at load time for the same reason the old
# loop skipped it: a session resuming its own recorded codename must not read
# that record as a conflict.
_HI_BURNED_NAMES=""
_HI_BURNED_NAMES_LOADED=0
_hi_burned_names_load() {
    [ "$_HI_BURNED_NAMES_LOADED" = "1" ] && return 0
    local own names
    own=$(basename "${HARNESS_IDENTITY_SENTINEL:-/nonexistent}")
    names=$(find "$HARNESS_IDENTITY_SENTINEL_DIR" -maxdepth 1 -type f \
                 ! -name decisions.jsonl ! -name "$own" \
                 -exec awk 'FNR==1' {} + 2>/dev/null)
    # Wrapped in newlines so membership is a whole-line match, not a substring.
    _HI_BURNED_NAMES="
$names
"
    _HI_BURNED_NAMES_LOADED=1
}

_hi_name_burned() { # full display name, e.g. claude-amber-koto
    _hi_burned_names_load
    case "$_HI_BURNED_NAMES" in
        *"
$1
"*) return 0 ;;
    esac
    return 1
}

_hi_routing_id_in_use() {
    local ident="$1"
    [ -n "$ident" ] || return 1
    # Burned-set first: it is pure shell state, so a candidate that collides
    # with a sentinel is rejected without a tmux round-trip. Only a candidate
    # that survives the cheap check pays for the live-pane question.
    _hi_name_burned "${HARNESS_IDENTITY_CHASSIS}-${ident}" && return 0
    _hi_routing_id_on_other_pane "$ident"
}

# ── decision log ─────────────────────────────────────────────────────────────
# Identity drift is only ever noticed by eye, hours later. A bounded decision
# log makes "which process decided to rename this pane, and why" answerable
# after the fact. Values here are pane ids, chassis names and reason slugs —
# no command lines, no environment.
_hi_log() {
    local log="$HARNESS_IDENTITY_SENTINEL_DIR/decisions.jsonl" lines
    mkdir -p "$HARNESS_IDENTITY_SENTINEL_DIR" 2>/dev/null || return 0
    printf '{"pane":"%s","chassis":"%s","mode":"%s","decision":"%s","reason":"%s","routing_id":"%s","pid":%s}\n' \
        "$HARNESS_IDENTITY_PANE" "$HARNESS_IDENTITY_CHASSIS" "$HARNESS_IDENTITY_MODE" \
        "$HARNESS_IDENTITY_DECISION" "$HARNESS_IDENTITY_REASON" \
        "$HARNESS_IDENTITY_ROUTING_ID" "$$" >> "$log" 2>/dev/null || return 0
    lines=$(wc -l < "$log" 2>/dev/null | tr -d ' ')
    case "$lines" in ''|*[!0-9]*) return 0 ;; esac
    if [ "$lines" -gt "$HARNESS_IDENTITY_DECISION_LOG_MAX" ]; then
        tail -n "$((HARNESS_IDENTITY_DECISION_LOG_MAX / 2))" "$log" > "$log.tmp" 2>/dev/null \
            && mv "$log.tmp" "$log" 2>/dev/null
    fi
    return 0
}

_hi_decide() {
    HARNESS_IDENTITY_DECISION="$1"
    HARNESS_IDENTITY_REASON="$2"
    # An explicit display override (HARNESS_KIMI_DISPLAY_NAME predates the
    # <chassis>-<id> convention and users still hold unprefixed ones) names the
    # window without changing what the pane is addressed as.
    if [ "$1" = "CLAIM" ] && [ -n "${HARNESS_IDENTITY_DISPLAY_OVERRIDE:-}" ]; then
        HARNESS_IDENTITY_NAME="$HARNESS_IDENTITY_DISPLAY_OVERRIDE"
    fi
    _hi_log
    return 0
}

# ── resolve ──────────────────────────────────────────────────────────────────
# Sets, for the caller:
#   HARNESS_IDENTITY_DECISION   CLAIM | PRESERVE | REFUSE
#   HARNESS_IDENTITY_REASON     machine-readable slug
#   HARNESS_IDENTITY_ROUTING_ID routing id on CLAIM
#   HARNESS_IDENTITY_NAME       <chassis>-<routing id> on CLAIM
#   HARNESS_IDENTITY_RESUMED    1 when the identity pre-existed this process
#
# PRESERVE and REFUSE both mutate nothing. They are distinct so the log can tell
# a benign one-shot apart from a real ownership conflict.
harness_identity_resolve() {
    HARNESS_IDENTITY_PANE="${TMUX_PANE:-}"
    HARNESS_IDENTITY_CHASSIS="claude"
    HARNESS_IDENTITY_MODE="session-start"
    HARNESS_IDENTITY_DECISION="REFUSE"
    HARNESS_IDENTITY_REASON="unresolved"
    HARNESS_IDENTITY_ROUTING_ID=""
    HARNESS_IDENTITY_NAME=""
    HARNESS_IDENTITY_RESUMED=0
    HARNESS_IDENTITY_SESSION_KEY=""
    HARNESS_IDENTITY_SENTINEL=""
    HARNESS_IDENTITY_WINDOW_PANES=""
    HARNESS_IDENTITY_OWNER_KEY=""
    HARNESS_IDENTITY_DISPLAY_OVERRIDE=""

    local requested_routing_id="" requested_display_name=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --pane)        HARNESS_IDENTITY_PANE="$2"; shift 2 ;;
            --chassis)     HARNESS_IDENTITY_CHASSIS="$2"; shift 2 ;;
            --mode)        HARNESS_IDENTITY_MODE="$2"; shift 2 ;;
            --routing-id)  requested_routing_id="$2"; shift 2 ;;
            --display-name) requested_display_name="$2"; shift 2 ;;
            --session-key) HARNESS_IDENTITY_SESSION_KEY="$2"; shift 2 ;;
            *)             shift ;;
        esac
    done

    HARNESS_IDENTITY_DISPLAY_OVERRIDE="$requested_display_name"

    if harness_identity_disabled; then
        _hi_decide REFUSE disabled; return 0
    fi
    [ -n "$HARNESS_IDENTITY_PANE" ] || { _hi_decide REFUSE no-tmux; return 0; }
    command -v tmux >/dev/null 2>&1 || { _hi_decide REFUSE no-tmux; return 0; }

    local resolved_pane
    resolved_pane=$(_hi_tmux_get '#{pane_id}')
    [ -n "$resolved_pane" ] || { _hi_decide REFUSE no-tmux; return 0; }
    # A stale TMUX_PANE can name a pane that exists but is not ours; targeting
    # every command with -t is necessary and not sufficient.
    HARNESS_IDENTITY_PANE="$resolved_pane"

    if [ -z "$HARNESS_IDENTITY_SESSION_KEY" ]; then
        HARNESS_IDENTITY_SESSION_KEY="${HARNESS_IDENTITY_PANE//[^a-zA-Z0-9]/_}"
    fi
    mkdir -p "$HARNESS_IDENTITY_SENTINEL_DIR" 2>/dev/null || true
    HARNESS_IDENTITY_SENTINEL="$HARNESS_IDENTITY_SENTINEL_DIR/$HARNESS_IDENTITY_SESSION_KEY"
    # 7 days, not 30: at this host's session rate 30 days accumulated 729
    # sentinels against a 672-name pool, so fresh claims fought a ~30% burned
    # rate for names nothing would ever resume (#236). A week comfortably
    # covers any session a user would still resume by hand.
    find "$HARNESS_IDENTITY_SENTINEL_DIR" -maxdepth 1 -type f -mtime +7 -delete 2>/dev/null || true
    _HI_BURNED_NAMES_LOADED=0

    local pane_pid owner_chassis owner_key
    pane_pid=$(_hi_tmux_get '#{pane_pid}')
    HARNESS_IDENTITY_PANE_PID="$pane_pid"

    # A resolving pane id is not proof that we are in that pane. An inherited
    # TMUX_PANE names a pane that exists and belongs to someone else; without
    # this the walk simply fails to find an owner and a fallback would go on to
    # rename a live stranger's window. Descent from the pane's own process is
    # the proof, and it is checked before anything else reads pane state.
    local pane_root
    if ! pane_root=$(_hi_pane_root_pid "$pane_pid"); then
        _hi_decide REFUSE not-in-pane; return 0
    fi

    # A one-shot invocation has nothing to name. It never claims, not even a
    # free pane: `kimi --help` and `claude -p` are transient and would leave a
    # codename behind that no live process answers to.
    if [ "$HARNESS_IDENTITY_MODE" = "one-shot" ]; then
        _hi_decide PRESERVE one-shot; return 0
    fi

    owner_chassis=$(_hi_tmux_get '#{@harness_chassis}')
    owner_key=$(_hi_tmux_get '#{@harness_owner_key}')
    HARNESS_IDENTITY_WINDOW_PANES=$(_hi_tmux_get '#{window_panes}')

    # Ownership, recorded rather than inferred.
    #
    # Note the ordering: a live owner that is *not* an ancestor is refused
    # whatever its chassis. Restricting that to foreign chassis let a second
    # codex, started while the first was suspended, take the pane, replace the
    # owner token with its own, exit — and hand the pane to any chassis while
    # the original was still resumable.
    if _hi_key_alive "$owner_key"; then
        if ! _hi_is_ancestor "${owner_key%%:*}"; then
            _hi_decide REFUSE owner-live-elsewhere; return 0
        fi
        if [ -n "$owner_chassis" ] && [ "$owner_chassis" != "$HARNESS_IDENTITY_CHASSIS" ]; then
            _hi_decide REFUSE foreign-owner-nested; return 0
        fi
        # Our own chassis, and it started us: a compact/resume or a nested
        # same-family launch. Re-assert the display if it drifted, but leave the
        # owner token pointing at the process that actually holds the pane.
        HARNESS_IDENTITY_OWNER_KEY="$owner_key"
    fi

    # Legacy panes claimed before this core existed carry no @harness_owner_key.
    # Fall back to the window label, which is what those adapters wrote, so an
    # in-flight upgrade does not hand every pane to the first CLI that restarts.
    if [ -z "$owner_chassis" ]; then
        # Ancestry by name, unconditionally — a nested launch is nested whatever
        # the window happens to be called, and a parent window renamed to
        # something custom is exactly the case a label check misses.
        if _hi_legacy_foreign_ancestor; then
            _hi_decide REFUSE legacy-foreign-ancestor; return 0
        fi
        local current_window_name
        current_window_name=$(_hi_tmux_get '#{window_name}')
        case "$current_window_name" in
            "${HARNESS_IDENTITY_CHASSIS}-"*) : ;;
            claude-*|codex-*|kimi-*|grok-*)
                # One window name, several panes, and the label is a sibling's.
                # Taking a routing id here would give this pane an identity that
                # cannot be displayed and that formation routing would then see
                # beside the lead's (#101). Inert is the contract.
                [ "$HARNESS_IDENTITY_WINDOW_PANES" = "1" ] \
                    || { _hi_decide REFUSE legacy-shared-window; return 0; }
                ;;
        esac
    fi

    # `harness-cross-cli` and any other supervisor that waits on the CLI sits
    # between the pane shell and the CLI, so the derived owner is the supervisor,
    # not the CLI. That is a usable lifetime token only while the supervisor
    # outlives its child; a launcher that knows better passes the real pid.
    if [ -z "$HARNESS_IDENTITY_OWNER_KEY" ]; then
        HARNESS_IDENTITY_OWNER_KEY=$(_hi_proc_key "${HARNESS_IDENTITY_OWNER_PID:-$pane_root}" 2>/dev/null || true)
    fi

    local pane_locked pane_alias
    pane_locked=$(_hi_tmux_get '#{@formation_identity_locked}')
    pane_alias=$(_hi_tmux_get '#{@formation_id}')

    # Formation owns the worker identity outright. A worker whose pane says it
    # belongs to someone else is a spawn that landed on the wrong pane.
    if [ -n "${FORMATION_SELF:-}" ]; then
        local pane_owner="${pane_locked:-$pane_alias}"
        if [ -n "$pane_owner" ] && [ "$pane_owner" != "$FORMATION_SELF" ]; then
            _hi_decide REFUSE formation-mismatch; return 0
        fi
        HARNESS_IDENTITY_ROUTING_ID="$FORMATION_SELF"
        [ -n "$pane_owner" ] && HARNESS_IDENTITY_RESUMED=1
        HARNESS_IDENTITY_NAME="${HARNESS_IDENTITY_CHASSIS}-${HARNESS_IDENTITY_ROUTING_ID}"
        _hi_decide CLAIM formation; return 0
    fi

    # An explicit request (a launcher's --routing-id, HARNESS_KIMI_FORMATION_ID)
    # only applies to a pane that has not already locked one.
    local candidate="${pane_locked:-$pane_alias}"
    if [ -n "$candidate" ]; then
        if _hi_routing_id_on_other_pane "$candidate"; then
            _hi_decide REFUSE routing-id-taken; return 0
        fi
        HARNESS_IDENTITY_ROUTING_ID="$candidate"
        HARNESS_IDENTITY_RESUMED=1
        HARNESS_IDENTITY_NAME="${HARNESS_IDENTITY_CHASSIS}-${HARNESS_IDENTITY_ROUTING_ID}"
        _hi_decide CLAIM locked; return 0
    fi

    if [ -n "$requested_routing_id" ] && ! _hi_routing_id_on_other_pane "$requested_routing_id"; then
        HARNESS_IDENTITY_ROUTING_ID="$requested_routing_id"
        HARNESS_IDENTITY_NAME="${HARNESS_IDENTITY_CHASSIS}-${HARNESS_IDENTITY_ROUTING_ID}"
        _hi_decide CLAIM requested; return 0
    fi

    if [ -f "$HARNESS_IDENTITY_SENTINEL" ]; then
        local existing
        existing=$(head -n1 "$HARNESS_IDENTITY_SENTINEL" 2>/dev/null)
        case "$existing" in
            "${HARNESS_IDENTITY_CHASSIS}-"?*)
                local existing_id="${existing#${HARNESS_IDENTITY_CHASSIS}-}"
                # Someone else acquired this codename since we last ran. Inventing
                # a fresh one here would silently change the identity a resumed
                # session already told the user and the mailbox about.
                if _hi_routing_id_in_use "$existing_id"; then
                    _hi_decide REFUSE sentinel-taken; return 0
                fi
                HARNESS_IDENTITY_ROUTING_ID="$existing_id"
                HARNESS_IDENTITY_RESUMED=1
                HARNESS_IDENTITY_NAME="$existing"
                _hi_decide CLAIM sentinel; return 0
                ;;
        esac
    fi

    local attempt cand
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 \
                   21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40; do
        cand=$(_hi_generate_codename)
        if ! _hi_routing_id_in_use "$cand"; then
            HARNESS_IDENTITY_ROUTING_ID="$cand"
            break
        fi
    done
    # The pool is finite. A numeric suffix beats looping forever or accepting a
    # colliding candidate.
    if [ -z "$HARNESS_IDENTITY_ROUTING_ID" ]; then
        HARNESS_IDENTITY_ROUTING_ID="$(_hi_generate_codename)-$(_hi_rand_mod 10000)"
    fi
    HARNESS_IDENTITY_NAME="${HARNESS_IDENTITY_CHASSIS}-${HARNESS_IDENTITY_ROUTING_ID}"
    _hi_decide CLAIM first
    return 0
}

# Pre-core panes have no @harness_chassis to consult, so ancestry is the only
# signal left for them. This is the old name-list check, kept strictly as a
# transitional fallback and never as the primary answer.
_hi_legacy_foreign_ancestor() {
    local pid comm
    while read -r pid; do
        comm=$(ps -o comm= -p "$pid" 2>/dev/null | awk 'NR==1 { print $1 }')
        comm="${comm##*/}"
        case "$comm" in
            claude|codex|kimi|kimi-code|grok)
                [ "$comm" = "$HARNESS_IDENTITY_CHASSIS" ] || return 0 ;;
        esac
    done <<EOF
$(_hi_ancestors)
EOF
    return 1
}

# ── apply ────────────────────────────────────────────────────────────────────
# One transaction, fixed order. Routing metadata lands before any display
# surface, so an interrupted apply leaves a pane that is addressable but
# mislabelled rather than labelled but unreachable.
#
# The window name is only written when this pane is the window's only pane: a
# shared window has one name and several legitimate owners.
harness_identity_apply() {
    [ "$HARNESS_IDENTITY_DECISION" = "CLAIM" ] || return 1
    [ -n "$HARNESS_IDENTITY_NAME" ] || return 1

    local rc=0
    tmux set-option -p -t "$HARNESS_IDENTITY_PANE" @harness_chassis "$HARNESS_IDENTITY_CHASSIS" >/dev/null 2>&1 || rc=1
    if [ -n "$HARNESS_IDENTITY_OWNER_KEY" ]; then
        tmux set-option -p -t "$HARNESS_IDENTITY_PANE" @harness_owner_key "$HARNESS_IDENTITY_OWNER_KEY" >/dev/null 2>&1 || rc=1
    fi
    tmux set-option -p -t "$HARNESS_IDENTITY_PANE" @formation_identity_locked "$HARNESS_IDENTITY_ROUTING_ID" >/dev/null 2>&1 || rc=1
    tmux set-option -p -t "$HARNESS_IDENTITY_PANE" @formation_id "$HARNESS_IDENTITY_ROUTING_ID" >/dev/null 2>&1 || rc=1

    # A formation worker's identity is spawn-scoped, and the sentinel is keyed by
    # pane. Writing one would let a recycled pane id hand a retired worker's
    # display name to whatever starts there next.
    if [ -n "$HARNESS_IDENTITY_SENTINEL" ] && [ "$HARNESS_IDENTITY_REASON" != "formation" ]; then
        printf '%s\n' "$HARNESS_IDENTITY_NAME" > "$HARNESS_IDENTITY_SENTINEL" 2>/dev/null || rc=1
    fi

    if [ "$HARNESS_IDENTITY_WINDOW_PANES" = "1" ]; then
        tmux rename-window -t "$HARNESS_IDENTITY_PANE" "$HARNESS_IDENTITY_NAME" >/dev/null 2>&1 || rc=1
    fi
    tmux select-pane -t "$HARNESS_IDENTITY_PANE" -T "$HARNESS_IDENTITY_NAME" >/dev/null 2>&1 || rc=1

    return "$rc"
}

# ── serialization ────────────────────────────────────────────────────────────
# A fixed write order does not make several `tmux set-option` calls one
# transaction. Two hooks that resolve a free pane before either applies both
# decide CLAIM(first), and their writes interleave: the pane can end up with one
# claimant's chassis and the other's owner token. Reporting a partial failure
# cannot undo that, so the window between deciding and writing has to be closed
# instead.
#
# mkdir, not flock: flock is absent on macOS and the `{fd}>` redirection needs
# bash 4.1, while these hooks run under whatever the chassis ships.
_HI_LOCK=""
_hi_lock() {
    local key="$1" dir waited=0
    dir="$HARNESS_IDENTITY_SENTINEL_DIR/.locks"
    mkdir -p "$dir" 2>/dev/null || return 1
    # A process killed between mkdir and release leaves the lock behind.
    find "$dir" -mindepth 1 -maxdepth 1 -type d -mmin +1 -exec rm -rf {} + 2>/dev/null || true
    while [ "$waited" -lt 50 ]; do
        if mkdir "$dir/$key.lock" 2>/dev/null; then
            _HI_LOCK="$dir/$key.lock"
            return 0
        fi
        sleep 0.1
        waited=$((waited + 1))
    done
    return 1
}
_hi_unlock() {
    [ -n "$_HI_LOCK" ] || return 0
    rmdir "$_HI_LOCK" 2>/dev/null || true
    _HI_LOCK=""
}

# The entry point adapters should call. Resolve and apply happen under one
# pane-scoped lock, and the resolve runs *inside* it, so a claimant that loses
# the race sees the winner's record rather than the free pane it saw first.
#
# Returns 0 when the pane was claimed, 1 otherwise; HARNESS_IDENTITY_DECISION
# and friends carry the detail either way.
harness_identity_claim() {
    local pane_key="${TMUX_PANE:-nopane}"
    local i
    for i in "$@"; do
        case "$i" in %*) pane_key="$i" ;; esac
    done
    pane_key="${pane_key//[^a-zA-Z0-9]/_}"

    if ! _hi_lock "$pane_key"; then
        # Losing the lock is not a licence to write anyway: a contended pane is
        # exactly the case this exists to protect. It is also not a licence to
        # vanish: this used to be the one exit that skipped the decision log,
        # and a 5s lock wait inside a same-sized hook timeout left no trace at
        # all (#236). Resolve has not run, so log with what the args carry.
        HARNESS_IDENTITY_PANE="${TMUX_PANE:-}"
        HARNESS_IDENTITY_CHASSIS="claude"
        HARNESS_IDENTITY_MODE="session-start"
        HARNESS_IDENTITY_ROUTING_ID=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --pane)    HARNESS_IDENTITY_PANE="$2"; shift 2 ;;
                --chassis) HARNESS_IDENTITY_CHASSIS="$2"; shift 2 ;;
                --mode)    HARNESS_IDENTITY_MODE="$2"; shift 2 ;;
                *)         shift ;;
            esac
        done
        _hi_decide REFUSE lock-unavailable
        return 1
    fi
    harness_identity_resolve "$@"
    if [ "$HARNESS_IDENTITY_DECISION" = "CLAIM" ]; then
        harness_identity_apply
        _hi_unlock
        return 0
    fi
    _hi_unlock
    return 1
}

# ── release ──────────────────────────────────────────────────────────────────
# A suspended TUI still owns its pane, which is correct — Ctrl-Z is not an exit
# and the process can be resumed. It also means a user who deliberately hands
# the pane to another CLI has no way to say so. This is that way.
harness_identity_release() {
    local pane="${1:-${TMUX_PANE:-}}"
    [ -n "$pane" ] || return 1
    command -v tmux >/dev/null 2>&1 || return 1
    tmux set-option -pu -t "$pane" @harness_owner_key >/dev/null 2>&1 || true
    tmux set-option -pu -t "$pane" @harness_chassis >/dev/null 2>&1 || true
    return 0
}
