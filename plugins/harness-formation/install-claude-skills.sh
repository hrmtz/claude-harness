#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CLAUDE_ROOT="${CLAUDE_HOME:-${HOME}/.claude}"
SKILLS_ROOT="${CLAUDE_ROOT}/skills"
SOURCE="${HERE}/skills/babysit-pr"
DEST="${SKILLS_ROOT}/babysit-pr"

[ -f "$SOURCE/SKILL.md" ] || {
  echo "babysit-pr source is incomplete: $SOURCE" >&2
  exit 1
}

if [ -L "$SKILLS_ROOT" ]; then
  echo "refusing symlinked Claude skills root: $SKILLS_ROOT" >&2
  exit 1
fi
if [ -e "$SKILLS_ROOT" ] && [ ! -d "$SKILLS_ROOT" ]; then
  echo "Claude skills root is not a directory: $SKILLS_ROOT" >&2
  exit 1
fi
mkdir -p -- "$SKILLS_ROOT"

if [ -L "$DEST" ]; then
  current="$(readlink -f -- "$DEST" 2>/dev/null || true)"
  expected="$(readlink -f -- "$SOURCE")"
  if [ "$current" = "$expected" ]; then
    printf 'linked %s -> %s (already current)\n' "$DEST" "$SOURCE"
    exit 0
  fi
  echo "refusing foreign babysit-pr symlink: $DEST" >&2
  exit 1
fi
if [ -e "$DEST" ]; then
  echo "refusing foreign babysit-pr destination: $DEST" >&2
  exit 1
fi

# ln(2) refuses if a racing writer creates DEST first; never replace it.
ln -s -- "$SOURCE" "$DEST"
printf 'linked %s -> %s\n' "$DEST" "$SOURCE"
