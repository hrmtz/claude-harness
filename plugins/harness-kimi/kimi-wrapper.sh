#!/bin/bash
# kimi-wrapper.sh — launch Kimi Code CLI with harness-kimi + formation identity setup.
#
# This wrapper is meant to be placed earlier in PATH than the real `kimi` binary,
# or invoked via a shell alias. Before exec-ing the real Kimi, it:
#
#   1. Searches the current directory and its parents for AGENTS.md.
#   2. If none is found and the directory looks like a project workspace,
#      copies harness-kimi's AGENTS.md.template into the current directory.
#   3. If running inside a tmux pane without an @formation_id, auto-assigns one
#      so Kimi can participate in the harness-formation mailbox using a stable
#      identity (e.g. slate-falcon). The id is random and checked against other
#      panes so multiple Kimi launches in the same directory do not collide.
#   4. Derives the tmux pane/window display name from that same mailbox id
#      (e.g. @formation_id=slate-falcon -> "kimi-slate-falcon") so routing,
#      display, and self-reference cannot drift.
#   5. Execs the real `kimi` binary with all original arguments.
#
# Environment variables:
#   HARNESS_KIMI_TEMPLATE — override path to the AGENTS.md template.
#   HARNESS_KIMI_ANYWHERE — set to 1 to allow AGENTS.md creation outside ~/projects/.
#   HARNESS_KIMI_FORMATION_ID — override the auto-derived formation identity.
#   HARNESS_KIMI_DISPLAY_NAME — override the auto-derived pane/window name.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_KIMI="${HARNESS_KIMI_REAL:-$HOME/.kimi-code/bin/kimi}"

# Resolve the AGENTS.md.template: env override, wrapper directory, or canonical
# harness-kimi plugin directory.
if [ -n "${HARNESS_KIMI_TEMPLATE:-}" ]; then
    TEMPLATE="$HARNESS_KIMI_TEMPLATE"
elif [ -f "$HERE/AGENTS.md.template" ]; then
    TEMPLATE="$HERE/AGENTS.md.template"
elif [ -f "$HOME/projects/claude-harness/plugins/harness-kimi/AGENTS.md.template" ]; then
    TEMPLATE="$HOME/projects/claude-harness/plugins/harness-kimi/AGENTS.md.template"
else
    TEMPLATE=""
fi

# Fallback: if the explicit/real binary is missing, trust PATH.
if [ ! -x "$REAL_KIMI" ]; then
    REAL_KIMI="$(command -v kimi 2>/dev/null || true)"
    if [ -z "$REAL_KIMI" ]; then
        echo "error: kimi binary not found at ~/.kimi-code/bin/kimi and not in PATH" >&2
        exit 1
    fi
fi

# Check whether the current directory already has AGENTS.md.
# We intentionally do NOT walk parents: a global AGENTS.md should not prevent
# per-project harness rules from being created.
has_local_agents() {
    [ -f "$PWD/AGENTS.md" ]
}

# Decide whether the current directory is a workspace where AGENTS.md belongs.
# Default: only under ~/projects/. Override with HARNESS_KIMI_ANYWHERE=1.
is_workspace() {
    if [ "${HARNESS_KIMI_ANYWHERE:-0}" = "1" ]; then
        return 0
    fi
    case "$PWD" in
        "$HOME/projects"/*) return 0 ;;
        *) return 1 ;;
    esac
}

if [ -f "$TEMPLATE" ] && ! has_local_agents && is_workspace; then
    TARGET="$PWD/AGENTS.md"
    if cp "$TEMPLATE" "$TARGET" 2>/dev/null; then
        echo "[harness-kimi] installed $TARGET" >&2
    fi
fi

# Random formation codename in the style used by harness-formation.
generate_formation_id() {
    local adjectives=(
        amber cinder crimson dusk ember iron midnight moss muted onyx
        rust silent slate steady storm swift woven
    )
    local nouns=(
        crane falcon fox heron lantern otter raven rook tanuki wren
    )
    local idx1 idx2
    idx1=$(($(od -An -N2 -i /dev/urandom | tr -d ' ') % ${#adjectives[@]}))
    idx2=$(($(od -An -N2 -i /dev/urandom | tr -d ' ') % ${#nouns[@]}))
    echo "${adjectives[$idx1]}-${nouns[$idx2]}"
}

formation_id_in_use() {
    local id="$1"
    tmux list-panes -a -F '#{@formation_id}' 2>/dev/null | grep -qx "$id"
}

generate_unique_formation_id() {
    local id
    local attempt
    for attempt in $(seq 1 100); do
        id="$(generate_formation_id)"
        if ! formation_id_in_use "$id"; then
            echo "$id"
            return 0
        fi
    done
    # Fallback: timestamped hash if the random pool is exhausted.
    echo "kimi-$(date +%s%N | sha256sum | cut -c1-8)"
}

# Sentinel file for persistent standalone display naming across compact/resume.
# Formation workers do not consult this pane-keyed state: their spawn-scoped
# FORMATION_SELF is authoritative, so a recycled pane id cannot inherit an old
# worker's display identity.
self_name_sentinel() {
    local pane="${1:-${TMUX_PANE:-}}"
    local key="${pane//[^a-zA-Z0-9]/_}"
    echo "$HOME/.local/state/tmux_self_name/${key}"
}

# Store the display name so compact/resume reuses it.
save_display_name() {
    local name="$1"
    local sentinel
    sentinel="$(self_name_sentinel)"
    mkdir -p "$(dirname "$sentinel")"
    echo "$name" > "$sentinel"
}

# Auto-assign a formation identity and tmux display name when running inside tmux.
# The formation id is the stable mailbox address; the display name is what the
# user sees in the pane/window title, mirroring Claude-harness behavior.
setup_formation_identity() {
    if [ -z "${TMUX_PANE:-}" ]; then
        return 0
    fi

    # Formation id (stable mailbox identity).
    local formation_id current_window
    formation_id="$(tmux display-message -p -t "$TMUX_PANE" '#{@formation_id}' 2>/dev/null || true)"
    current_window="$(tmux display-message -p -t "$TMUX_PANE" '#{window_name}' 2>/dev/null || true)"
    # A stale/inherited TMUX_PANE must not let this process rename a sibling
    # worker. Formation sets both values before launching the CLI.
    if [ -n "${FORMATION_SELF:-}" ] \
        && [ "$formation_id" != "$FORMATION_SELF" ]; then
        return 0
    fi
    has_foreign_chassis_ancestor() {
        local pid="${PPID:-}" comm next
        while [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 1 ]; do
            comm="$(ps -o comm= -p "$pid" 2>/dev/null | awk 'NR==1 { print $1 }')"
            case "$comm" in
                claude|codex|grok) return 0 ;;
            esac
            next="$(ps -o ppid= -p "$pid" 2>/dev/null | awk 'NR==1 { print $1 }')"
            [ "$next" = "$pid" ] && break
            pid="$next"
        done
        return 1
    }

    # A standalone Kimi launched as a child inside another chassis inherits its
    # parent's TMUX_PANE. Permit sequential reuse after that process exits.
    if [ -z "${FORMATION_SELF:-}" ]; then
        case "$current_window" in
            claude-*|codex-*|grok-*)
                has_foreign_chassis_ancestor && return 0
                ;;
        esac
    fi
    if [ -z "$formation_id" ]; then
        formation_id="${HARNESS_KIMI_FORMATION_ID:-}"
        if [ -z "$formation_id" ] && [ -n "${HARNESS_KIMI_DISPLAY_NAME:-}" ]; then
            formation_id="${HARNESS_KIMI_DISPLAY_NAME#kimi-}"
            if formation_id_in_use "$formation_id"; then
                formation_id="$(generate_unique_formation_id)"
            fi
        fi
        if [ -z "$formation_id" ]; then
            formation_id="$(generate_unique_formation_id)"
        fi
        tmux set-option -p -t "$TMUX_PANE" @formation_id "$formation_id" >/dev/null 2>&1 || true
    fi

    # @formation_id is the single source of truth for routing, display, and
    # self-reference in both Formation-managed and standalone Kimi sessions.
    local display_name
    display_name="kimi-${formation_id}"
    if [ -z "${FORMATION_SELF:-}" ] && [ -n "${HARNESS_KIMI_DISPLAY_NAME:-}" ]; then
        display_name="$HARNESS_KIMI_DISPLAY_NAME"
    fi
    if [ -z "${FORMATION_SELF:-}" ]; then
        save_display_name "$display_name"
    fi

    # A split worker shares its window with the lead; only a dedicated
    # single-pane worker window may be renamed without changing the lead.
    local window_panes
    window_panes="$(tmux display-message -p -t "$TMUX_PANE" '#{window_panes}' 2>/dev/null || true)"
    if [ "$window_panes" = "1" ]; then
        tmux rename-window -t "$TMUX_PANE" "$display_name" >/dev/null 2>&1 || true
    fi
    tmux select-pane -t "$TMUX_PANE" -T "$display_name" >/dev/null 2>&1 || true
}

KIMI_IDENTITY_INTERACTIVE=1
if [ ! -t 1 ] && [ "${HARNESS_KIMI_FORCE_INTERACTIVE_IDENTITY:-0}" != "1" ]; then
    KIMI_IDENTITY_INTERACTIVE=0
fi
for arg in "$@"; do
    case "$arg" in
        -h|--help|-V|--version|-p|--prompt|\
        export|provider|acp|web|server|login|doctor|vis|migrate|upgrade|update)
            KIMI_IDENTITY_INTERACTIVE=0
            ;;
    esac
done

if [ "$KIMI_IDENTITY_INTERACTIVE" = "1" ] \
   && [ "${HARNESS_TMUX_SELF_NAME_DISABLE:-0}" != "1" ] \
   && [ "${KIMI_TMUX_NAME_DISABLE:-0}" != "1" ] \
   && [ "${HIPPOCAMPUS_TMUX_NAME_DISABLE:-0}" != "1" ]; then
    setup_formation_identity
fi

# NOTE: the BASH_ENV / PATH-shim Bash guard (issue #52) was removed — Kimi Code
# CLI >= 0.28 has a native PreToolUse hook API, and install-kimi-hooks.sh wires
# the same guards through it, closing the layer's known bypasses (bash --posix,
# bash -i, sh -c) with no interception tricks. HARNESS_KIMI_BASH_GUARD is now
# ignored. See docs/kimi_hooks.md.

exec "$REAL_KIMI" "$@"
