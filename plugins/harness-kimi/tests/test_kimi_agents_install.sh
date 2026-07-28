#!/usr/bin/env bash
# Regression tests for install-kimi-agents.sh safety rails (claude-harness#231).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="$HERE/../install-kimi-agents.sh"
TMP="$(mktemp -d)" || { echo "cannot create temporary directory" >&2; exit 1; }
[ -n "$TMP" ] || { echo "mktemp returned an empty path" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

MARKER="Agent harness — behavioral rails"
# Route backups into the sandbox, not the real ~/sanada_backup_persistent.
HOME="$TMP/home"
mkdir -p "$HOME"
export HOME

# --- fresh install writes the template -------------------------------------
mkdir -p "$TMP/fresh"
"$INSTALL" "$TMP/fresh" >/dev/null 2>&1 || { echo "fresh install failed" >&2; exit 1; }
grep -qF "$MARKER" "$TMP/fresh/AGENTS.md" || { echo "fresh install lacks marker" >&2; exit 1; }

# --- marker-less existing file is refused even with FORCE=1 -----------------
mkdir -p "$TMP/local"
printf '# project-local notes\n' > "$TMP/local/AGENTS.md"
FORCE=1 "$INSTALL" "$TMP/local" >/dev/null 2>&1 && {
  echo "marker-less AGENTS.md was overwritten" >&2; exit 1; }
grep -qx '# project-local notes' "$TMP/local/AGENTS.md" || {
  echo "marker-less AGENTS.md content changed" >&2; exit 1; }

# --- marker file without FORCE=1 is refused ---------------------------------
mkdir -p "$TMP/guarded"
printf '# %s\nold-body\n' "$MARKER" > "$TMP/guarded/AGENTS.md"
"$INSTALL" "$TMP/guarded" >/dev/null 2>&1 && {
  echo "overwrite without FORCE=1 was accepted" >&2; exit 1; }
grep -qx 'old-body' "$TMP/guarded/AGENTS.md" || exit 1

# --- batch loop in the same second keeps one backup per project -------------
# claude-harness#231 review HIGH: second-resolution BACKUP_DIR + fixed
# 'AGENTS.md' file name collapsed N project backups into 1. The backup file
# name must be project-unique.
for proj in alpha beta gamma; do
  mkdir -p "$TMP/batch/$proj"
  printf '# %s\nbody-of-%s\n' "$MARKER" "$proj" > "$TMP/batch/$proj/AGENTS.md"
done
for proj in alpha beta gamma; do
  FORCE=1 "$INSTALL" "$TMP/batch/$proj" >/dev/null 2>&1 || {
    echo "batch install of $proj failed" >&2; exit 1; }
done
backup_count="$(find "$HOME/sanada_backup_persistent" -type f -name '*_AGENTS.md' | wc -l)"
[ "$backup_count" -eq 3 ] || {
  echo "same-second batch kept $backup_count backups, expected 3" >&2; exit 1; }
for proj in alpha beta gamma; do
  backup_file="$(find "$HOME/sanada_backup_persistent" -type f -name "${proj}_AGENTS.md")"
  [ -n "$backup_file" ] || { echo "backup for $proj missing" >&2; exit 1; }
  grep -qx "body-of-$proj" "$backup_file" || {
    echo "backup for $proj has wrong content" >&2; exit 1; }
done

echo "test_kimi_agents_install: PASS"
