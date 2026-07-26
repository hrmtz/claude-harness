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

# Codename generation, collision checking and the compact/resume sentinel used
# to live here, duplicated in three other adapters with three sets of subtle
# differences. They are now the ownership core's job (#95).

# Auto-assign a formation identity and tmux display name when running inside tmux.
# The formation id is the stable mailbox address; the display name is what the
# user sees in the pane/window title, mirroring Claude-harness behavior.
#
# The ownership rules — nested launches, stale TMUX_PANE, sequential reuse,
# collision-free codenames — are not decided here. They live in the shared
# identity ownership core and are identical for claude, codex, kimi and grok
# (#95). This wrapper's only unique knowledge is which argv shapes are one-shot,
# because that is the part only a wrapper can know.
setup_formation_identity() {
    [ -n "${TMUX_PANE:-}" ] || return 0

    # shellcheck source=../harness-core/hooks/identity_owner.sh
    . "$HERE/identity_owner.sh" 2>/dev/null || return 0

    # An explicit display-name override is honoured for standalone launches by
    # deriving the routing id from it, so display and routing cannot diverge.
    local requested="${HARNESS_KIMI_FORMATION_ID:-}"
    if [ -z "$requested" ] && [ -z "${FORMATION_SELF:-}" ] && [ -n "${HARNESS_KIMI_DISPLAY_NAME:-}" ]; then
        requested="${HARNESS_KIMI_DISPLAY_NAME#kimi-}"
    fi

    # An override that already carries no chassis prefix is a name the user chose
    # for the window ("review-agent"); prefixing it would rename their pane out
    # from under them. Routing still uses the bare id either way.
    local display=""
    if [ -z "${FORMATION_SELF:-}" ] && [ -n "${HARNESS_KIMI_DISPLAY_NAME:-}" ]; then
        display="$HARNESS_KIMI_DISPLAY_NAME"
    fi

    harness_identity_claim \
        --pane "$TMUX_PANE" \
        --chassis kimi \
        --mode "$KIMI_IDENTITY_MODE" \
        ${requested:+--routing-id "$requested"} \
        ${display:+--display-name "$display"} || return 0
}

# Mode is the one identity input a wrapper is uniquely qualified to supply: it
# is the only layer that sees argv. A pipe instead of a TTY, or any of the
# one-shot subcommands below, means this process has nothing to name — it will
# be gone in a moment, and the codename it left behind would answer to nobody.
# `kimi --help` stealing a Claude pane's window name is the report that opened
# #95, and this is the check that closes it.
KIMI_IDENTITY_MODE=interactive
if [ ! -t 1 ] && [ "${HARNESS_KIMI_FORCE_INTERACTIVE_IDENTITY:-0}" != "1" ]; then
    KIMI_IDENTITY_MODE=one-shot
fi
for arg in "$@"; do
    case "$arg" in
        -h|--help|-V|--version|-p|--prompt|\
        export|provider|acp|web|server|login|doctor|vis|migrate|upgrade|update)
            KIMI_IDENTITY_MODE=one-shot
            ;;
    esac
done

# The kill switches are the core's business — it honours all six names for every
# chassis, so GROK_TMUX_NAME_DISABLE now stops kimi too and vice versa. Calling
# unconditionally keeps that in one place instead of re-listing a subset here.
setup_formation_identity

# NOTE: the BASH_ENV / PATH-shim Bash guard (issue #52) was removed — Kimi Code
# CLI >= 0.28 has a native PreToolUse hook API, and install-kimi-hooks.sh wires
# the same guards through it, closing the layer's known bypasses (bash --posix,
# bash -i, sh -c) with no interception tricks. HARNESS_KIMI_BASH_GUARD is now
# ignored. See docs/kimi_hooks.md.

exec "$REAL_KIMI" "$@"
