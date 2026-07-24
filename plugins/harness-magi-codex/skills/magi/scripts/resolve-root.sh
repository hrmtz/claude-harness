#!/usr/bin/env bash
# Resolve the companion plugin root for native/symlink and legacy --copy installs.
set -euo pipefail

# `cd -P` is essential here: the default installer exposes this script through
# CODEX_HOME/skills/magi -> <plugin>/skills/magi.  A logical `pwd` would derive
# CODEX_HOME as the plugin root and make every default symlink install fail.
SKILL_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
candidate="$(cd "$SKILL_DIR/../.." && pwd)"
if [ -x "$candidate/scripts/magi_preflight_codex.sh" ] &&
   [ -f "$candidate/schemas/preflight-run.schema.json" ]; then
    printf '%s\n' "$candidate"
    exit 0
fi

marker="$SKILL_DIR/.harness-magi-codex"
prefix="installed by harness-magi-codex from "
[ -f "$marker" ] || { echo "magi: companion plugin root unavailable" >&2; exit 2; }
line="$(cat "$marker")"
case "$line" in
    "$prefix"*) source_skill="${line#"$prefix"}" ;;
    *) echo "magi: invalid companion ownership marker" >&2; exit 2 ;;
esac
[ -d "$source_skill" ] || { echo "magi: recorded source skill is unavailable" >&2; exit 2; }
candidate="$(cd "$source_skill/../.." && pwd)"
[ -x "$candidate/scripts/magi_preflight_codex.sh" ] &&
[ -f "$candidate/schemas/preflight-run.schema.json" ] || {
    echo "magi: recorded companion runtime is incomplete" >&2
    exit 2
}
printf '%s\n' "$candidate"
