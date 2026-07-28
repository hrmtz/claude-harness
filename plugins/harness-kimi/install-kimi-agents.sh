#!/bin/bash
# install-kimi-agents.sh — copy the Kimi harness AGENTS.md into a target project.
#
# Usage: install-kimi-agents.sh [<project-root>]
# Default project-root: current working directory.
#
# Safety rails (claude-harness#231):
# - An existing AGENTS.md is overwritten only when it carries the harness
#   marker (`Agent harness — behavioral rails`). Project-local AGENTS.md
#   without the marker is never destroyed, even with FORCE=1.
# - Overwrites are backed up to ~/sanada_backup_persistent/ first.
# - Running from a git worktree warns: the template there may be stale.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/AGENTS.md.template"
TARGET="${1:-$PWD}/AGENTS.md"
MARKER="Agent harness — behavioral rails"

if [ ! -f "$TEMPLATE" ]; then
    echo "error: template not found: $TEMPLATE" >&2
    exit 1
fi

# Stale-template hazard: worktree checkouts drift behind canonical HEAD.
if git -C "$HERE" rev-parse --git-dir 2>/dev/null | grep -q '/worktrees/'; then
    echo "warning: running from a git worktree; the template may be stale." >&2
    echo "         prefer the canonical checkout's install-kimi-agents.sh." >&2
fi

if [ -f "$TARGET" ]; then
    if ! grep -qF "$MARKER" "$TARGET"; then
        echo "error: $TARGET exists without the harness marker — refusing to overwrite" >&2
        echo "       a project-local AGENTS.md. Merge by hand if you want the rails." >&2
        exit 1
    fi
    if [ "${FORCE:-0}" != "1" ]; then
        echo "error: $TARGET already exists. Set FORCE=1 to overwrite." >&2
        exit 1
    fi
    BACKUP_DIR="$HOME/sanada_backup_persistent/install-kimi-agents_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    cp "$TARGET" "$BACKUP_DIR/AGENTS.md"
    echo "backed up $TARGET -> $BACKUP_DIR/AGENTS.md"
fi

cp "$TEMPLATE" "$TARGET"
echo "wrote $TARGET"
echo "Kimi will load this file on the next session start in this directory."
