#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="$HERE/../install-kimi-skills.sh"
TMP="$(mktemp -d)" || { echo "cannot create temporary directory" >&2; exit 1; }
[ -n "$TMP" ] || { echo "mktemp returned an empty path" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

KIMI_CODE_HOME="$TMP/kimi" "$INSTALL" >/dev/null || exit 1
env -u HOME -u KIMI_CODE_HOME "$INSTALL" >/dev/null 2>"$TMP/no-home.err" && {
  echo "installer accepted missing HOME and KIMI_CODE_HOME" >&2; exit 1; }
grep -Fq 'set KIMI_CODE_HOME or HOME' "$TMP/no-home.err" || {
  echo "installer did not report controlled missing-home error" >&2; exit 1; }
for skill in magi bug-hunt dual-magi-review ultramagi; do
  [ -f "$TMP/kimi/skills/$skill/SKILL.md" ] || { echo "missing $skill" >&2; exit 1; }
  [ -f "$TMP/kimi/skills/$skill/.harness-kimi-skill" ] || { echo "unowned $skill" >&2; exit 1; }
done
[ -L "$TMP/kimi/harness-magi-runtime" ] || { echo "runtime link missing" >&2; exit 1; }
KIMI_CODE_HOME="$TMP/kimi" "$INSTALL" >/dev/null || { echo "idempotent reinstall failed" >&2; exit 1; }

ln -s "$INSTALL" "$TMP/install-kimi-skills-via-link"
KIMI_CODE_HOME="$TMP/kimi-via-link" "$TMP/install-kimi-skills-via-link" >/dev/null || {
  echo "install through a symlinked script path failed" >&2; exit 1; }
[ "$(readlink -f "$TMP/kimi-via-link/harness-magi-runtime")" = \
  "$(readlink -f "$HERE/../../harness-magi-codex")" ] || {
  echo "symlinked installer created a noncanonical runtime link" >&2; exit 1; }

for skill in magi bug-hunt dual-magi-review ultramagi; do
  foreign="$TMP/foreign-$skill"
  mkdir -p "$foreign/skills/$skill"
  printf 'foreign\n' > "$foreign/skills/$skill/SKILL.md"
  KIMI_CODE_HOME="$foreign" "$INSTALL" >/dev/null 2>&1 && {
    echo "foreign Kimi skill $skill was overwritten" >&2; exit 1; }
  grep -qx foreign "$foreign/skills/$skill/SKILL.md" || exit 1
  for candidate in magi bug-hunt dual-magi-review ultramagi; do
    if [ "$candidate" != "$skill" ] && [ -e "$foreign/skills/$candidate" ]; then
      echo "foreign $skill refusal partially installed $candidate" >&2
      exit 1
    fi
  done

  symlink_home="$TMP/foreign-link-$skill"
  foreign_target="$TMP/foreign-target-$skill"
  mkdir -p "$symlink_home/skills" "$foreign_target"
  ln -s "$foreign_target" "$symlink_home/skills/$skill"
  KIMI_CODE_HOME="$symlink_home" "$INSTALL" >/dev/null 2>&1 && {
    echo "foreign Kimi symlink $skill was overwritten" >&2; exit 1; }
  [ -L "$symlink_home/skills/$skill" ] || exit 1
  [ "$(readlink -f "$symlink_home/skills/$skill")" = "$foreign_target" ] || exit 1
done

mkdir -p "$TMP/foreign-runtime/harness-magi-runtime"
KIMI_CODE_HOME="$TMP/foreign-runtime" "$INSTALL" >/dev/null 2>&1 && {
  echo "foreign runtime was overwritten" >&2; exit 1; }
[ -d "$TMP/foreign-runtime/harness-magi-runtime" ] || exit 1
[ ! -e "$TMP/foreign-runtime/skills/magi" ] || {
  echo "foreign runtime rejection partially installed skills" >&2; exit 1; }

# Replace an owned directory from inside the first successful marker comparison. This makes the
# full-set preflight observe the old owner while phase 2 sees a late foreign tree. Revalidation
# must refuse it before the preserve/publish path can recursively delete it as a predecessor.
race_home="$TMP/kimi-race-after-preflight"
KIMI_CODE_HOME="$race_home" "$INSTALL" >/dev/null || exit 1
race_bin="$TMP/kimi-race-bin"
mkdir -p "$race_bin"
real_cmp="$(command -v cmp)"
cat > "$race_bin/cmp" <<'STUB'
#!/usr/bin/env bash
"$REAL_CMP" "$@"
rc=$?
if [ "$rc" -eq 0 ] && [ ! -e "$RACE_SENTINEL" ]; then
  rm -rf "$RACE_HOME/skills/magi"
  mkdir -p "$RACE_HOME/skills/magi"
  printf 'foreign-after-preflight\n' > "$RACE_HOME/skills/magi/SKILL.md"
  : > "$RACE_SENTINEL"
fi
exit "$rc"
STUB
chmod +x "$race_bin/cmp"
PATH="$race_bin:$PATH" REAL_CMP="$real_cmp" RACE_HOME="$race_home" \
  RACE_SENTINEL="$TMP/kimi-race-swapped" KIMI_CODE_HOME="$race_home" \
  "$INSTALL" >"$TMP/kimi-race.log" 2>&1 && {
  echo "Kimi installer accepted a foreign replacement created after preflight" >&2; exit 1; }
grep -qx 'foreign-after-preflight' "$race_home/skills/magi/SKILL.md" || {
  echo "Kimi installer deleted the foreign replacement created after preflight" >&2; exit 1; }
grep -q 'refusing foreign skill directory' "$TMP/kimi-race.log" || {
  echo "Kimi installer did not report late destination ownership drift" >&2; exit 1; }

# Swap the destination inside the first preserve mv, after revalidation has returned. Validation
# of the displaced object must catch the foreign tree and restore it without deletion.
post_race_home="$TMP/kimi-race-before-displacement"
KIMI_CODE_HOME="$post_race_home" "$INSTALL" >/dev/null || exit 1
post_race_bin="$TMP/kimi-race-mv-bin"
mkdir -p "$post_race_bin"
real_mv="$(command -v mv)"
cat > "$post_race_bin/mv" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "$RACE_HOME/skills/magi" ] && [ ! -e "$RACE_SENTINEL" ]; then
  rm -rf "$1"
  mkdir -p "$1"
  printf 'foreign-at-displacement\n' > "$1/SKILL.md"
  : > "$RACE_SENTINEL"
fi
exec "$REAL_MV" "$@"
STUB
chmod +x "$post_race_bin/mv"
PATH="$post_race_bin:$PATH" REAL_MV="$real_mv" RACE_HOME="$post_race_home" \
  RACE_SENTINEL="$TMP/kimi-post-race-swapped" KIMI_CODE_HOME="$post_race_home" \
  "$INSTALL" >"$TMP/kimi-post-race.log" 2>&1 && {
  echo "Kimi installer accepted a foreign object swapped at displacement" >&2; exit 1; }
grep -qx 'foreign-at-displacement' "$post_race_home/skills/magi/SKILL.md" || {
  echo "Kimi installer deleted the foreign object displaced after validation" >&2; exit 1; }

# Create a foreign destination at the no-replace publication call. The stage must not be moved
# beneath it, and the displaced owned tree must remain preserved for recovery.
publish_race_home="$TMP/kimi-race-before-publication"
KIMI_CODE_HOME="$publish_race_home" "$INSTALL" >/dev/null || exit 1
publish_race_bin="$TMP/kimi-race-python-bin"
mkdir -p "$publish_race_bin"
real_python="$(command -v python3)"
rename_helper="$(readlink -f "$HERE/../../harness-magi-codex/scripts/magi_rename_noreplace.py")"
cat > "$publish_race_bin/python3" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "$RENAME_HELPER" ] && [ "$3" = "$RACE_HOME/skills/magi" ] \
    && [ ! -e "$RACE_SENTINEL" ]; then
  mkdir -p "$3"
  printf 'foreign-before-publish\n' > "$3/SKILL.md"
  : > "$RACE_SENTINEL"
fi
exec "$REAL_PYTHON" "$@"
STUB
chmod +x "$publish_race_bin/python3"
PATH="$publish_race_bin:$PATH" REAL_PYTHON="$real_python" RENAME_HELPER="$rename_helper" \
  RACE_HOME="$publish_race_home" RACE_SENTINEL="$TMP/kimi-publish-race" \
  KIMI_CODE_HOME="$publish_race_home" "$INSTALL" >"$TMP/kimi-publish-race.log" 2>&1 && {
  echo "Kimi installer accepted a foreign destination created before publication" >&2; exit 1; }
grep -qx 'foreign-before-publish' "$publish_race_home/skills/magi/SKILL.md" || {
  echo "Kimi installer replaced the late foreign publication destination" >&2; exit 1; }
[ ! -e "$publish_race_home/skills/magi/.harness-kimi-skill" ] || {
  echo "Kimi installer nested its stage into the late foreign directory" >&2; exit 1; }
find "$publish_race_home/skills" -maxdepth 1 -type d -name '.magi.old.*' | grep -q . || {
  echo "Kimi installer did not preserve the displaced owned predecessor" >&2; exit 1; }

# A second-skill publication failure must restore the already-published first skill exactly.
transaction_home="$TMP/kimi-transaction-second-publish"
KIMI_CODE_HOME="$transaction_home" "$INSTALL" >/dev/null || exit 1
printf 'local-predecessor-byte\n' >> "$transaction_home/skills/magi/SKILL.md"
transaction_before="$(
  find "$transaction_home/skills/magi" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
)"
transaction_bin="$TMP/kimi-transaction-python-bin"
mkdir -p "$transaction_bin"
cat > "$transaction_bin/python3" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "$RENAME_HELPER" ] \
    && [ "$3" = "$TRANSACTION_HOME/skills/bug-hunt" ] \
    && [ ! -e "$TRANSACTION_SENTINEL" ]; then
  mkdir -p "$3"
  printf 'foreign-second-publication\n' > "$3/SKILL.md"
  : > "$TRANSACTION_SENTINEL"
fi
exec "$REAL_PYTHON" "$@"
STUB
chmod +x "$transaction_bin/python3"
PATH="$transaction_bin:$PATH" REAL_PYTHON="$real_python" RENAME_HELPER="$rename_helper" \
  TRANSACTION_HOME="$transaction_home" \
  TRANSACTION_SENTINEL="$TMP/kimi-transaction-second" KIMI_CODE_HOME="$transaction_home" \
  "$INSTALL" >"$TMP/kimi-transaction.log" 2>&1 && {
  echo "Kimi installer accepted a foreign second-skill publication" >&2; exit 1; }
transaction_after="$(
  find "$transaction_home/skills/magi" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
)"
[ "$transaction_after" = "$transaction_before" ] || {
  echo "Kimi second-skill failure did not restore the first skill byte-for-byte" >&2; exit 1; }
grep -Fq '[harness-kimi] installed ' "$TMP/kimi-transaction.log" && {
  echo "failed Kimi transaction emitted committed-success output" >&2; exit 1; }
grep -qx 'foreign-second-publication' "$transaction_home/skills/bug-hunt/SKILL.md" || {
  echo "Kimi transaction rollback removed the late foreign second skill" >&2; exit 1; }

# Substitute the private quarantine immediately after marker validation. Neither the foreign
# replacement nor the displaced owned publication may be recursively removed.
rollback_race_home="$TMP/kimi-rollback-quarantine-race"
KIMI_CODE_HOME="$rollback_race_home" "$INSTALL" >/dev/null || exit 1
printf 'rollback-predecessor-byte\n' >> "$rollback_race_home/skills/magi/SKILL.md"
rollback_before="$(
  cd "$rollback_race_home/skills/magi" \
    && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
)"
rollback_race_bin="$TMP/kimi-rollback-race-bin"
mkdir -p "$rollback_race_bin"
cp "$transaction_bin/python3" "$rollback_race_bin/python3"
cat > "$rollback_race_bin/cmp" <<'STUB'
#!/usr/bin/env bash
"$REAL_CMP" "$@"
rc=$?
last="${!#}"
case "$last" in
  "$TRANSACTION_HOME"/skills/.magi.rollback.*/.harness-kimi-skill)
    if [ "$rc" -eq 0 ] && [ ! -e "$ROLLBACK_SENTINEL" ]; then
      quarantine="$(dirname "$last")"
      "$REAL_MV" "$quarantine" "${quarantine}.owned"
      mkdir -p "$quarantine"
      printf 'foreign-private-quarantine\n' > "$quarantine/SKILL.md"
      : > "$ROLLBACK_SENTINEL"
    fi ;;
esac
exit "$rc"
STUB
chmod +x "$rollback_race_bin/python3" "$rollback_race_bin/cmp"
real_cmp="$(command -v cmp)"
real_mv="$(command -v mv)"
PATH="$rollback_race_bin:$PATH" REAL_PYTHON="$real_python" REAL_CMP="$real_cmp" \
  REAL_MV="$real_mv" \
  RENAME_HELPER="$rename_helper" TRANSACTION_HOME="$rollback_race_home" \
  TRANSACTION_SENTINEL="$TMP/kimi-rollback-second" \
  ROLLBACK_SENTINEL="$TMP/kimi-rollback-swapped" KIMI_CODE_HOME="$rollback_race_home" \
  "$INSTALL" >"$TMP/kimi-rollback-race.log" 2>&1 && {
  echo "Kimi rollback race unexpectedly succeeded" >&2; exit 1; }
rollback_foreign="$(find "$rollback_race_home/skills" -maxdepth 1 -type d \
  -name '.magi.rollback.*' ! -name '*.owned' -print -quit)"
[ -n "$rollback_foreign" ] \
  && grep -qx 'foreign-private-quarantine' "$rollback_foreign/SKILL.md" || {
  echo "Kimi rollback deleted the substituted private quarantine" >&2; exit 1; }
rollback_owned="$(find "$rollback_race_home/skills" -maxdepth 1 -type d \
  -name '.magi.rollback.*.owned' -print -quit)"
[ -n "$rollback_owned" ] || {
  echo "Kimi rollback lost the displaced owned publication" >&2; exit 1; }
rollback_after="$(
  cd "$rollback_race_home/skills/magi" \
    && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
)"
[ "$rollback_after" = "$rollback_before" ] || {
  echo "Kimi rollback did not restore the predecessor byte-for-byte" >&2; exit 1; }

# A runtime link changed after the initial preflight must be preserved and refused by the final
# identity check; the installer never removes runtime entries.
runtime_race_home="$TMP/kimi-runtime-race"
KIMI_CODE_HOME="$runtime_race_home" "$INSTALL" >/dev/null || exit 1
runtime_skills_before="$(
  for skill in magi bug-hunt dual-magi-review ultramagi; do
    (
      cd "$runtime_race_home/skills/$skill" \
        && find . -type f -print0 | sort -z | xargs -0 sha256sum
    )
  done | sha256sum
)"
runtime_race_bin="$TMP/kimi-runtime-readlink-bin"
runtime_foreign="$TMP/kimi-runtime-foreign"
mkdir -p "$runtime_race_bin" "$runtime_foreign"
real_readlink="$(command -v readlink)"
cat > "$runtime_race_bin/readlink" <<'STUB'
#!/usr/bin/env bash
result="$("$REAL_READLINK" "$@")"
last="${!#}"
if [ "$last" = "$RACE_HOME/harness-magi-runtime" ]; then
  count=0
  [ ! -f "$RACE_COUNT" ] || read -r count < "$RACE_COUNT"
  count=$((count + 1))
  printf '%s\n' "$count" > "$RACE_COUNT"
  if [ "$count" -eq 3 ] && [ ! -e "$RACE_SENTINEL" ]; then
    rm -f "$last"
    ln -s "$RACE_FOREIGN" "$last"
    : > "$RACE_SENTINEL"
  fi
fi
printf '%s\n' "$result"
STUB
chmod +x "$runtime_race_bin/readlink"
PATH="$runtime_race_bin:$PATH" REAL_READLINK="$real_readlink" RACE_HOME="$runtime_race_home" \
  RACE_FOREIGN="$runtime_foreign" RACE_SENTINEL="$TMP/kimi-runtime-swapped" \
  RACE_COUNT="$TMP/kimi-runtime-readlink-count" \
  KIMI_CODE_HOME="$runtime_race_home" "$INSTALL" >"$TMP/kimi-runtime-race.log" 2>&1 && {
  echo "Kimi installer accepted a late foreign runtime link" >&2; exit 1; }
[ "$(readlink -f "$runtime_race_home/harness-magi-runtime")" = "$runtime_foreign" ] || {
  echo "Kimi installer replaced the late foreign runtime link" >&2; exit 1; }
runtime_skills_after="$(
  for skill in magi bug-hunt dual-magi-review ultramagi; do
    (
      cd "$runtime_race_home/skills/$skill" \
        && find . -type f -print0 | sort -z | xargs -0 sha256sum
    )
  done | sha256sum
)"
[ "$runtime_skills_after" = "$runtime_skills_before" ] || {
  echo "late runtime drift partially committed Kimi skill replacements" >&2; exit 1; }

# --- no-rsync fallback path: a PATH without rsync forces the mktemp/cp/mv branch ---
fallback_bin="$TMP/no-rsync-bin"
mkdir -p "$fallback_bin"
for tool in bash cmp cp diff dirname flock git grep ln mkdir mv mktemp python3 readlink rm; do
  ln -s "$(command -v "$tool")" "$fallback_bin/$tool" || {
    echo "cannot stage fallback tool: $tool" >&2; exit 1; }
done
PATH="$fallback_bin" KIMI_CODE_HOME="$TMP/kimi-fallback" bash "$INSTALL" \
  >"$TMP/fallback.log" 2>&1 || {
  echo "no-rsync fallback install failed" >&2; cat "$TMP/fallback.log" >&2; exit 1; }
for skill in magi bug-hunt dual-magi-review ultramagi; do
  [ -f "$TMP/kimi-fallback/skills/$skill/SKILL.md" ] || {
    echo "no-rsync fallback missing $skill" >&2; exit 1; }
  [ -f "$TMP/kimi-fallback/skills/$skill/.harness-kimi-skill" ] || {
    echo "no-rsync fallback left $skill unowned" >&2; exit 1; }
done
# Reinstall over an owned destination: exercises the preserve + publish replacement branch.
PATH="$fallback_bin" KIMI_CODE_HOME="$TMP/kimi-fallback" bash "$INSTALL" >/dev/null 2>&1 || {
  echo "no-rsync fallback reinstall over owned skills failed" >&2; exit 1; }

# --- rsync path must not expose a destination when staging fails ---
failrsync_bin="$TMP/fail-rsync-bin"
mkdir -p "$failrsync_bin"
for tool in bash cmp cp diff dirname flock git grep ln mkdir mv mktemp python3 readlink rm; do
  ln -s "$(command -v "$tool")" "$failrsync_bin/$tool" || {
    echo "cannot stage fail-rsync tool: $tool" >&2; exit 1; }
done
cat > "$failrsync_bin/rsync" <<'STUB'
#!/bin/bash
exit 23
STUB
chmod +x "$failrsync_bin/rsync"
PATH="$failrsync_bin" KIMI_CODE_HOME="$TMP/kimi-failrsync" bash "$INSTALL" \
  >"$TMP/failrsync.log" 2>&1 && {
  echo "installer reported success after staged rsync failed" >&2; exit 1; }
[ ! -e "$TMP/kimi-failrsync/skills/magi" ] || {
  echo "failed staged rsync exposed a partial magi destination" >&2; exit 1; }
find "$TMP/kimi-failrsync/skills" -maxdepth 1 -name '.magi.stage.*' | grep -q . && {
  echo "rsync failure leaked a stage directory" >&2; exit 1; }

# Failed stage cleanup must be explicit and name the residual path.
cleanup_bin="$TMP/cleanup-failure-bin"
mkdir -p "$cleanup_bin"
for tool in bash cmp cp diff dirname flock git grep ln mkdir mv mktemp python3 readlink; do
  ln -s "$(command -v "$tool")" "$cleanup_bin/$tool" || exit 1
done
cat > "$cleanup_bin/rsync" <<'STUB'
#!/usr/bin/env bash
exit 23
STUB
cat > "$cleanup_bin/rm" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *.stage.*) exit 1 ;;
esac
exec "$REAL_RM" "$@"
STUB
chmod +x "$cleanup_bin/rsync" "$cleanup_bin/rm"
PATH="$cleanup_bin" REAL_RM="$(command -v rm)" \
  KIMI_CODE_HOME="$TMP/kimi-cleanup-failure" bash "$INSTALL" \
  >"$TMP/cleanup-failure.log" 2>&1
cleanup_rc=$?
if [ "$cleanup_rc" -ne 0 ] \
    && grep -Fq 'cannot remove interrupted stage:' "$TMP/cleanup-failure.log" \
    && grep -Fq '.stage.' "$TMP/cleanup-failure.log" \
    && grep -q 'interrupted Kimi skill cleanup was incomplete' "$TMP/cleanup-failure.log"; then
  ok_cleanup=1
else
  echo "Kimi installer did not expose interrupted stage cleanup failure: $(tr '\n' ' ' < "$TMP/cleanup-failure.log")" >&2
  exit 1
fi

DUAL="$HERE/../skills/dual-magi-review/SKILL.md"
ULTRA="$HERE/../skills/ultramagi/SKILL.md"
grep -Fq 'fan-out 1 → cross-family 2 → fan-out 3 → cross-family 4' "$DUAL" || {
  echo "dual-magi round transition contract missing" >&2; exit 1; }
grep -Fq 'synthesize the cross-family round with' "$DUAL" || {
  echo "dual-magi cross-family synthesis contract missing" >&2; exit 1; }
grep -Fq 'magi_synthesize.py' "$DUAL" || {
  echo "dual-magi deterministic synthesis helper missing" >&2; exit 1; }
grep -Fq 'magi_plateau_gate.sh' "$ULTRA" || {
  echo "ultramagi implementation gate missing" >&2; exit 1; }
grep -Fq 'run_checked()' "$DUAL" \
  && grep -Fq 'status=$?' "$DUAL" \
  && grep -Fq "printf 'MAGI_PHASE_FAILED phase=%s status=%s command:'" "$DUAL" \
  && grep -Fq "printf ' %q' \"\$@\"" "$DUAL" \
  && grep -Fq 'do not start the next phase or implementation' "$DUAL" || {
  echo "Kimi dual-magi checked command/status handoff pattern missing" >&2; exit 1; }
echo "test_kimi_skills_install: PASS"
