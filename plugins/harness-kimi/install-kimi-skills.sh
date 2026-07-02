#!/bin/bash
# install-kimi-skills.sh — install harness-kimi magi/bug-hunt skills into Kimi skill dir.
#
# Kimi Code CLI scans:
#   - $KIMI_CODE_HOME/skills/  (default ~/.kimi-code/skills/)
#   - ~/.agents/skills/
# for SKILL.md files.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIMI_HOME="${KIMI_CODE_HOME:-$HOME/.kimi-code}"
TARGET="$KIMI_HOME/skills"

mkdir -p "$TARGET"

for skill in magi bug-hunt; do
    src="$HERE/skills/$skill"
    if [ ! -d "$src" ]; then
        echo "error: source skill dir not found: $src" >&2
        exit 1
    fi
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete "$src/" "$TARGET/$skill/"
    else
        rm -rf "$TARGET/$skill"
        cp -R "$src" "$TARGET/$skill"
    fi
    echo "[harness-kimi] installed $TARGET/$skill"
done

echo "[harness-kimi] skills ready. Restart Kimi sessions to discover them."
