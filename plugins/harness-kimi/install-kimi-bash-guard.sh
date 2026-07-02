#!/bin/bash
# install-kimi-bash-guard.sh — install the guarded bash wrapper for Kimi.
#
# This creates:
#   ~/.kimi-code/bin/kimi-guarded-bash                  (the PATH-shim wrapper)
#   ~/.kimi-code/bin/guarded-bash-dir/bash              (drop-in "bash" seen by Kimi)
#   ~/.kimi-code/bin/guarded-bash-dir/guard-check.sh    (shared guard core)
#   ~/.kimi-code/bin/guarded-bash-dir/guard-env.sh      (BASH_ENV layer, issue #52)
#
# The wrapper in ~/.local/bin/kimi will prepend guarded-bash-dir to PATH and
# export BASH_ENV=guard-env.sh when HARNESS_KIMI_BASH_GUARD=1 is set, so Kimi
# Bash tool calls go through the guard even via absolute-path /bin/bash.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIMI_BIN="$HOME/.kimi-code/bin"
GUARD_DIR="$KIMI_BIN/guarded-bash-dir"
WRAPPER_SRC="$HERE/guarded-bash.sh"
WRAPPER_DST="$KIMI_BIN/kimi-guarded-bash"

for src in "$WRAPPER_SRC" "$HERE/guard-check.sh" "$HERE/guard-env.sh"; do
    if [[ ! -f "$src" ]]; then
        echo "error: $(basename "$src") not found at $src" >&2
        exit 1
    fi
done

mkdir -p "$KIMI_BIN" "$GUARD_DIR"
cp "$WRAPPER_SRC" "$WRAPPER_DST"
chmod +x "$WRAPPER_DST"

cp "$HERE/guard-check.sh" "$GUARD_DIR/guard-check.sh"
chmod +x "$GUARD_DIR/guard-check.sh"
cp "$HERE/guard-env.sh" "$GUARD_DIR/guard-env.sh"

ln -sf "$WRAPPER_DST" "$GUARD_DIR/bash"

echo "[harness-kimi] installed guarded bash wrapper:"
echo "  $WRAPPER_DST"
echo "  $GUARD_DIR/bash -> $WRAPPER_DST"
echo "  $GUARD_DIR/guard-check.sh"
echo "  $GUARD_DIR/guard-env.sh"
echo ""
echo "To enable guards for a Kimi session:"
echo "  HARNESS_KIMI_BASH_GUARD=1 kimi"
echo ""
echo "Or create an alias:"
echo "  alias kimi-guard='HARNESS_KIMI_BASH_GUARD=1 kimi'"
