#!/usr/bin/env bash
set -u
PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$PLUGIN_ROOT/scripts/magi_autorun.py" --hook
