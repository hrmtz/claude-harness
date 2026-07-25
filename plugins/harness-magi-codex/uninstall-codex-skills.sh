#!/usr/bin/env bash
# uninstall-codex-skills.sh — remove harness-magi-codex skills from the Codex skill dir.
# Removes only entries this plugin installed (a symlink into this repo, or a copied dir).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
TARGET="$CODEX_HOME/skills"

mkdir -p "$TARGET"
exec 9>"$TARGET/.harness-magi-codex.install.lock" || {
    echo "[harness-magi-codex] error: cannot open installer lock" >&2; exit 1; }
flock -x 9 || { echo "[harness-magi-codex] error: cannot acquire installer lock" >&2; exit 1; }

marker_matches() {
    local expected_value="$1" marker_path="$2"
    [ -f "$marker_path" ] && printf '%s\n' "$expected_value" | cmp -s - "$marker_path"
}

result=0
for skill in magi dual-magi-review ultramagi; do
    dst="$TARGET/$skill"
    if [ -L "$dst" ]; then
        # Only unlink if it points into THIS plugin — never touch a foreign symlink.
        if [ "$(readlink -f "$dst")" = "$(readlink -f "$HERE/skills/$skill")" ]; then
            unlink "$dst" || {
                echo "[harness-magi-codex] error: cannot unlink $dst" >&2; exit 1; }
            echo "[harness-magi-codex] unlinked $dst"
        else
            echo "[harness-magi-codex] skipping $dst (symlink points elsewhere)" >&2
            result=1
        fi
    elif [ -d "$dst" ] && [ -f "$dst/.harness-magi-codex" ]; then
        # Only remove a copied dir carrying OUR ownership marker. "contains a SKILL.md" would
        # also match a user's hand-written skill of the same name -- an irreversible rm -rf of
        # someone else's work.
        expected="installed by harness-magi-codex from $HERE/skills/$skill"
        marker_matches "$expected" "$dst/.harness-magi-codex" || {
            echo "[harness-magi-codex] refusing to remove $dst (invalid ownership marker)" >&2
            result=1
            continue
        }
        rm -rf "$dst" || {
            echo "[harness-magi-codex] error: cannot remove $dst" >&2; exit 1; }
        echo "[harness-magi-codex] removed $dst"
    elif [ -d "$dst" ]; then
        echo "[harness-magi-codex] refusing to remove $dst (no ownership marker; not ours)" >&2
        result=1
    else
        echo "[harness-magi-codex] not installed: $dst"
    fi
done
exit "$result"
