#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
INSTALL="$HERE/../install-claude-skills.sh"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

target="$(readlink -f "$HERE/../skills/babysit-pr")"

CLAUDE_HOME="$TMP/fresh" bash "$INSTALL" >/dev/null
[ -L "$TMP/fresh/skills/babysit-pr" ]
[ "$(readlink -f "$TMP/fresh/skills/babysit-pr")" = "$target" ]
CLAUDE_HOME="$TMP/fresh" bash "$INSTALL" | grep -q 'already current'

mkdir -p "$TMP/foreign-file/skills"
printf 'foreign\n' >"$TMP/foreign-file/skills/babysit-pr"
CLAUDE_HOME="$TMP/foreign-file" bash "$INSTALL" >/dev/null 2>&1 && {
  echo "installer overwrote a foreign file" >&2
  exit 1
}
grep -qx foreign "$TMP/foreign-file/skills/babysit-pr"

mkdir -p "$TMP/foreign-dir/skills/babysit-pr"
printf 'foreign\n' >"$TMP/foreign-dir/skills/babysit-pr/SKILL.md"
CLAUDE_HOME="$TMP/foreign-dir" bash "$INSTALL" >/dev/null 2>&1 && {
  echo "installer overwrote a foreign directory" >&2
  exit 1
}
grep -qx foreign "$TMP/foreign-dir/skills/babysit-pr/SKILL.md"

mkdir -p "$TMP/foreign-link/skills" "$TMP/foreign-target"
ln -s "$TMP/foreign-target" "$TMP/foreign-link/skills/babysit-pr"
CLAUDE_HOME="$TMP/foreign-link" bash "$INSTALL" >/dev/null 2>&1 && {
  echo "installer retargeted a foreign symlink" >&2
  exit 1
}
[ "$(readlink -f "$TMP/foreign-link/skills/babysit-pr")" = "$TMP/foreign-target" ]

mkdir -p "$TMP/symlink-root" "$TMP/symlink-target"
ln -s "$TMP/symlink-target" "$TMP/symlink-root/skills"
CLAUDE_HOME="$TMP/symlink-root" bash "$INSTALL" >/dev/null 2>&1 && {
  echo "installer accepted a symlinked skills root" >&2
  exit 1
}
[ ! -e "$TMP/symlink-target/babysit-pr" ]

echo "claude skill installer fixtures: PASS"
