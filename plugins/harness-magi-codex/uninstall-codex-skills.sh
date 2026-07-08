#!/usr/bin/env bash
# uninstall-codex-skills.sh — remove harness-magi-codex skills from the Codex skill dir.
# Removes only entries this plugin installed (a symlink into this repo, or a copied dir).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
TARGET="$CODEX_HOME/skills"

for skill in dual-magi-review ultramagi; do
    dst="$TARGET/$skill"
    if [ -L "$dst" ]; then
        # Only unlink if it points into THIS plugin — never touch a foreign symlink.
        if [ "$(readlink -f "$dst")" = "$(readlink -f "$HERE/skills/$skill")" ]; then
            rm -f "$dst"; echo "[harness-magi-codex] unlinked $dst"
        else
            echo "[harness-magi-codex] skipping $dst (symlink points elsewhere)" >&2
        fi
    elif [ -d "$dst" ] && [ -f "$dst/.harness-magi-codex" ]; then
        # Only remove a copied dir carrying OUR ownership marker. "contains a SKILL.md" would
        # also match a user's hand-written skill of the same name -- an irreversible rm -rf of
        # someone else's work.
        rm -rf "$dst"; echo "[harness-magi-codex] removed $dst"
    elif [ -d "$dst" ]; then
        echo "[harness-magi-codex] refusing to remove $dst (no ownership marker; not ours)" >&2
    else
        echo "[harness-magi-codex] not installed: $dst"
    fi
done
