#!/bin/bash
# install-kimi-agents.sh — copy the Kimi harness AGENTS.md into a target project.
#
# Usage: install-kimi-agents.sh [<project-root>]
# Default project-root: current working directory.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/AGENTS.md.template"
TARGET="${1:-$PWD}/AGENTS.md"

if [ ! -f "$TEMPLATE" ]; then
    echo "error: template not found: $TEMPLATE" >&2
    exit 1
fi

if [ -f "$TARGET" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "error: $TARGET already exists. Set FORCE=1 to overwrite." >&2
    exit 1
fi

cp "$TEMPLATE" "$TARGET"
echo "wrote $TARGET"
echo "Kimi will load this file on the next session start in this directory."
