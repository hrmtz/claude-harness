#!/usr/bin/env bash
# Backward-compatible Claude adapter. New callers should use:
#   magi_xfamily.sh --reviewer claude|grok ...
set -euo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SELF_DIR/magi_xfamily.sh" --reviewer claude "$@"
