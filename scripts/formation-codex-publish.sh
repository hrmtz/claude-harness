#!/usr/bin/env bash
set -euo pipefail

# Publish workers need write access to .git and network access for push.
# Keep this opt-in instead of changing Formation's sandboxed Codex default.
exec formation spawn --cli codex --bypass-sandbox "$@"
