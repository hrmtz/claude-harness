#!/usr/bin/env bash
# install-codex-skills.sh — install harness-magi-codex skills into the Codex skill dir.
#
# Codex scans $CODEX_HOME/skills/ (default ~/.codex/skills/) for <name>/SKILL.md.
#
# Symlink by default, not rsync: ~/.codex/skills/formation is already a live symlink into a
# repo, so Codex demonstrably resolves symlinked skills, and a symlink cannot drift from the
# repo the way an installed copy does. (The harness-kimi rsync'd persona templates have
# already diverged from their originals -- measured.) Use --copy if you need a detached copy.
#
# Idempotent. Re-run after editing SKILL.md only if you used --copy.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
TARGET="$CODEX_HOME/skills"
MODE="symlink"

while [ $# -gt 0 ]; do
    case "$1" in
        --copy) MODE="copy"; shift ;;
        -h|--help) echo "usage: $0 [--copy]"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 64 ;;
    esac
done

command -v codex >/dev/null 2>&1 || echo "[harness-magi-codex] warning: codex CLI not found on PATH" >&2
command -v claude >/dev/null 2>&1 || {
    echo "[harness-magi-codex] warning: claude CLI not found. The cross-family round will" >&2
    echo "                     fail closed (exit 2) and NO plateau can be granted." >&2
}
command -v flock >/dev/null 2>&1 || { echo "[harness-magi-codex] error: flock(1) required" >&2; exit 1; }

mkdir -p "$TARGET"

for skill in dual-magi-review ultramagi; do
    src="$HERE/skills/$skill"
    dst="$TARGET/$skill"
    [ -d "$src" ] || { echo "error: source skill not found: $src" >&2; exit 1; }

    # Only ever replace what we own: a symlink we made, or a dir we previously copied.
    # (`rm -rf` on a symlink-to-dir with no trailing slash removes the link, not the target.)
    if [ -L "$dst" ] || [ -d "$dst" ]; then
        rm -rf "$dst"
    elif [ -e "$dst" ]; then
        echo "[harness-magi-codex] error: $dst exists and is not a skill dir or symlink" >&2
        exit 1
    fi

    # Every install action is checked. Reporting "linked" after a failed `ln` is how a plugin
    # ends up nominally installed and actually absent.
    if [ "$MODE" = "symlink" ]; then
        ln -s "$src" "$dst" || { echo "[harness-magi-codex] error: ln failed for $dst" >&2; exit 1; }
        echo "[harness-magi-codex] linked $dst -> $src"
    else
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --delete "$src/" "$dst/" || { echo "[harness-magi-codex] error: rsync failed" >&2; exit 1; }
        else
            cp -R "$src" "$dst" || { echo "[harness-magi-codex] error: cp failed" >&2; exit 1; }
        fi
        # Ownership marker: uninstall removes a copied dir ONLY if it finds this. Without it,
        # a user's own hand-written skill of the same name is indistinguishable from ours and
        # would be rm -rf'd.
        printf 'installed by harness-magi-codex from %s\n' "$src" > "$dst/.harness-magi-codex"
        echo "[harness-magi-codex] copied $dst"
    fi
done

chmod +x "$HERE"/scripts/*.sh "$HERE"/scripts/*.py "$HERE"/tests/*.sh "$HERE"/tests/*.py 2>/dev/null || true
echo "[harness-magi-codex] done. Restart Codex sessions to discover the skills."
echo "[harness-magi-codex] note: the plateau gate detects accidental skips (T1), NOT an"
echo "                     adversarial same-user process (T2). It is not forgery-resistant."
