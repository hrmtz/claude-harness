#!/bin/bash
# install-kimi-wrapper.sh — install the auto-AGENTS wrapper as `kimi` in ~/.local/bin.
#
# This puts the wrapper earlier in PATH than the real binary for users who
# start Kimi from a shell. Non-shell launches (desktop shortcuts, etc.) are
# not intercepted.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER_SRC="$HERE/kimi-wrapper.sh"
TEMPLATE_SRC="$HERE/AGENTS.md.template"
WRAPPER_DST="${HOME}/.local/bin/kimi"
TEMPLATE_DST="${HOME}/.local/bin/AGENTS.md.template"
REAL_KIMI="$HOME/.kimi-code/bin/kimi"
CROSS_CLI_SRC="$HERE/../harness-core/bin/harness-cross-cli"
CROSS_CLI_DST="${HOME}/.local/bin/harness-cross-cli"

if [ ! -f "$WRAPPER_SRC" ]; then
    echo "error: wrapper not found: $WRAPPER_SRC" >&2
    exit 1
fi

if [ ! -f "$TEMPLATE_SRC" ]; then
    echo "error: template not found: $TEMPLATE_SRC" >&2
    exit 1
fi

if [ ! -x "$REAL_KIMI" ]; then
    echo "error: real kimi binary not found: $REAL_KIMI" >&2
    exit 1
fi

mkdir -p "$(dirname "$WRAPPER_DST")"

# Backup existing wrapper if it exists and is not the same file.
if [ -f "$WRAPPER_DST" ] && [ ! "$WRAPPER_SRC" -ef "$WRAPPER_DST" ]; then
    BACKUP="$WRAPPER_DST.backup.$(date +%Y%m%d%H%M%S)"
    cp "$WRAPPER_DST" "$BACKUP"
    echo "backed up existing $WRAPPER_DST to $BACKUP"
fi

cp "$WRAPPER_SRC" "$WRAPPER_DST"
chmod +x "$WRAPPER_DST"
cp "$TEMPLATE_SRC" "$TEMPLATE_DST"
if [ -x "$CROSS_CLI_SRC" ]; then
    if [ -e "$CROSS_CLI_DST" ] || [ -L "$CROSS_CLI_DST" ]; then
        if [ "$(readlink -f "$CROSS_CLI_DST" 2>/dev/null || true)" != \
             "$(readlink -f "$CROSS_CLI_SRC")" ]; then
            CROSS_CLI_BACKUP="$CROSS_CLI_DST.backup.$(date +%Y%m%d%H%M%S)"
            mv "$CROSS_CLI_DST" "$CROSS_CLI_BACKUP"
            echo "backed up existing $CROSS_CLI_DST to $CROSS_CLI_BACKUP"
        fi
    fi
    if [ ! -e "$CROSS_CLI_DST" ] && [ ! -L "$CROSS_CLI_DST" ]; then
        ln -s "$CROSS_CLI_SRC" "$CROSS_CLI_DST"
    fi
fi

echo "installed wrapper: $WRAPPER_DST"
echo "installed template: $TEMPLATE_DST"
echo ""
echo "Make sure ~/.local/bin is before ~/.kimi-code/bin in your PATH."
echo "Typical shell rc addition:"
echo '  export PATH="$HOME/.local/bin:$PATH"'
echo ""
echo "When you run 'kimi' from a project under ~/projects/ without AGENTS.md,"
echo "the wrapper will copy the harness-kimi template first."
